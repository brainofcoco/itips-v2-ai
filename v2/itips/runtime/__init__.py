"""Process lifecycle: orchestrator + per-camera workers + shared frame bus."""

from .cuda_guard import verify_cuda
from .frame_bus import FrameBus
from .orchestrator import Orchestrator

__all__ = ["FrameBus", "Orchestrator", "verify_cuda"]
