# syntax=docker/dockerfile:1.7
#
# ITIPS V2.1 — Dahua-native edge node.
#
# Multi-arch, no ML deps. Same image builds on a Mac for local validation
# against real cameras and on a Jetson for production deployment.
#
# Why so small? The pipeline runs zero inference on this box now — the
# Dahua cameras do face recognition, ANPR, IVS rules, fire/smoke, and
# active deterrence on their own silicon. The Jetson/container is a
# message broker + evidence assembler.

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs cv2 needs at import time (libGL etc.). Nothing GPU.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
        curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer 1 — pip deps. Pinned in pyproject.toml; kept here as an explicit
# install so the layer caches independently of the app code.
RUN pip install --no-cache-dir \
        "numpy<2" \
        flask==3.0.3 werkzeug==3.0.3 \
        python-dotenv==1.0.1 \
        requests==2.32.3 \
        pydantic==2.8.2 \
        pyyaml==6.0.2 \
        opencv-python-headless==4.10.0.84 \
        pillow==10.4.0

# Layer 2 — app code.
COPY pyproject.toml ./
COPY itips/ ./itips/
COPY config/ ./config/
COPY openapi/ ./openapi/

# Drop privileges. /opt/itips/{var,evidence_store} are bind-mounted in by
# docker-compose; we just prepare the mount points and chown them.
RUN useradd --uid 1000 --create-home --shell /bin/bash itips \
    && mkdir -p /opt/itips/var /opt/itips/evidence_store \
    && chown -R itips:itips /opt/itips /app
USER itips

EXPOSE 5050 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5050/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "itips"]
