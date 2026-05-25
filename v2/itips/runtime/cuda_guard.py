"""CUDA verification with a simulation escape hatch.

In production the Jetson must have a working GPU before the pipeline
starts. On a dev laptop or in CI we let `ITIPS_SIMULATE=true` skip the
check so the rest of the stack stays exercisable.
"""

from __future__ import annotations

import logging

from config.settings import settings

logger = logging.getLogger(__name__)


class CUDAUnavailable(RuntimeError):
    """Raised in prod when the GPU is missing."""


def verify_cuda() -> str:
    """Return the active inference device, or raise in prod without a GPU."""
    try:
        import torch  # local import — heavy and only needed at boot
    except ImportError:
        if settings.flags.simulate:
            logger.warning("Simulation mode: torch not installed; using cpu device label.")
            return "cpu"
        raise CUDAUnavailable("PyTorch is not installed.")

    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        logger.info("CUDA verified — GPU: %s", name)
        return "cuda:0"

    if settings.flags.simulate:
        logger.warning("Simulation mode: CUDA unavailable; using cpu device. Performance will be poor.")
        return "cpu"

    raise CUDAUnavailable("CUDA is not available — refusing to start in production mode.")
