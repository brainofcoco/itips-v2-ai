# ITIPS-ai — V2.1 (Dahua-native)

The on-Jetson edge node of the ITIPS national telecom infrastructure
protection platform. V2.1 trims the heavy ML stack from V2 and leans on
the Dahua cameras' onboard AI as the primary classifier.

The Jetson runs zero inference. The cameras' WizMind / TiOC silicon does
face recognition, line/region/wander detection, ANPR, fire/smoke
detection, and active deterrence. The Jetson subscribes to camera
events, packages signed evidence, and proxies operator clicks onto the
Dahua HTTP API.

## Run it locally on your Mac, against real cameras

The default (`core`) Docker image is ~1 GB (ffmpeg + OpenCV dominate),
multi-arch, and has no GPU or ML dependency — built for Mac/lab live
validation against real cameras. Production on the Jetson uses a separate
GPU build (`make build-jetson`, `Dockerfile.jetson`) that adds the ML
fallback layer on CUDA/TensorRT. See `DEPLOY.md §0` for the three image
shapes.

### 1. One-time setup

```bash
cd v2

# Copy and edit env. The RTSP URLs already carry the HTTP credentials
# (Dahua uses the same user/pass on both protocols), so this is the
# only knob per camera.
cp .env.example .env
nano .env
```

Required `.env` keys:

| Key | What |
|---|---|
| `ITIPS_SITE_ID`, `ITIPS_OPERATOR_ID`, `ITIPS_DEVICE_ID` | Anything for local test — they get stamped onto intake records. |
| `ITIPS_CAMERA_1_RTSP` (and 2/3/4) | `rtsp://admin:PASS@192.168.0.184:554/...`. Leave unused slots blank. |
| `ITIPS_DEVICE_HMAC_KEY` | 64-hex-char (any 32 bytes will do for local test). |
| `ITIPS_INBOUND_TOKEN` | Any string — used by the dashboard's Inbound proxy. |

### 2. Confirm your Mac can reach the cameras

```bash
# Should return HTTP 200 (digest-auth challenge) on each one:
curl -I -u admin:PASS --digest http://192.168.0.184/cgi-bin/snapshot.cgi
curl -I -u admin:PASS --digest http://192.168.0.123/cgi-bin/snapshot.cgi
curl -I -u admin:PASS --digest http://192.168.0.124/cgi-bin/snapshot.cgi
```

If those work, your Mac (and therefore the container) is on the same LAN
as the cameras and can talk to them.

### 3. Build + run

```bash
make build       # ~90s first time, ~5s on subsequent rebuilds (cached layers)
make up
make logs
```

Open the dashboard at <http://localhost:5050/dashboard>.

Tabs you'll use most:

- **Live** — MJPEG tiles of the latest event snapshot from each camera,
  plus a quick deterrence fire/standdown button per camera.
- **Workers** — upload a JPEG of a worker; the container fans it out via
  `POST /cgi-bin/faceRecognitionServer.cgi?action=addPerson` to every
  camera. Each camera assigns its own UID; we persist the mapping.
- **Plates** — add/remove plates per camera against `TrafficRedList` (allow,
  optional gate-open) or `TrafficBlackList` (escalate).
- **Test Console** — three panes:
  1. *Live Dahua events* — SSE feed of every event the cameras fire,
     including the ones filtered by the dispatcher's cooldown.
  2. *Camera quick-actions + PTZ jog pad + Event simulator* — press-and-hold
     directional jog, plus one-click buttons that inject synthetic events
     into the AlertEngine so you can verify the incident lifecycle without
     a real motion trigger.
  3. *Inbound API tester* — collapsible forms for B1–B5 (proxied through
     the dashboard so the bearer stays server-side).
- **Alerts** — SSE of every record the AlertEngine emits.
- **Incidents** — local signed evidence packages.

API reference (Scalar UI):

- Public surface: <http://localhost:5050/docs>
- Inbound surface: <http://localhost:8443/docs> (bearer required)

### 4. Tear down

```bash
make down        # stops the container
make fresh       # also cleans pycache (evidence + intake DB are preserved on host)
```

Inspect anything the container wrote on your Mac:

```bash
ls var/                    # intake.sqlite, personnel.sqlite, logs/
ls evidence_store/incidents/  # signed packages
```

### Will it work on Docker Desktop for Mac?

Yes — with one important note. On macOS, Docker Desktop **does not honour
`network_mode: host`**, which is why the compose file uses normal port
mapping (`-p 5050:5050 -p 8443:8443`). All traffic *from* the container
*to* your cameras (192.168.x.x) routes through Docker's NAT and reaches
them just like any other LAN device — no special config needed. We use
that path for every Dahua call (`eventManager.cgi`, `snapshot.cgi`,
`faceRecognitionServer.cgi`, `recordUpdater.cgi`, `coaxialControlIO.cgi`,
`ptz.cgi`). No camera ever needs to reach back into the container.

When you move to the Jetson the same compose file works as-is on Linux.

## Repository layout

```
v2/
├── README.md
├── pyproject.toml            — Python 3.10–3.11 deps (no ML)
├── Dockerfile                — slim multi-arch image
├── docker-compose.yml        — Mac + Jetson, same file
├── Makefile
├── .env.example
├── config/
│   └── settings.py
├── itips/
│   ├── __main__.py
│   ├── app.py
│   ├── runtime/              — orchestrator, dispatchers, EventTap
│   ├── camera/               — Dahua HTTP clients
│   │   ├── dahua_http.py     —   endpoint + snapshot
│   │   ├── dahua_face_db.py  —   faceRecognitionServer.cgi
│   │   ├── dahua_plate_db.py —   recordUpdater.cgi (Red/BlackList)
│   │   ├── dahua_deterrence  —   coaxialControlIO.cgi
│   │   ├── dahua_ptz.py      —   ptz.cgi (incl. jog start/stop)
│   │   └── dahua_manager.py
│   ├── sensors/              — Dahua event listener (multipart parser)
│   ├── alerts/               — two-stage AlertEngine
│   ├── evidence/             — packager, signing, manifest, recorder
│   ├── sync/                 — local intake queue → backend Sync Agent
│   ├── api/                  — Flask 5050 (dashboard) + 8443 (inbound)
│   │   ├── personnel_store   —   local person_id ↔ camera UID map
│   │   └── static/dashboard  —   HTML + vanilla JS (Test Console here)
│   └── utils/
└── tests/                    — 44 tests, all pass without GPU/ML deps
```

## License

Proprietary — Seismic Digital & Innovations Limited.
