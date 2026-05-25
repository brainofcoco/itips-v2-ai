"""Alert engine + PTZ controller — the side-effect surface of the AI pipeline.

All outbound effects route through the intake queue. Local side effects
(PTZ commands) stay on-Jetson.
"""

from .engine import AlertEngine
from .ptz import PTZController

__all__ = ["AlertEngine", "PTZController"]
