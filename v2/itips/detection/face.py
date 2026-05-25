"""InsightFace SCRFD + ArcFace.

The V1 logic is kept (per-track plausibility check, intruder labelling,
encrypted local cache) but the surface is narrower. The cloud-generated
embedding flow (PRD §4.5 REQ-PDB-02) is honoured by `apply_personnel_sync`,
which accepts embeddings from the B1 endpoint without re-computing them.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FaceResult:
    name: str          # registered name | 'person' | 'INTRUDER'
    confidence: float
    bbox: tuple[float, float, float, float]
    is_known: bool


class FaceRecognitionEngine:
    """Detects and recognises faces against an in-memory embedding cache.

    The cache is keyed by `person_id`. Embeddings can arrive from two
    places: the local enrolment endpoint (POC convenience) and the B1
    personnel sync push (the production path).
    """

    def __init__(
        self,
        *,
        model_pack: str = "buffalo_l",
        det_size: int = 640,
        match_threshold: float = 0.6,
        margin_threshold: float = 0.05,
    ) -> None:
        from insightface.app import FaceAnalysis

        self._app = FaceAnalysis(name=model_pack, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        self._app.prepare(ctx_id=0, det_size=(det_size, det_size))
        self._match_threshold = match_threshold
        self._margin_threshold = margin_threshold
        self._cache: dict[str, dict[str, Any]] = {}   # person_id → {name, embeddings: [np.ndarray]}
        self._cache_lock = threading.Lock()
        logger.info("InsightFace ready — pack=%s, det=%dx%d, match≥%.2f",
                    model_pack, det_size, det_size, match_threshold)

    # ─── personnel cache ───────────────────────────────────────────

    def apply_personnel_sync(self, payload: dict[str, Any]) -> None:
        action = payload.get("action")
        person_id = str(payload["person_id"])
        with self._cache_lock:
            if action == "deactivate":
                self._cache.pop(person_id, None)
                return
            embeddings = [np.asarray(e, dtype=np.float32) for e in payload.get("embeddings", [])]
            self._cache[person_id] = {
                "name": payload.get("full_name", person_id),
                "embeddings": embeddings,
            }

    def loaded_people(self) -> list[str]:
        with self._cache_lock:
            return list(self._cache.keys())

    # ─── inference ─────────────────────────────────────────────────

    def recognize(self, frame: np.ndarray, *, detections, camera_id: int) -> list[FaceResult]:
        if frame is None or frame.size == 0:
            return []
        faces = self._app.get(frame)
        if not faces:
            return []
        results: list[FaceResult] = []
        for face in faces:
            embedding = face.normed_embedding
            name, confidence, is_known = self._best_match(embedding)
            results.append(FaceResult(
                name=name,
                confidence=confidence,
                bbox=tuple(map(float, face.bbox)),
                is_known=is_known,
            ))
        return results

    def _best_match(self, embedding: np.ndarray) -> tuple[str, float, bool]:
        with self._cache_lock:
            entries = list(self._cache.items())
        if not entries:
            return ("person", 0.0, False)
        best_name, best_score = "person", -1.0
        for person_id, record in entries:
            for cached in record["embeddings"]:
                score = float(np.dot(embedding, cached))
                if score > best_score:
                    best_score = score
                    best_name = record["name"]
        if best_score >= self._match_threshold:
            return (best_name, best_score, True)
        return ("person", float(max(0.0, best_score)), False)
