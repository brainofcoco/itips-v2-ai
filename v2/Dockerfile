# ITIPS V2.1 — Dahua-native edge node.
#
# Two build targets from one file:
#   core   (default) — Dahua-native runtime only. ~280 MB, no GPU, no ML.
#   ml-cpu           — adds the CPU ML fallback layer for Mac/x86 validation.
# The Jetson GPU build is separate: see Dockerfile.jetson.
# docker-compose.yml pins `target: core`; `make build-ml` selects ml-cpu.

FROM python:3.11-slim-bookworm AS core

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# cv2 runtime libs, libgomp1 (OpenMP), and ffmpeg for H.265 evidence
# clips (PRD §4.3 REQ-EV-01).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
        curl ca-certificates tini ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Core runtime. headless OpenCV so we don't pull GTK into the container.
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

COPY pyproject.toml ./
COPY itips/ ./itips/
COPY config/ ./config/
COPY openapi/ ./openapi/

# ML libs cache their weights under $HOME by default; redirect to the
# mounted /opt/itips/models volume so first-run downloads survive restarts.
ENV INSIGHTFACE_HOME=/opt/itips/models/insightface \
    PADDLEOCR_HOME=/opt/itips/models/paddleocr \
    YOLO_CONFIG_DIR=/opt/itips/models/yolo \
    TORCH_HOME=/opt/itips/models/torch \
    HF_HOME=/opt/itips/models/huggingface

# Non-root user. Volume mount points are pre-created and chowned. PaddleOCR
# hardcodes ~/.paddleocr (ignores PADDLEOCR_HOME), so symlink it into the
# persistent tree to avoid re-downloading models on every restart.
RUN useradd --uid 1000 --create-home --shell /bin/bash itips \
    && mkdir -p /opt/itips/var \
                /opt/itips/evidence_store \
                /opt/itips/models/insightface \
                /opt/itips/models/paddleocr \
                /opt/itips/models/yolo \
                /opt/itips/models/torch \
                /opt/itips/models/huggingface \
    && ln -s /opt/itips/models/paddleocr /home/itips/.paddleocr \
    && chown -R itips:itips /opt/itips /app /home/itips/.paddleocr
USER itips

EXPOSE 5050 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5050/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "itips"]


# ─── ml-cpu: CPU ML fallback layer (Mac/x86 validation only) ──────────
# CPU-only torch from pytorch.org — PyPI's torch wheel drags 3+ GB of CUDA
# libs that don't run on Mac and don't match JetPack. The Jetson uses its
# own L4T GPU wheels (Dockerfile.jetson).
FROM core AS ml-cpu
USER root
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision

# insightface ships an sdist (no aarch64 wheel) so it needs a C++ toolchain;
# install build-essential, build, then purge in one layer. onnx/protobuf are
# pinned: insightface leaves onnx unpinned and the resolver's pick crashes on
# import against the protobuf paddlepaddle caps us at — 1.16.1 + 3.20.2 cohere.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir \
        "insightface==0.7.3" \
        "onnxruntime>=1.16,<2" \
        "onnx==1.16.1" \
        "protobuf==3.20.2" \
        "paddleocr>=2.10,<3" \
        "paddlepaddle>=2.6,<4" \
        "ultralytics>=8.2,<9" \
        "hikaxpro>=2.0" \
        "openai>=1.40,<2" \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
USER itips
