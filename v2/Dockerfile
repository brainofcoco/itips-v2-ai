# ITIPS V2.1 — Dahua-native edge node, with Jetson-side ML fallback layer.
#
# Same image builds on a Mac for local validation against real cameras
# and on a Jetson for production deployment. Two layers:
#
#   1. Core runtime — Flask, OpenCV, requests, etc. (~150 MB)
#   2. ML fallback layer — InsightFace, EasyOCR, YOLOv8, ONNX Runtime
#      (~3 GB). Engages only when the dahua_health probe reports a
#      camera lacks the native capability (face DB, ANPR, IVS rules).
#
# Jetson swap (for GPU-accelerated inference on JetPack):
#
#   * Base image       → nvcr.io/nvidia/l4t-pytorch:r36.2.0-pth2.2-py3
#                         (or whatever matches your JetPack release)
#   * onnxruntime      → onnxruntime-gpu wheel built for L4T
#   * torch / torchvision (pulled by easyocr + ultralytics) → NVIDIA's
#                         L4T wheels, NOT the generic PyPI build
#
# The Python entry points and code paths are identical — only the
# native libs swap. See `docker-compose.yml` for the volume mount that
# persists model caches across container restarts so first-run
# downloads don't repeat.

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs cv2 + torch need at import time. libgomp1 is for OpenMP
# threading inside ONNX / Torch; libstdc++ comes with the base.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
        curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Layer 1: core runtime ────────────────────────────────────────────
# Pinned in pyproject.toml; kept here as an explicit install so the
# layer caches independently of the app code. opencv-python-headless
# instead of opencv-python so we don't pull GTK into the container.
RUN pip install --no-cache-dir \
        "numpy<2" \
        flask==3.0.3 werkzeug==3.0.3 \
        python-dotenv==1.0.1 \
        requests==2.32.3 \
        pydantic==2.8.2 \
        pyyaml==6.0.2 \
        opencv-python-headless==4.10.0.84 \
        pillow==10.4.0

# ─── Layer 2: ML fallback layer ───────────────────────────────────────
# easyocr + ultralytics pull torch + torchvision transitively. By
# default PyPI's torch wheel drags 3+ GB of CUDA toolkit wheels (cuDNN,
# NCCL, cuBLAS …) that don't work on Mac (no GPU) and don't match the
# Jetson's JetPack CUDA version anyway. Pin to the **CPU-only** index
# so the image stays in the ~1 GB range for Mac dev.
#
# Jetson swap: replace this entire RUN with the JetPack-matched
# `torch`, `torchvision`, and `onnxruntime-gpu` wheels published by
# NVIDIA for the appropriate L4T release.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.4.1+cpu" \
        "torchvision==0.19.1+cpu" \
 && pip install --no-cache-dir \
        "insightface==0.7.3" \
        "onnxruntime>=1.16,<2" \
        "easyocr>=1.7,<2" \
        "ultralytics>=8.2,<9"

# ─── Layer 3: app code ────────────────────────────────────────────────
COPY pyproject.toml ./
COPY itips/ ./itips/
COPY config/ ./config/
COPY openapi/ ./openapi/

# ─── Model caches ─────────────────────────────────────────────────────
# Each ML lib downloads its weights to a hard-coded $HOME path on first
# use. Redirect those to /opt/itips/models so a single docker-compose
# volume mount keeps every cache alive across container restarts —
# critical on the Jetson where the PRD mandates offline-by-design.
ENV INSIGHTFACE_HOME=/opt/itips/models/insightface \
    EASYOCR_MODULE_PATH=/opt/itips/models/easyocr \
    YOLO_CONFIG_DIR=/opt/itips/models/yolo \
    TORCH_HOME=/opt/itips/models/torch \
    HF_HOME=/opt/itips/models/huggingface

# Drop privileges. /opt/itips/{var,evidence_store,models} are
# bind-mounted in by docker-compose; we just prepare the mount points
# and chown them.
RUN useradd --uid 1000 --create-home --shell /bin/bash itips \
    && mkdir -p /opt/itips/var \
                /opt/itips/evidence_store \
                /opt/itips/models/insightface \
                /opt/itips/models/easyocr \
                /opt/itips/models/yolo \
                /opt/itips/models/torch \
                /opt/itips/models/huggingface \
    && chown -R itips:itips /opt/itips /app
USER itips

EXPOSE 5050 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5050/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "itips"]
