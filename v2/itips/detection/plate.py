"""Plate Recognizer Stream API client (PRD §5.6 REQ-AI-14).

Replaces V1's YOLO-detect-then-no-OCR placeholder. Calls the on-prem
Plate Recognizer container's REST API and returns the OCR result.

If the URL is unset, the client is `None` at construction and the
detection pipeline simply skips plate OCR. We never break the pipeline
because plate recognition isn't configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


@dataclass
class PlateResult:
    plate: str
    confidence: float
    box: tuple[int, int, int, int]


class PlateRecognizerClient:
    def __init__(self, *, url: str, token: str = "", timeout: float = 4.0) -> None:
        if not url:
            raise ValueError("PlateRecognizer URL is required")
        self._url = url.rstrip("/") + "/"
        self._timeout = timeout
        self._headers = {"Authorization": f"Token {token}"} if token else {}

    def read(self, jpeg_bytes: bytes) -> list[PlateResult]:
        try:
            response = requests.post(
                self._url,
                files={"upload": ("frame.jpg", jpeg_bytes, "image/jpeg")},
                headers=self._headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException:
            logger.exception("PlateRecognizer call failed")
            return []

        payload = response.json() if response.content else {}
        out: list[PlateResult] = []
        for r in payload.get("results", []):
            box = r.get("box", {})
            out.append(PlateResult(
                plate=r.get("plate", ""),
                confidence=float(r.get("score", 0.0)),
                box=(int(box.get("xmin", 0)), int(box.get("ymin", 0)),
                     int(box.get("xmax", 0)), int(box.get("ymax", 0))),
            ))
        return out
