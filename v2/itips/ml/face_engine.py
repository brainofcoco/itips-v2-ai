"""InsightFace (SCRFD + ArcFace) wrapped for event-driven recognition.

Lazy-loaded so the v2 baseline doesn't pay for insightface on import.
Used as the fallback when a camera lacks native faceRecognitionServer
or the operator has forced the Jetson FR path via the override map.
"""

from __future__ import annotations

import logging
import threading
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from itips.ml.embedding_store import EmbeddingRecord, EmbeddingStore

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# InsightFace 0.7.3 uses scikit-image's `SimilarityTransform.estimate`,
# which scikit-image 0.26 deprecates with a FutureWarning emitted on
# every face-alignment call. The replacement (`from_estimate`) doesn't
# exist in the pinned `insightface==0.7.3`, so we can't fix the call
# upstream without a fork. Silence the one warning class precisely so
# container logs stay readable; real warnings still surface.
warnings.filterwarnings(
    "ignore",
    message=r".*estimate.*is deprecated since version 0\.26.*",
    category=FutureWarning,
)


class FaceEngineUnavailable(RuntimeError):
    """Raised when `insightface` / `onnxruntime` aren't installed.
    Callers catch and degrade to bare-bbox / no-validation paths."""


@dataclass
class RecognitionResult:
    matched: bool
    person_id: Optional[str]
    full_name: Optional[str]
    similarity: float                  # cosine in [-1, 1]
    embedding: Optional["np.ndarray"]  # always returned (for caller debugging)

    @property
    def display_name(self) -> str:
        if self.matched and self.full_name:
            return self.full_name
        return "INTRUDER"


