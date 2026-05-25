# ITIPS V2 — Jetson Commands Runbook

> Quick reference for everything you need to do on the Jetson while reviewing, running, testing, or redeploying V2 from the `DRAFT` branch.
>
> All commands assume you're SSH'd into the Jetson as `seismic` and the repo lives at `~/ITIPS-ai`.

---

## Where things live

| Path | What |
|---|---|
| `~/ITIPS-ai/` | git working tree (currently on `DRAFT`) |
| `~/ITIPS-ai/v2/` | V2 codebase |
| `~/ITIPS-ai/v2/itips/` | Python package (`itips.runtime`, `itips.detection`, …) |
| `~/ITIPS-ai/v2/config/` | settings.py, zones, presets |
| `~/ITIPS-ai/v2/scripts/` | shell helpers (TensorRT/ONNX export, healthcheck, sim) |
| `~/ITIPS-ai/v2/docs/` | this runbook + `jetson-memory.md` |
| `~/ITIPS-ai/v2/.env` | live config (RTSP creds, AXPRO, PTZ, YOLO model, flags) — **gitignored** |
| `~/ITIPS-ai/v2/.env.example` | template, safe to commit |
| `/opt/itips/models/` | model files (yolo11n.pt, yolo11n.onnx, …) |
| `/opt/itips/evidence_store/` | recorded clips + intruder snapshots |
| `/opt/itips/var/` | logs, intake.sqlite, zones.json runtime overrides |
| `/opt/itips/dataset/` | enrolled face dataset (when face_engine is on) |

---

## Branch + repo

```bash
cd ~/ITIPS-ai
git status                         # show current branch + uncommitted changes
git branch -a                      # all branches
git fetch --all                    # pick up new remote branches
git checkout DRAFT                 # switch to DRAFT (this is the V2 branch)
git pull origin DRAFT              # pull latest changes from GitHub
git log --oneline -10              # recent commits
```

---

## .env — the file you'll edit most

`.env` is the single source of truth for credentials and feature flags.
It's gitignored, so editing it never accidentally leaks secrets. The
template lives in `.env.example`.

```bash
cd ~/ITIPS-ai/v2
nano .env                          # edit (or `code .env` from VS Code Remote)
diff .env.example .env             # see what's customised
```

Most important knobs while testing on this Jetson:

```bash
# Camera worker mode: streaming = continuous RTSP, event_driven = on-demand snapshot
ITIPS_CAMERA_MODE=event_driven

# YOLO backend: ultralytics (PyTorch+ByteTrack, heavier) or onnx (no torch, lighter)
ITIPS_YOLO_BACKEND=onnx
ITIPS_YOLO_MODEL=/opt/itips/models/yolo11n.onnx

# Face recognition off saves ~1.5 GB. Set to false to re-enable on a bigger Jetson.
ITIPS_FACE_ENGINE_DISABLED=true

# Each camera's RTSP URL (auth lives in the URL via user:pass@host)
ITIPS_CAMERA_1_RTSP=
ITIPS_CAMERA_2_RTSP=
ITIPS_CAMERA_3_RTSP=
ITIPS_CAMERA_4_RTSP=rtsp://admin:...@192.168.0.124:554/cam/realmonitor?channel=1&subtype=0
ITIPS_CAMERA_MAX_FRAME_WIDTH=1280
```

After any `.env` change you have to recreate the container (env_file is
read only at start):

```bash
sudo docker compose up -d --force-recreate
```

---

## Recommended `.env` for Jetson Orin Nano (3 cameras)

Verified live on this hardware — container memory sits at ~330 MiB at idle with all three cameras subscribed, leaving plenty of host headroom. Cam 2 stays empty because no second physical camera is installed at the site.

```bash
# Worker mode + YOLO backend (the two big wins on Orin Nano)
ITIPS_CAMERA_MODE=event_driven
ITIPS_YOLO_BACKEND=onnx
ITIPS_YOLO_MODEL=/opt/itips/models/yolo11n.onnx

# Face engine off saves another ~1.5 GB. Re-enable on bigger Jetsons.
ITIPS_FACE_ENGINE_DISABLED=true

# Three cameras (cam 2 not installed at this site)
ITIPS_CAMERA_1_RTSP=rtsp://admin:<pw>@192.168.0.184:554/cam/realmonitor?channel=1&subtype=0
ITIPS_CAMERA_2_RTSP=
ITIPS_CAMERA_3_RTSP=rtsp://admin:<pw>@192.168.0.123:554/cam/realmonitor?channel=1&subtype=0
ITIPS_CAMERA_4_RTSP=rtsp://admin:<pw>@192.168.0.124:554/cam/realmonitor?channel=1&subtype=0
ITIPS_CAMERA_MAX_FRAME_WIDTH=1280
```

