#!/usr/bin/env bash
# Export YOLO11n.pt -> YOLO11n.onnx for the no-PyTorch runtime path.
#
# Run ONCE on any machine that has ultralytics installed (e.g. the Jetson
# while the current image still ships ultralytics, or a laptop with
# `pip install ultralytics`). The output .onnx lives on a host volume so
# the production runtime image can drop torch + ultralytics entirely.
#
# Why a separate step:
#   The export still needs ultralytics + PyTorch in scope (the conversion
#   tool runs the model once with random weights to trace it). Doing that
#   inside the production image would defeat the point of dropping torch.
#   Doing it inside the *current* image once, then mounting the .onnx into
#   a smaller future image, is the standard build-vs-runtime split.

set -euo pipefail

MODELS_DIR=${MODELS_DIR:-/opt/itips/models}
MODEL_NAME=${MODEL_NAME:-yolo11n}
IMG_SIZE=${IMG_SIZE:-640}
OPSET=${OPSET:-12}     # onnxruntime-gpu 1.17 (V2's pinned version) supports up to 17;
                       # 12 is the broadly-compatible choice.

mkdir -p "$MODELS_DIR"
cd "$MODELS_DIR"

if [ ! -f "$MODEL_NAME.pt" ]; then
    echo "Downloading $MODEL_NAME.pt..."
    curl -fL --retry 3 -o "$MODEL_NAME.pt" \
        "https://github.com/ultralytics/assets/releases/download/v8.3.0/$MODEL_NAME.pt"
fi

echo "Exporting $MODEL_NAME.pt -> $MODEL_NAME.onnx  (imgsz=$IMG_SIZE, opset=$OPSET)"

python3 - <<PY
from ultralytics import YOLO
m = YOLO("$MODEL_NAME.pt")
m.export(format="onnx", imgsz=$IMG_SIZE, opset=$OPSET, simplify=True, dynamic=False)
PY

ONNX="$MODELS_DIR/$MODEL_NAME.onnx"
if [ ! -f "$ONNX" ]; then
    echo "Export did not produce $ONNX" >&2
    exit 1
fi

echo ""
echo "Wrote $ONNX ($(stat -c%s "$ONNX") bytes)"
echo "SHA-256: $(sha256sum "$ONNX" | cut -d" " -f1)"
echo ""
echo "Next step: set these in your .env:"
echo "  ITIPS_YOLO_BACKEND=onnx"
echo "  ITIPS_YOLO_MODEL=$ONNX"
