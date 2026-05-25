#!/usr/bin/env bash
# Convert yolo11n.pt to a Jetson-native TensorRT engine.
#
# Run this ONCE per Jetson, after first boot. The output engine file is
# architecture-specific and cannot be shared between machines.

set -euo pipefail

MODELS_DIR=${MODELS_DIR:-/opt/itips/models}
MODEL_NAME=${MODEL_NAME:-yolo11n}
PRECISION=${PRECISION:-fp16}     # fp16 | int8
IMG_SIZE=${IMG_SIZE:-640}

mkdir -p "${MODELS_DIR}"
cd "${MODELS_DIR}"

if [ ! -f "${MODEL_NAME}.pt" ]; then
    echo "Downloading ${MODEL_NAME}.pt..."
    curl -fL --retry 3 -o "${MODEL_NAME}.pt" \
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/${MODEL_NAME}.pt"
fi

echo "Exporting ${MODEL_NAME}.pt to TensorRT (${PRECISION}, ${IMG_SIZE}px)..."

python3 - <<PY
from ultralytics import YOLO
half = "${PRECISION}" == "fp16"
int8 = "${PRECISION}" == "int8"
model = YOLO("${MODEL_NAME}.pt")
model.export(format="engine", half=half, int8=int8, imgsz=${IMG_SIZE}, device=0)
PY

ENGINE="${MODELS_DIR}/${MODEL_NAME}.engine"
if [ ! -f "${ENGINE}" ]; then
    echo "Export did not produce ${ENGINE}" >&2
    exit 1
fi

echo "Engine: ${ENGINE}"
echo "SHA-256: $(sha256sum "${ENGINE}" | cut -d' ' -f1)"
echo "Set ITIPS_YOLO_MODEL=${ENGINE} in your .env."
