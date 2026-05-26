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
# ffmpeg is the H.265 encoder for PRD §4.3 REQ-EV-01 video evidence.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
        curl ca-certificates tini \
        ffmpeg \
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
        pillow==10.4.0 \
        "reportlab>=4.0,<5"

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
# 2a. CPU-only torch + torchvision from pytorch.org's CPU index.
#     `--index-url` (not `--extra-index-url`) so pip ignores PyPI for
#     these two — keeps the resolver from accidentally pulling a
#     CUDA-bundled wheel. Versions left unpinned so the latest
#     mutually-compatible aarch64 / x86_64 +cpu pair is picked.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision

# 2b. The rest of the ML stack from PyPI. `insightface==0.7.3` ships
#     a source distribution (no aarch64 wheel) so we need a C++ compiler
#     at install time. We install build-essential, build, then purge in
#     the same RUN layer so the toolchain doesn't bloat the final image.
#     ultralytics declares a hard `opencv-python` requirement so it'll
#     install full opencv on top of our headless build — that's fine,
#     the libgl1 apt dep above covers what full opencv loads on import.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir \
        "insightface==0.7.3" \
        "onnxruntime>=1.16,<2" \
        "paddleocr>=2.10,<3" \
        "paddlepaddle>=2.6,<4" \
        "ultralytics>=8.2,<9" \
        "hikaxpro>=2.0" \
        "openai>=1.40,<2" \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

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
    PADDLEOCR_HOME=/opt/itips/models/paddleocr \
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
                /opt/itips/models/paddleocr \
                /opt/itips/models/yolo \
                /opt/itips/models/torch \
                /opt/itips/models/huggingface \
    # PaddleOCR hardcodes `~/.paddleocr/` and ignores PADDLEOCR_HOME,
    # so first-run downloads of the detector/recognizer/cls models
    # would land outside the persistent volume and re-download on
    # every restart. Symlink the home path into the mounted model
    # tree so the cache survives container recreation.
    && ln -s /opt/itips/models/paddleocr /home/itips/.paddleocr \
    && chown -R itips:itips /opt/itips /app /home/itips/.paddleocr
USER itips

EXPOSE 5050 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5050/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "itips"]
