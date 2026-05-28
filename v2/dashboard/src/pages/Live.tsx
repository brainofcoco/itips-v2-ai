import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchCameras, fetchZones, fireDeterrence, snapshotUrl, standdownDeterrence,
} from "../api/client";
import type { Camera, Zone } from "../api/types";
import { colorForZone, drawZone } from "../lib/zoneCanvas";
import PtzPanel from "../components/PtzPanel";

// Why snapshot polling instead of MJPEG:
// Each MJPEG <img src="/live/N"> holds one of the browser's six
// concurrent HTTP/1.1 slots open indefinitely. With four cameras plus
// the SSE event stream and other polls, the fourth tile silently
// starves — that's the cam4 bug. Snapshot polling fetches one short
// JPEG per camera per tick, reuses the connection pool, and works for
// any number of cameras.
const POLL_INTERVAL_MS = 250;

export default function Live() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showZones, setShowZones] = useState(true);
  const [controlledId, setControlledId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cams = await fetchCameras();
        if (!cancelled) {
          setCameras(cams);
          // Default the PTZ panel to the first PTZ-capable camera, else
          // the first camera, so the panel is usable on first load.
          if (cams.length && controlledId == null) {
            const ptz = cams.find((c) => c.ptz_connected !== false) ?? cams[0];
            setControlledId(ptz.camera_id);
          }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Live</h1>
        <div className="page-actions">
          <label className="switch">
            <input
              type="checkbox"
              checked={showZones}
              onChange={(e) => setShowZones(e.target.checked)}
            />
            <span className="switch-slider" />
            <span className="switch-label">Show zones</span>
          </label>
          <Link to="/zones" className="button-link">Edit zones →</Link>
        </div>
      </div>
      {error && <p className="alert-banner">Couldn’t load cameras: {error}</p>}
      {loading ? (
        <p className="muted">Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <p className="muted">No cameras configured.</p>
      ) : (
        <>
          <PtzPanel
            cameras={cameras}
            cameraId={controlledId}
            onSelectCamera={setControlledId}
          />
          <div className="cam-grid">
            {cameras.map((cam) => (
              <CameraTile
                key={cam.camera_id}
                cam={cam}
                showZones={showZones}
                isControlled={cam.camera_id === controlledId}
                onTakeControl={() => setControlledId(cam.camera_id)}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function CameraTile({
  cam, showZones, isControlled, onTakeControl,
}: {
  cam: Camera;
  showZones: boolean;
  isControlled: boolean;
  onTakeControl: () => void;
}) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const [busy, setBusy] = useState<"fire" | "stand" | null>(null);
  const [errored, setErrored] = useState(false);
  const [zones, setZones] = useState<Zone[]>([]);
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);

  // Snapshot polling: refresh the tile's src on a timer.
  // Pauses when the tab is hidden so we don't burn the camera's
  // snapshot endpoint while no one is looking.
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;

    const tick = () => {
      const img = imgRef.current;
      if (!img) return;
      img.src = snapshotUrl(cam.camera_id);
    };

    const start = () => {
      tick();
      timer = setInterval(tick, POLL_INTERVAL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
      const img = imgRef.current;
      if (img) img.removeAttribute("src");
    };

    if (!document.hidden) start();
    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVis);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [cam.camera_id]);

  // Load zones once per camera. Refresh on focus to pick up Zones-page edits
  // without a full reload.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const body = await fetchZones(cam.camera_id);
        if (!cancelled) setZones(body.available ? (body.zones ?? []) : []);
      } catch {
        if (!cancelled) setZones([]);
      }
    };
    load();
    const onFocus = () => { if (!document.hidden) load(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [cam.camera_id]);

  // Redraw overlay whenever zones, visibility, or image size changes.
  useEffect(() => {
    const c = overlayRef.current;
    if (!c) return;
    const w = imgSize?.w ?? 1280;
    const h = imgSize?.h ?? 720;
    if (c.width !== w) c.width = w;
    if (c.height !== h) c.height = h;
    const g = c.getContext("2d")!;
    g.clearRect(0, 0, w, h);
    if (!showZones) return;
    zones.forEach((z, idx) => {
      drawZone(g, z, w, h, {
        color: colorForZone(idx),
        width: Math.max(2, Math.round(w / 640) + 1),
        label: z.name || z.zone_id,
        fillAlpha: 0.15,
      });
    });
  }, [zones, showZones, imgSize]);

  const handleFire = useCallback(async () => {
    setBusy("fire");
    try {
      await fireDeterrence(cam.camera_id);
    } catch (e) {
      alert(String(e));
    } finally {
      setBusy(null);
    }
  }, [cam.camera_id]);

  const handleStand = useCallback(async () => {
    setBusy("stand");
    try {
      await standdownDeterrence(cam.camera_id);
    } catch (e) {
      alert(String(e));
    } finally {
      setBusy(null);
    }
  }, [cam.camera_id]);

  const zoneSummary = useMemo(() => {
    if (zones.length === 0) return null;
    const regions = zones.filter((z) => z.zone_type === "region").length;
    const lines = zones.filter((z) => z.zone_type === "line").length;
    const parts: string[] = [];
    if (regions) parts.push(`${regions} region${regions === 1 ? "" : "s"}`);
    if (lines) parts.push(`${lines} line${lines === 1 ? "" : "s"}`);
    return parts.join(" · ");
  }, [zones]);

  return (
    <article className={`cam${isControlled ? " cam-controlled" : ""}`}>
      <header>
        <h2>Camera {cam.camera_id} · {cam.endpoint}</h2>
        <span className={cam.workers_group_id ? "pill pill-ok" : "pill pill-idle"}>
          {cam.workers_group_id ? `face-group ${cam.workers_group_id}` : "no face-group"}
        </span>
      </header>
      <div className="feed">
        <img
          ref={imgRef}
          alt={`Camera ${cam.camera_id}`}
          onError={(e) => {
            const url = (e.currentTarget as HTMLImageElement).src;
            // eslint-disable-next-line no-console
            console.warn(`cam ${cam.camera_id} snapshot failed: ${url}`);
            setErrored(true);
          }}
          onLoad={(e) => {
            setErrored(false);
            const t = e.currentTarget;
            const w = t.naturalWidth, h = t.naturalHeight;
            if (w > 0 && h > 0 && (imgSize?.w !== w || imgSize?.h !== h)) {
              setImgSize({ w, h });
            }
          }}
        />
        <canvas ref={overlayRef} className="cam-overlay" aria-hidden />
        {showZones && zones.length > 0 && (
          <div className="cam-zone-legend">
            {zones.slice(0, 5).map((z, idx) => (
              <span key={z.zone_id} className="cam-zone-chip">
                <span className="dot" style={{ background: colorForZone(idx) }} />
                {z.name || z.zone_id}
              </span>
            ))}
            {zones.length > 5 && <span className="cam-zone-chip muted">+{zones.length - 5}</span>}
          </div>
        )}
        {errored && (
          <span className="placeholder">
            cam {cam.camera_id} snapshot failed — see browser console / itips logs
          </span>
        )}
      </div>
      <div className="controls">
        <button className="primary" disabled={busy !== null} onClick={handleFire}>
          {busy === "fire" ? "…" : "Test deterrence"}
        </button>
        <button disabled={busy !== null} onClick={handleStand}>
          {busy === "stand" ? "…" : "Stand down"}
        </button>
        {isControlled ? (
          <span className="pill pill-ok" title="PTZ panel above controls this camera">
            PTZ • controlling
          </span>
        ) : (
          <button
            onClick={onTakeControl}
            disabled={cam.ptz_connected === false}
            title={cam.ptz_connected === false ? "PTZ not available on this camera" : "Drive this camera from the PTZ panel"}
          >
            Control PTZ
          </button>
        )}
        <span className="spacer" />
        {zoneSummary && <span className="muted small">{zoneSummary}</span>}
        <span className={cam.ptz_connected ? "pill pill-ok" : "pill pill-idle"}>
          {cam.ptz_connected ? "PTZ ready" : "PTZ idle"}
        </span>
      </div>
    </article>
  );
}
