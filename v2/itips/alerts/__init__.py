"""Alert engine — the side-effect surface of the Dahua event pipeline.

All outbound effects route through the intake queue. Local side effects
(deterrence, PTZ) live in `itips.camera`.
"""

from .engine import AlertEngine

__all__ = ["AlertEngine"]