class FaceEngine:
    """InsightFace SCRFD+ArcFace wrapped for event-driven recognition."""

    def __init__(
        self,
        embedding_store: EmbeddingStore,
        *,
        similarity_threshold: float = 0.35,
        det_size: tuple[int, int] = (640, 640),
        providers: Optional[list[str]] = None,
    ) -> None:
        self._store = embedding_store
        self._threshold = float(similarity_threshold)
        self._det_size = det_size
        # CUDAExecutionProvider first; ONNX runtime will silently
        # fall back to CPU if CUDA isn't available on the host.
        self._providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._app = None
        self._init_lock = threading.Lock()
        self._warmup_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[Exception] = None

    # ─── lifecycle ────────────────────────────────────────────────────

    def warmup_async(self) -> None:
        with self._init_lock:
            if self._app is not None or self._warmup_thread is not None:
                return
            t = threading.Thread(
                target=self._ensure_model_safe, name="face-engine-warmup", daemon=True,
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
                # Reset so warmup can be retried — a transient import
                # failure at boot must not disable the engine for good.
                self._warmup_thread = None
            logger.exception("FaceEngine warmup failed (will retry on next request)")

    def _ensure_model(self):
        if self._app is not None:
            return self._app
        with self._init_lock:
            if self._app is not None:
                return self._app
            try:
                from insightface.app import FaceAnalysis  # type: ignore
            except ImportError as exc:
                raise FaceEngineUnavailable(
                    "insightface is not installed. `pip install itips-ai[ml]`."
                ) from exc
            # Override insightface's `~/.insightface/` default so weights
            # land in the persistent NVMe cache, not the container ephemera.
            import os
            root = os.environ.get("INSIGHTFACE_HOME") or None
            logger.info("FaceEngine: loading buffalo_l (providers=%s det_size=%s root=%s)",
                        self._providers, self._det_size, root or "<default>")
            kwargs = {"name": "buffalo_l", "providers": self._providers}
            if root:
                kwargs["root"] = root
            app = FaceAnalysis(**kwargs)
            app.prepare(ctx_id=0, det_size=self._det_size)
            self._app = app
            self._ready.set()
            logger.info("FaceEngine ready")
        return self._app

    # ─── enrolment ────────────────────────────────────────────────────

    def enroll(
        self, *, person_id: str, full_name: str, image_bytes: bytes,
    ) -> EmbeddingRecord:
        """Raises `ValueError` if the image can't be decoded or has no face."""
        app = self._ensure_model()
        frame = _decode_image(image_bytes)
        faces = app.get(frame)
        if not faces:
            raise ValueError("no face found in enrolment image")
        # Pick the largest face — operator is enrolling a worker, not bystanders.
        face = max(faces, key=_face_area)
        self._store.upsert(
            person_id=person_id, full_name=full_name, embedding=face.normed_embedding,
        )
        logger.info("FaceEngine enrolled person_id=%s (%s)", person_id, full_name)
        rec = self._store.get(person_id)
        assert rec is not None
        return rec

    def remove(self, person_id: str) -> bool:
        return self._store.delete(person_id)

    def recognize(
        self,
        frame: "np.ndarray",
        bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> RecognitionResult:
        """`bbox=(x1,y1,x2,y2)` skips SCRFD when the camera already detected."""
        app = self._ensure_model()
        crop = _crop_for_bbox(frame, bbox) if bbox else frame
        faces = app.get(crop)
        if not faces:
            return RecognitionResult(
                matched=False, person_id=None, full_name=None,
                similarity=0.0, embedding=None,
            )
        face = max(faces, key=_face_area)
        embedding = face.normed_embedding
        match = self._best_match(embedding)
        if match is None:
            return RecognitionResult(
                matched=False, person_id=None, full_name=None,
                similarity=0.0, embedding=embedding,
            )
        person_id, full_name, sim = match
        return RecognitionResult(
            matched=sim >= self._threshold,
            person_id=person_id if sim >= self._threshold else None,
            full_name=full_name if sim >= self._threshold else None,
            similarity=sim,
            embedding=embedding,
        )

    def _best_match(self, embedding: "np.ndarray"):
        import numpy as np
        records = self._store.list_all()
        if not records:
            return None
        # Store embeddings are L2-normalised on write — same here for query.
        q = embedding.astype("float32", copy=False)
        qn = float((q @ q) ** 0.5)
        if qn > 0:
            q = q / qn
        sims = np.array([float(q @ r.embedding) for r in records], dtype="float32")
        idx = int(sims.argmax())
        return records[idx].person_id, records[idx].full_name, float(sims[idx])


# ─── helpers ─────────────────────────────────────────────────────────


def _decode_image(image_bytes: bytes):
    """Decode an operator-uploaded enrolment image to a BGR ndarray.

    cv2 first; on failure fall back to Pillow, which decodes formats the
    headless cv2 build chokes on (WebP, CMYK JPEG, BMP, TIFF). Raw bytes
    only — no Dahua EOI trim here, since that would corrupt a binary
    format that happens to contain an 0xFFD9 byte pair.
    """
    import cv2
    import numpy as np
    buf = np.frombuffer(image_bytes, dtype="uint8")
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if frame is not None:
        return frame
    try:
        import io

        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception:
        pass
    raise ValueError(
        f"could not decode {_sniff_image_format(image_bytes)} image — "
        "upload a JPEG or PNG (HEIC/iPhone photos aren't supported; "
        "export as JPEG first)"
    )


def _sniff_image_format(blob: bytes) -> str:
    """Best-effort magic-byte sniff so the decode error names the format."""
    if blob[:3] == b"\xff\xd8\xff":
        return "a JPEG"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "a PNG"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "a WebP"
    if blob[4:8] == b"ftyp" and blob[8:12] in (
        b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"hevx",
    ):
        return "a HEIC"
    if blob[:2] == b"BM":
        return "a BMP"
    return "an unrecognized"


def _face_area(face) -> float:
    bbox = getattr(face, "bbox", None)
    if bbox is None:
        return 0.0
    x1, y1, x2, y2 = bbox
    return max(0.0, float(x2 - x1)) * max(0.0, float(y2 - y1))


def _crop_for_bbox(frame, bbox):
    # 20% padding gives the landmarker room around Dahua's tight bbox.
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    pad_x, pad_y = bw * 0.2, bh * 0.2
    xx1 = int(max(0, x1 - pad_x))
    yy1 = int(max(0, y1 - pad_y))
    xx2 = int(min(w, x2 + pad_x))
    yy2 = int(min(h, y2 + pad_y))
    if xx2 <= xx1 or yy2 <= yy1:
        return frame
    return frame[yy1:yy2, xx1:xx2]
