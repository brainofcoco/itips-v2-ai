#!/usr/bin/env bash
# Export YOLOv8n to a TensorRT FP16 engine for GPU inference on the Jetson.
# Run once per Jetson — engines are architecture-specific, never copy them
# between machines (DEPLOY.md §2.3). Needs CUDA/TensorRT + ultralytics, so
# run it inside the Jetson container:
#   docker compose -f docker-compose.yml -f docker-compose.jetson.yml \
#     run --rm itips ./scripts/export_yolo_tensorrt.sh
# Then set ITIPS_BEHAVIOR_MODEL=/opt/itips/models/yolov8n.engine in .env.

set -euo pipefail

MODEL=${ITIPS_YOLO_MODEL:-yolov8n.pt}
IMGSZ=${ITIPS_BEHAVIOR_IMGSZ:-1280}
OUT_DIR=${ITIPS_MODELS_DIR:-/opt/itips/models}

echo "Exporting ${MODEL} -> TensorRT FP16 engine (imgsz=${IMGSZ})..."
yolo export model="${MODEL}" format=engine half=True imgsz="${IMGSZ}"

# ultralytics writes <model>.engine beside the .pt; move it into the
# persistent model volume so the container finds it across restarts.
ENGINE="${MODEL%.pt}.engine"
mkdir -p "${OUT_DIR}"
mv -f "${ENGINE}" "${OUT_DIR}/${ENGINE}"
echo "Wrote ${OUT_DIR}/${ENGINE}"
echo "Set ITIPS_BEHAVIOR_MODEL=${OUT_DIR}/${ENGINE} to use it."
