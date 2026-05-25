# syntax=docker/dockerfile:1.7
#
# ITIPS V2 — Jetson Orin Nano Super (ARM64, L4T r36, JetPack 6.x)
#
# Two-stage build:
#   1. base   — system packages (GStreamer, libs) on top of the L4T-ML image.
#   2. runtime — pip deps + app code.
#
# The L4T-ML base image pre-ships CUDA, cuDNN, TensorRT, and PyTorch built
# for the Jetson's ARM64 GPU, so we don't try to install torch ourselves.
# Building on x86 requires QEMU; CI does that.

ARG L4T_BASE=nvcr.io/nvidia/l4t-ml:r36.2.0-py3

# ─────────────────────────────────────────────────────────────────────
FROM ${L4T_BASE} AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        libgles2 libegl1 libgl1 libgbm1 libdrm2 \
        libx11-6 libglib2.0-0 libsm6 libxext6 libxrender1 \
        curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────
FROM base AS runtime

WORKDIR /app

# Constrain numpy to 1.x — onnxruntime + ultralytics on Jetson are not
# yet numpy-2 compatible.
RUN pip3 install --break-system-packages "numpy<2"

# A custom-built onnxruntime-gpu wheel for ARM64 + JetPack 6.
# Pin the wheel via DIGEST in CI; for local builds we accept the GitHub release.
# The wheel must keep its PEP 427 filename — pip parses it to validate
# python/abi/platform tags. Renaming to /tmp/ort.whl breaks the install.
ARG ONNX_WHEEL_URL=https://github.com/Seismic-Consulting-Limited/onnx-runtime-deps/releases/download/v0.1.0-deps/onnxruntime_gpu-1.17.0-cp310-cp310-linux_aarch64.whl
RUN ONNX_WHEEL_NAME="$(basename "${ONNX_WHEEL_URL}")" \
    && curl -fsSL "${ONNX_WHEEL_URL}" -o "/tmp/${ONNX_WHEEL_NAME}" \
    && pip3 install --break-system-packages "/tmp/${ONNX_WHEEL_NAME}" \
    && rm "/tmp/${ONNX_WHEEL_NAME}"

COPY pyproject.toml ./
COPY itips/ ./itips/
COPY config/ ./config/
COPY openapi/ ./openapi/

# Constrain build-time parallelism so source builds (insightface Cython,
# any sdists) don't OOM the Jetson during pip install.
ENV MAKEFLAGS=-j1 \
    CMAKE_BUILD_PARALLEL_LEVEL=1 \
    MAX_JOBS=1

# Layer A: install the V2 package itself, no deps (handled below).
RUN pip3 install --break-system-packages --no-deps .

# Layer B: web/utility stack (small deps, safe to let pip resolve).
RUN pip3 install --break-system-packages --ignore-installed blinker \
        flask==3.0.3 werkzeug==3.0.3 \
        python-dotenv==1.0.1 requests==2.32.3 \
        pydantic==2.8.2 pyyaml==6.0.2 \
        opencv-python==4.10.0.84 pillow==10.4.0 \
        onvif-zeep==0.2.12 hikaxpro==2.3.0 \
        pycryptodomex==3.20.0

# Layer C: transitive ML deps that the next --no-deps layer would otherwise miss.
# This list mirrors V1's working Dockerfile (proven on this Jetson).
RUN pip3 install --break-system-packages \
        tqdm matplotlib packaging lazy_loader imageio tifffile networkx \
        PyWavelets qudida albucore==0.0.17 albumentations==1.3.1 \
        onnx scipy scikit-image

# Layer D: ML stack pinned with --no-deps so pip can't drag PyPI torch
# over the L4T-ML base's CUDA-built torch.
RUN pip3 install --break-system-packages --no-deps \
        ultralytics==8.3.40 lapx==0.5.11 insightface==0.7.3

# Layer E: re-pin numpy<2 — earlier deps can silently bump it.
RUN pip3 install --break-system-packages --force-reinstall "numpy<2"

COPY scripts/ ./scripts/
RUN chmod +x ./scripts/*.sh || true

# Non-root user — the L4T container runs as root by default, which is
# unnecessary at runtime. Drop privileges after model files exist.
RUN useradd --uid 1000 --create-home --shell /bin/bash itips \
    && mkdir -p /opt/itips/var /opt/itips/evidence_store /opt/itips/models /opt/itips/dataset \
    && chown -R itips:itips /opt/itips /app
USER itips

EXPOSE 5050 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:5050/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "itips"]
