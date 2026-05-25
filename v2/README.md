# ITIPS-ai — V2

The on-Jetson edge node of the ITIPS national telecom infrastructure protection
platform. This is a clean re-implementation of the V1 codebase against the
v2.5 PRD (`../docs/about-project.md`), with the structural fixes called out in
`../docs/feedback.md`.

V2 exists to do four things V1 did not:

1. **Fix the lag.** Per-camera worker threads, YOLO11n + TensorRT, 640 px input.
   The single-threaded `for cam in cameras: ...` loop in V1 is gone.
2. **Honour the team boundary.** The AI pipeline ends at a local intake queue.
   It never makes outbound cloud calls. The Sync Agent (backend deliverable)
   drains the queue.
3. **Produce signed evidence.** Per-incident packages with HMAC-SHA-256
   signatures, a 15-minute pre-event ring buffer, and the PRD §4.3 manifest.
4. **Accept backend commands.** A separate port 8443 server implements the
   B1–B5 inbound API: personnel sync, config push, maintenance windows,
   PTZ overrides, firmware.

V2 keeps every working detection idea from V1 (GStreamer + NVDEC, InsightFace
SCRFD+ArcFace, sensor-triggered PTZ pan, zone-aware behaviour analysis,
object-removal heuristics) and reorganises them around those four pillars.

## Quick start

```bash
# 1. Copy the env template and fill in real values
cp .env.example .env
nano .env                  # or vim / code / open -e

# 2. Sanity-check on a laptop with no cameras and no GPU (simulation mode)
make sim

# 3. On the Jetson, with real cameras
make up
```

`make help` lists every target.

Once it's up, open **http://localhost:5050/docs** in a browser — that's the
Scalar API reference, with a built-in HTTP client so you can fire every
endpoint without leaving the page. The same UI is also mounted on the
inbound surface at **http://localhost:8443/docs** (use the bearer token
you set in `.env` as `ITIPS_INBOUND_TOKEN`).

## Repository layout

```
v2/
├── README.md                 — this file
├── ARCHITECTURE.md           — design decisions, the lag fix, the boundary
├── DEPLOY.md                 — Jetson deployment runbook
├── RULE.md                   — coding standards
├── pyproject.toml            — Python 3.10 deps, pinned for L4T r36
├── Dockerfile                — multi-stage, l4t-ml base
├── docker-compose.yml        — production-shaped compose on a Jetson
├── docker-compose.sim.yml    — laptop simulation overlay (MediaMTX + ffmpeg)
├── Makefile                  — common dev targets
├── .env.example              — secrets and per-site config template
├── .gitignore                — protects .env, model weights, evidence
├── .dockerignore
├── .github/workflows/        — CI: lint, tests, arm64 image build
├── config/
│   ├── settings.py           — single source of truth for tunables
│   └── zones.example.json    — default detection zones
├── itips/                    — the Python package
│   ├── __main__.py           — `python -m itips`
│   ├── app.py                — bootstrap
│   ├── runtime/              — orchestrator + per-camera workers (LAG FIX)
│   ├── camera/               — GStreamer RTSP reader
│   ├── detection/            — YOLO11n, InsightFace, ByteTrack, Plate Recognizer
│   ├── behaviour/            — zones, rules, tracks, object removal
│   ├── sensors/              — AX PRO listener
│   ├── alerts/               — alert engine, PTZ controller
│   ├── evidence/             — packager, signing, manifest, pre-event buffer
│   ├── sync/                 — local intake queue → backend Sync Agent
│   ├── api/                  — port 5050 public, port 8443 inbound
│   └── utils/                — geometry, drawing, clock, logging
├── scripts/
│   ├── export_yolo_tensorrt.sh
│   ├── simulate_cameras.sh
│   └── healthcheck.sh
├── tests/                    — pytest suite
└── docs/                     — V2-specific design notes
```

## Status

V2 is being built in parallel with the V1 POC site visit (2026-05-21). The
goal is for V2 to be the platform that ships from Phase 1 onwards. V1 carries
the POC.

See `ARCHITECTURE.md` for *why* the structure looks the way it does. See
`../docs/feedback.md` for the gap analysis that motivated the rebuild.

## License

Proprietary — Seismic Digital & Innovations Limited.
