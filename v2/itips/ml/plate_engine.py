"""Event-driven license-plate OCR for cameras without native ANPR.

Mirrors the architecture of `face_engine.py`:

  * No module-load side effects — `easyocr` and `torch` are imported
    only inside `_ensure_model()`. The v2 baseline runs fine without
    them.
  * Singleton reader per process. ~150 MB GPU on CUDA; CPU fallback
    works but is ~10× slower per call.
  * Event-driven only. Frames arrive via the existing Dahua event
    multipart path; we never open a video stream.

Triggers (wired in `event_worker.py`):

  * `CarDrivingInOut` — strong signal that the camera just saw a
    vehicle at the gate. Always run if the camera lacks native ANPR.
  * `VideoMotion`     — weak signal. Run only with a per-camera
    cooldown so a tree in the wind can't pin the GPU.

The result, when read, is routed through the same
`AlertEngine.handle_plate_capture` handler that the native
`TrafficCarMeasurement` path uses — so downstream behaviour (gate-open
logic, evidence record, allow/deny list check) is identical.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class PlateEngineUnavailable(RuntimeError):
    """Raised when `easyocr` / `torch` aren't installed.

    The event worker catches this and degrades to the existing
    motion-only / vehicle-gate alert (still drives the lifecycle).
    """


# Permissive international plate filter — 5 to 10 alphanumeric chars,
# at least 2 letters AND at least 2 digits, no all-letters and no
# all-digits. Catches Nigerian "LAG-123-XY", US "ABC1234", EU "BMW 1234".
_PLATE_CHARSET = re.compile(r"[A-Z0-9]")
_PLATE_LEN_RANGE = (5, 10)


@dataclass
class PlateReadResult:
    plate_number: str               # cleaned, uppercase, no separators
    confidence: float               # OCR confidence in [0, 1]
    bbox: tuple[float, float, float, float]  # plate region within frame
    raw_text: str                   # what OCR emitted before filtering

    @property
    def display(self) -> str:
        return self.plate_number


class PlateEngine:
    """EasyOCR-backed plate OCR with a Nigerian-plate-aware filter."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.5,
        gpu: bool = True,
        languages: Optional[list[str]] = None,
    ) -> None:
        self._min_confidence = float(min_confidence)
        self._gpu = bool(gpu)
        # English alphabet covers Latin plates in NG/EU/US. Adding more
        # languages bloats the model with no plate-OCR benefit.
        self._languages = languages or ["en"]
        self._reader = None
        self._init_lock = threading.Lock()
        self._warmup_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[Exception] = None

    # ─── lifecycle ────────────────────────────────────────────────────

    def warmup_async(self) -> None:
        with self._init_lock:
            if self._reader is not None or self._warmup_thread is not None:
                return
            t = threading.Thread(
                target=self._ensure_model_safe, name="plate-engine-warmup", daemon=True,
            )
            self._warmup_thread = t
            t.start()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def _ensure_model_safe(self) -> None:
        try:
            self._ensure_model()
        except Exception as exc:  # noqa: BLE001
            with self._init_lock:
                self._init_error = exc
            logger.exception("PlateEngine warmup failed")

    def _ensure_model(self):
        if self._reader is not None:
            return self._reader
        with self._init_lock:
            if self._reader is not None:
                return self._reader
            try:
                import easyocr  # type: ignore
            except ImportError as exc:
                raise PlateEngineUnavailable(
                    "easyocr is not installed. `pip install itips-ai[ml]` "
                    "or disable the plate fallback in settings."
                ) from exc
            logger.info(
                "PlateEngine: loading EasyOCR reader (lang=%s gpu=%s)",
                self._languages, self._gpu,
            )
            reader = easyocr.Reader(self._languages, gpu=self._gpu)
            self._reader = reader
            self._ready.set()
            logger.info("PlateEngine ready")
        return self._reader

    # ─── reading ──────────────────────────────────────────────────────

    def read_plate(
        self,
        frame: "np.ndarray",
        vehicle_bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[PlateReadResult]:
        """Find the most plate-like text in `frame` (or its `vehicle_bbox`).

        Returns `None` if no plate-like string crosses the confidence
        and pattern bars. The result's `bbox` is in frame coordinates,
        not crop coordinates — callers can hand it straight to the
        recorder for evidence overlay.
        """
        reader = self._ensure_model()
        crop, (off_x, off_y) = _crop_for_vehicle(frame, vehicle_bbox)
        # `detail=1` returns [bbox_points, text, confidence] tuples.
        # `paragraph=False` keeps each text region separate so we can
        # rank candidates individually instead of EasyOCR mashing them.
        raw = reader.readtext(crop, detail=1, paragraph=False)

        best: Optional[PlateReadResult] = None
        for item in raw:
            text, conf, points = _unpack_readtext_row(item)
            if text is None:
                continue
            cleaned = _clean_plate_text(text)
            if not _looks_like_plate(cleaned):
                continue
            if conf < self._min_confidence:
                continue
            bbox_frame = _points_to_bbox(points, off_x, off_y)
            candidate = PlateReadResult(
                plate_number=cleaned,
                confidence=conf,
                bbox=bbox_frame,
                raw_text=text,
            )
            if best is None or conf > best.confidence:
                best = candidate
        return best


# ─── helpers ─────────────────────────────────────────────────────────


def _clean_plate_text(text: str) -> str:
    """Uppercase, strip everything that isn't [A-Z0-9]."""
    return "".join(c for c in text.upper() if _PLATE_CHARSET.match(c))


def _looks_like_plate(cleaned: str) -> bool:
    """Permissive plate-pattern check.

    Length 5–10, at least 2 letters AND at least 2 digits. Rejects
    pure numbers ('123456' — likely a price tag) and pure letters
    ('STOPSIGN' — likely a road sign).
    """
    n = len(cleaned)
    if n < _PLATE_LEN_RANGE[0] or n > _PLATE_LEN_RANGE[1]:
        return False
    letters = sum(1 for c in cleaned if c.isalpha())
    digits = sum(1 for c in cleaned if c.isdigit())
    return letters >= 2 and digits >= 2


def _crop_for_vehicle(
    frame: "np.ndarray",
    bbox: Optional[tuple[float, float, float, float]],
) -> tuple["np.ndarray", tuple[int, int]]:
    """Optionally crop to the vehicle bbox; return (crop, (off_x, off_y))."""
    if bbox is None:
        return frame, (0, 0)
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    xx1 = int(max(0, x1))
    yy1 = int(max(0, y1))
    xx2 = int(min(w, x2))
    yy2 = int(min(h, y2))
    if xx2 <= xx1 or yy2 <= yy1:
        return frame, (0, 0)
    return frame[yy1:yy2, xx1:xx2], (xx1, yy1)


def _unpack_readtext_row(row):
    """EasyOCR's `detail=1` shape is `[points, text, conf]`."""
    try:
        points, text, conf = row
    except (TypeError, ValueError):
        return None, 0.0, None
    return str(text), float(conf), points


def _points_to_bbox(points, off_x: int, off_y: int) -> tuple[float, float, float, float]:
    """EasyOCR returns 4 corner points; convert to (x1, y1, x2, y2)."""
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (
        min(xs) + off_x, min(ys) + off_y,
        max(xs) + off_x, max(ys) + off_y,
    )