Memory budget on the actual Jetson, all 3 cameras subscribed, no event activity yet:

| Layer | Used |
|---|---:|
| Container (V2 + onnxruntime + listeners) | ~330 MiB |
| Host (VS Code Remote SSH + dockerd + system) | ~1.5 GB |
| **Total** | **~1.9 GB / 7.4 GB** |

That leaves ~5.5 GB free for inference bursts during camera events. With the docker `mem_limit: 6g` cap, V2 can never push the host into kernel-OOM territory regardless of what an event burst does.

To scale beyond 3 cameras: each extra `EventDrivenWorker` adds ~0.1 MiB at idle (one HTTP long-poll thread). The practical ceiling is set by how many cameras the Jetson can run inference for during a simultaneous-event burst, not by idle footprint.

After editing `.env`, recreate the container so the new env is loaded:

```bash
cd ~/ITIPS-ai/v2 && sudo docker compose up -d --force-recreate
```

To verify all configured cameras' event streams connected:

```bash
sudo docker logs itips 2>&1 | grep "event stream connected"
# Expect one line per active camera, e.g.:
#   cam 1 event stream connected (192.168.0.184:80)
#   cam 3 event stream connected (192.168.0.123:80)
#   cam 4 event stream connected (192.168.0.124:80)
```

---

## One-time setup (fresh Jetson)

```bash
# 1. Clone the repo if you don't have it yet
cd ~
git clone https://github.com/Seismic-Consulting-Limited/ITIPS-ai.git
cd ITIPS-ai
git checkout DRAFT

# 2. Create the runtime data dirs (owned by uid 1000 so the container can write)
sudo mkdir -p /opt/itips/{var/logs,evidence_store,dataset,models}
sudo chown -R 1000:1000 /opt/itips

# 3. Make .env from the template, then fill in real values
cd v2
cp .env.example .env
nano .env
```

The Jetson should already have:

