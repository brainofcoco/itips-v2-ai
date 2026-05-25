"""Event-driven face recognition for cameras without native faceRecognitionServer.

Built on InsightFace's `buffalo_l` pack — SCRFD detector + ArcFace
encoder, ~300 MB of ONNX weights auto-downloaded to `~/.insightface/`
on first run.

Two ways into this module:

  enroll(person_id, full_name, image_bytes)
      Called from the dashboard's Add Worker flow. Detects the face,
      encodes it, writes the L2-normalised 512-d embedding to the
      `EmbeddingStore`.

  recognize(frame, bbox=None)
      Called from `event_worker._handle_face_detection` when the
      capability router says this camera lacks native FR. If `bbox`
      is supplied, we trust the camera's detection and just encode
      that crop; otherwise SCRFD re-detects and picks the largest
      face. Returns the best match (or unknown) against the store.

Design constraints — read before changing anything:

  * **No module-load side effects.** `insightface` is imported only
    inside `_ensure_model()`. Importing this module from tests or from
    a v2 deploy without the ML extra is free.
  * **Singleton model.** One `FaceAnalysis` instance per process. CUDA
    on Jetson, CPU fallback elsewhere. ~300 MB GPU residency once
    warm; never reloaded.
  * **Event-driven only.** Nothing here opens a video stream. Frames
    arrive via the existing Dahua event multipart path.
  * **Cheap unless used.** `warmup_async()` spawns one background
    thread; if nothing ever calls `recognize()` the cost is zero
    beyond the import. If the first event lands before warmup
    finishes, the handler blocks until it does.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from itips.ml.embedding_store import EmbeddingRecord, EmbeddingStore

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class FaceEngineUnavailable(RuntimeError):
    """Raised when `insightface` / `onnxruntime` aren't installed.

    The event worker catches this and falls back to the existing
    bare-bbox alert path so the camera still drives the incident
    lifecycle. The dashboard catches it to surface a hint to the
    operator that ML extras need to be installed.
    """


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
        """Load the model in a background thread; safe to call repeatedly."""
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
            logger.exception("FaceEngine warmup failed")

    def _ensure_model(self):
        # Fast path — already warm.
        if self._app is not None:
            return self._app
        with self._init_lock:
            if self._app is not None:
                return self._app
            try:
                from insightface.app import FaceAnalysis  # type: ignore
            except ImportError as exc:
                raise FaceEngineUnavailable(
                    "insightface is not installed. `pip install itips-ai[ml]` "
                    "or disable the face fallback in settings."
                ) from exc
            logger.info(
                "FaceEngine: loading buffalo_l (providers=%s det_size=%s)",
                self._providers, self._det_size,
            )
            app = FaceAnalysis(name="buffalo_l", providers=self._providers)
            app.prepare(ctx_id=0, det_size=self._det_size)
            self._app = app
            self._ready.set()
            logger.info("FaceEngine ready")
        return self._app

    # ─── enrolment ────────────────────────────────────────────────────

    def enroll(
        self, *, person_id: str, full_name: str, image_bytes: bytes,
    ) -> EmbeddingRecord:
        """Detect → encode → store. Returns the stored record.

        Raises `ValueError` if no face is found in the image.
        """
        app = self._ensure_model()
        frame = _decode_jpeg(image_bytes)
        faces = app.get(frame)
        if not faces:
            raise ValueError("no face found in enrolment image")
        # Take the largest face — the operator is enrolling a worker,
        # so we want the dominant subject not a bystander.
        face = max(faces, key=_face_area)
        embedding = face.normed_embedding
        self._store.upsert(
            person_id=person_id, full_name=full_name, embedding=embedding,
        )
        logger.info("FaceEngine enrolled person_id=%s (%s)", person_id, full_name)
        rec = self._store.get(person_id)
        assert rec is not None  # we just wrote it
        return rec

    def remove(self, person_id: str) -> bool:
        return self._store.delete(person_id)

    # ─── recognition ──────────────────────────────────────────────────

    def recognize(
        self,
        frame: "np.ndarray",
        bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> RecognitionResult:
        """Identify the face in `frame` (or its `bbox` crop).

        `bbox` is `(x1, y1, x2, y2)` in pixels. Pass it when the
        camera already detected the face — saves SCRFD a pass.
        """
        app = self._ensure_model()
        crop = _crop_for_bbox(frame, bbox) if bbox else frame
        faces = app.get(crop)
        if not faces:
            # Fall through: no face inside the crop. Caller will treat
            # the original bbox event as "presence without identity".
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
        # All store embeddings are L2-normalised on write. We just
        # need to normalise the query and dot-product against each.
        q = embedding.astype("float32", copy=False)
        qn = float((q @ q) ** 0.5)
        if qn > 0:
            q = q / qn
        sims = np.array([float(q @ r.embedding) for r in records], dtype="float32")
        idx = int(sims.argmax())
        return records[idx].person_id, records[idx].full_name, float(sims[idx])


# ─── helpers ─────────────────────────────────────────────────────────


def _decode_jpeg(image_bytes: bytes):
    import cv2
    import numpy as np
    buf = np.frombuffer(image_bytes, dtype="uint8")
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("could not decode image bytes as JPEG/PNG")
    return frame


def _face_area(face) -> float:
    bbox = getattr(face, "bbox", None)
    if bbox is None:
        return 0.0
    x1, y1, x2, y2 = bbox
    return max(0.0, float(x2 - x1)) * max(0.0, float(y2 - y1))


def _crop_for_bbox(frame, bbox):
    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    # Expand by 20% so InsightFace's landmarker has room around the
    # camera's tight Dahua bbox. Clamp to frame edges.
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    pad_x, pad_y = bw * 0.2, bh * 0.2
    xx1 = int(max(0, x1 - pad_x))
    yy1 = int(max(0, y1 - pad_y))
    xx2 = int(min(w, x2 + pad_x))
    yy2 = int(min(h, y2 + pad_y))
    if xx2 <= xx1 or yy2 <= yy1:
        return frame
    return frame[yy1:yy2, xx1:xx2]
