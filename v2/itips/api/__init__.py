"""HTTP surfaces: public 5050 (live view + ops) and inbound 8443 (backend push).

Imports are lazy so tests for the route handlers can import this package
without dragging in heavy dependencies (cv2, ultralytics, insightface).
"""

from __future__ import annotations

__all__ = ["InboundApiServer", "PublicApiServer"]


def __getattr__(name: str):
    if name == "InboundApiServer":
        from .inbound import InboundApiServer

        return InboundApiServer
    if name == "PublicApiServer":
        from .public import PublicApiServer

        return PublicApiServer
    raise AttributeError(name)