- Docker daemon with the `nvidia` runtime (`docker info | grep -i runtime` shows `Default Runtime: nvidia`)
- L4T-ML base image cached (it's huge — first pull is 8+ GB)

If `nvidia` isn't the default runtime, edit `/etc/docker/daemon.json` and restart docker.

---

## Build the image

```bash
cd ~/ITIPS-ai/v2
sudo docker compose build
```

First build: 20-30 min on Orin Nano. Subsequent builds reuse cached layers — usually under 2 min when only `itips/` or `scripts/` changes.

If a build hangs on the `insightface` Cython compile and the Jetson reboots, the Dockerfile already sets `MAKEFLAGS=-j1` to serialise compilation. If you still hit it, free more host RAM first:

```bash
# Stop GUI services (you can SSH back in)
sudo systemctl isolate multi-user.target

# Stop k3s if it's running (V2 doesn't need it)
sudo systemctl stop k3s
```

---

## One-time: export YOLO to ONNX (only needed if using ITIPS_YOLO_BACKEND=onnx)

```bash
cd ~/ITIPS-ai/v2
# Runs ultralytics' .pt -> .onnx export inside the docker image and writes
# the result to /opt/itips/models on the host.
sudo docker run --rm --runtime=nvidia \
    -v /opt/itips/models:/opt/itips/models \
    itips/edge:2.0.0 \
    bash /app/scripts/export_yolo_onnx.sh

# Confirm the file is there
ls -lh /opt/itips/models/yolo11n.onnx
```

Then set in `.env`:

```bash
ITIPS_YOLO_BACKEND=onnx
ITIPS_YOLO_MODEL=/opt/itips/models/yolo11n.onnx
```

---

## Start V2

```bash
cd ~/ITIPS-ai/v2
sudo docker compose up -d                       # detached
sudo docker ps --filter name=itips              # confirm "Up Xs (healthy)" status
```

The compose file sets `restart: unless-stopped`, `mem_limit: 6g`, `memswap_limit: 6g`, so a runaway V2 will get OOM-killed cleanly by docker instead of panicking the Jetson kernel.

---

## Logs and live state

```bash
# Tail logs (most recent 100 lines, then follow)
sudo docker logs -n 100 -f itips

# Last 30s of logs (one-shot, no follow)
sudo docker logs --since 30s itips

# Live container stats (mem, cpu, net I/O) — Ctrl+C to exit
sudo docker stats itips

# Host memory + swap
free -h

# GPU + thermals on Jetson
sudo tegrastats --interval 2000
```

### Filter for things you care about

```bash
# Detection / event flow
sudo docker logs itips 2>&1 | grep -iE "EVENT|Camera mode|cam [0-9]|detect|alert"

# Errors only
sudo docker logs itips 2>&1 | grep -iE "ERROR|Traceback|FAIL"
```

---

## Test the API surfaces

Both servers run with `network_mode: host`, so they bind directly to the
Jetson's IP. From the Jetson itself:

```bash
# Public API (port 5050)
curl -s http://localhost:5050/health        # {"status":"ok"}
curl -s http://localhost:5050/status        # camera roster, mode, site/device IDs
curl -s http://localhost:5050/status | python3 -m json.tool

# Inbound (backend-facing) API on port 8443
curl -s http://localhost:8443/health         # should be HTTP 200

# Interactive API docs (Scalar)
xdg-open http://localhost:5050/docs           # if you have a browser
# Or visit http://<jetson-ip>:5050/docs from your laptop
```

### Live MJPEG feeds

```bash
# Camera 4 (the one currently wired)
curl -s http://localhost:5050/video_feed/4 > /tmp/cam4.mjpg

# Easier: open in a browser
# http://<jetson-ip>:5050/video_feed/4
```

---

## Where the snapshots go

`EventDrivenWorker` writes an annotated JPEG every time **YOLO confirms a detection** on a snapshot returned by the camera. Camera-side IVS false positives (wind, lighting, IR-cut flicker) are silently skipped, so the directory stays clean.

```
/opt/itips/evidence_store/
└── snapshots/
    ├── cam1/
    │   └── 2026-05-23T03-58-12_VideoMotion.jpg
    ├── cam3/
    │   └── 2026-05-23T04-02-44_CrossLineDetection.jpg
    └── cam4/
        └── 2026-05-23T04-03-01_ObjectDetect.jpg
```

Filename = `{iso-utc-no-colons}_{Dahua event code}.jpg`. UTC keeps them sortable across timezones; colon-free is Windows/SMB-safe if you ever copy them off the device.

To inspect what's been captured:

```bash
# Newest 10 captures across all cameras
sudo find /opt/itips/evidence_store/snapshots -name '*.jpg' -printf '%T@ %p
'     | sort -rn | head -10 | awk '{print $2}'

# Count per camera over the last hour
for c in 1 3 4; do
    n=$(sudo find /opt/itips/evidence_store/snapshots/cam$c -name '*.jpg' -mmin -60 2>/dev/null | wc -l)
    echo "cam$c: $n captures in last hour"
done

# Copy a few off the Jetson to look at them
scp seismic@192.168.0.198:/opt/itips/evidence_store/snapshots/cam4/'*.jpg' ./
```

Event metadata (camera, timestamp, event code, detection bbox) is also written into `/opt/itips/var/intake.sqlite` — that's the queue the Sync Agent (separate backend deliverable) drains to your cloud database.

---

## Stop / restart V2

```bash
cd ~/ITIPS-ai/v2

# Stop V2 cleanly
sudo docker compose down

# Restart in place (after a code change inside the container, e.g.)
sudo docker compose restart

# Force-recreate after editing .env or compose
sudo docker compose up -d --force-recreate

# Nuke V2 (container + image)
sudo docker compose down
sudo docker rmi itips/edge:2.0.0
```

---

## Redeploy after pulling a new commit

```bash
cd ~/ITIPS-ai
git pull origin DRAFT
cd v2

# If only python files changed:
sudo docker compose up -d --build

# If Dockerfile changed or you want a clean rebuild:
sudo docker compose build --no-cache
sudo docker compose up -d
```

---

## Tuning knobs (cheat sheet)

All in `.env`. Recreate the container after editing.

| Env var | Default | What it does |
|---|---|---|
| `ITIPS_CAMERA_MODE` | `streaming` | `event_driven` switches to Dahua eventManager + snapshot worker. **Recommended for Orin Nano.** |
| `ITIPS_YOLO_BACKEND` | `ultralytics` | `onnx` runs YOLO through onnxruntime-gpu only, no torch. Saves ~2 GB. **Recommended for Orin Nano.** |
| `ITIPS_YOLO_MODEL` | `/opt/itips/models/yolo11n.engine` | Path to the YOLO model. Use `yolo11n.onnx` with the onnx backend. |
| `ITIPS_YOLO_IMG_SIZE` | `640` | Inference resolution. Smaller = faster + less memory. |
| `ITIPS_FACE_ENGINE_DISABLED` | `true` (on DRAFT) | Skips InsightFace. Saves ~1.5 GB. Set `false` to re-enable on a bigger Jetson. |
| `ITIPS_INSIGHTFACE_DET_SIZE` | `640` | Drop to `320` to halve face-engine memory if you re-enable it. |
| `ITIPS_CAMERA_MAX_FRAME_WIDTH` | `1920` | Downscale incoming frames before processing. `1280` is plenty for YOLO. |
| `ITIPS_EVIDENCE_PRE_EVENT_SECONDS` | `15` | Pre-event ring buffer per camera. Big memory cost if streaming. |

---

## Inspect the project from the command line

```bash
# Tree of the V2 package
cd ~/ITIPS-ai/v2
find itips -type f -name "*.py" | sort

# Lines of code per module
find itips -name "*.py" | xargs wc -l | sort -n

# What changed on this branch vs main
git diff --stat main..DRAFT -- v2/

# What changed in the most recent commit
git show --stat HEAD
git show HEAD -- v2/itips/detection/yolo_onnx.py | head -60
```

---

## Troubleshooting

### V2 keeps OOM-killing at boot

Check `sudo docker inspect itips --format '{{.State.OOMKilled}}'` — if true, V2 hit `mem_limit`. Either:

- Switch backend: `ITIPS_YOLO_BACKEND=onnx` (saves ~2 GB)
- Disable face engine: `ITIPS_FACE_ENGINE_DISABLED=true` (saves ~1.5 GB)
- Raise the cap in `docker-compose.yml` (`mem_limit: 7g` is the practical ceiling on Orin Nano 8GB)
- Reduce cameras / frame width in `.env`

See `docs/jetson-memory.md` for the full breakdown.

### Jetson rebooted during build or runtime

Almost always RAM exhaustion. `last reboot | head -5` shows recent boots. Check that:

- `sudo systemctl is-active k3s` is **inactive** (k3s eats ~600 MB)
- `systemctl get-default` is **multi-user.target** (no GUI = ~700 MB saved)
- `docker-compose.yml` has `mem_limit` and `memswap_limit` set

### Camera 1 / 3 won't connect

```bash
# Reachability
ping -c 2 192.168.0.184
nc -z -w 3 192.168.0.184 554

# RTSP playable?
sudo docker exec itips gst-launch-1.0 -v rtspsrc location='rtsp://...' ! fakesink \
    2>&1 | head -30

# HTTP / snapshot API
curl -s --digest -u "admin:..." -o /tmp/snap.jpg \
    "http://192.168.0.184/cgi-bin/snapshot.cgi?channel=1" && ls -l /tmp/snap.jpg
```

### "Permission denied" on docker without sudo

User `seismic` isn't in the `docker` group. Either add them:

```bash
sudo usermod -aG docker seismic
# log out and back in
```

…or just keep using `sudo docker` everywhere (what this doc assumes).

### Inspect what's actually inside the container

```bash
# Open a shell inside the running container
sudo docker exec -it itips bash

# Inside the container:
ls /app/itips/                          # the python package
ls /app/config/                         # config
ls /opt/itips/                          # mounted host volumes
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
python3 -c "import torch; print(torch.cuda.is_available())"
```

---

## Clean slate

```bash
# Stop V2
cd ~/ITIPS-ai/v2 && sudo docker compose down

# Free disk
sudo docker image prune -f
sudo docker builder prune -f --filter 'until=24h'

# Wipe evidence (this destroys recorded clips — be sure)
sudo rm -rf /opt/itips/evidence_store/*
sudo rm -rf /opt/itips/var/intake.sqlite

# Wipe runtime zone overrides (revert to checked-in seeds)
sudo rm -f /opt/itips/var/zones.json
```

---

## Where to look in the code

| What you want to understand | File |
|---|---|
| App bootstrap (DI wiring) | `itips/app.py` |
| Per-camera worker (streaming) | `itips/runtime/camera_worker.py` |
| Per-camera worker (event-driven) | `itips/runtime/event_worker.py` |
| Dahua event subscription | `itips/sensors/dahua_events.py` |
| Snapshot HTTP client | `itips/camera/dahua_http.py` |
| YOLO via PyTorch | `itips/detection/yolo.py` |
| YOLO via ONNX only | `itips/detection/yolo_onnx.py` |
| Face recognition | `itips/detection/face.py` |
| Behaviour zones + rules | `itips/behaviour/` |
| Alert routing | `itips/alerts/engine.py` |
| Evidence packager | `itips/evidence/packager.py` |
| Public API (port 5050) | `itips/api/public.py` |
| Inbound API (port 8443) | `itips/api/inbound.py` |
| Configuration | `config/settings.py` |
| Why the memory profile is what it is | `docs/jetson-memory.md` |
