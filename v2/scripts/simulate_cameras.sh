#!/bin/sh
# Publish per-camera MP4s into MediaMTX as distinct RTSP streams.
#
# Mapping is explicit, driven by env vars on the ffmpeg-feeder container.
# Each cam slot reads SIM_CAM{N}_FILE; an unset or missing file silently
# leaves that slot empty.

set -eu

MTX_HOST=${MTX_HOST:-mediamtx}
MTX_PORT=${MTX_PORT:-8554}

if [ ! -d /sim ]; then
    echo "/sim is not mounted — drop MP4s into v2/assets/sim/ and mount them."
    exec sleep infinity
fi

publish() {
    slot=$1
    rel=$2
    if [ -z "$rel" ]; then
        return 0
    fi
    src="/sim/${rel}"
    if [ ! -f "$src" ]; then
        echo "  ✗ cam${slot}: ${rel} not found under /sim — skipping."
        return 0
    fi
    echo "  ✓ cam${slot}: ${rel} → rtsp://${MTX_HOST}:${MTX_PORT}/cam${slot}"
    ffmpeg \
        -re -stream_loop -1 -i "$src" \
        -c:v copy -an -f rtsp \
        -rtsp_transport tcp \
        "rtsp://${MTX_HOST}:${MTX_PORT}/cam${slot}" &
}

echo "Publishing simulation streams:"
publish 1 "${SIM_CAM1_FILE:-}"
publish 2 "${SIM_CAM2_FILE:-}"
publish 3 "${SIM_CAM3_FILE:-}"
publish 4 "${SIM_CAM4_FILE:-}"

# If nothing was published, idle so the container does not respawn-loop.
if ! jobs -p | grep -q .; then
    echo "No streams published — sleeping forever."
    exec sleep infinity
fi

wait
