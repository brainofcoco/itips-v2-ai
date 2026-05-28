import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchCameras, fetchCurrentPresets, fetchRecentActivity, fetchZones,
  fireDeterrence, snapshotUrl, standdownDeterrence,
} from "../api/client";
import type { ActivityEvent, Camera, Zone } from "../api/types";

// How long a detection badge stays on a tile after it was published.
const ACTIVITY_TTL_MS = 12000;
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
  const [currentPresets, setCurrentPresets] = useState<Record<string, string | null>>({});
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  // Bumped every second so stale badges (past their TTL) re-evaluate and
  // disappear even when no new activity is arriving.
  const [, setNow] = useState(Date.now());

  // Poll the real-time detection feed. Fast enough that a line cross /
  // sensor trip shows on the tile within ~1s of happening.
  useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      try {
        const body = await fetchRecentActivity(50);
        if (!cancelled) setActivity(body.available ? (body.events ?? []) : []);
      } catch {
        if (!cancelled) setActivity([]);
      }
    };
    pull();
    const poll = setInterval(() => { if (!document.hidden) pull(); }, 1000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { cancelled = true; clearInterval(poll); clearInterval(tick); };
  }, []);

  // Group fresh (within TTL) events by camera, newest first.
  const activityByCamera = useMemo(() => {
    const cutoff = Date.now() - ACTIVITY_TTL_MS;
    const out: Record<number, ActivityEvent[]> = {};
    for (const e of activity) {
      if (e.ts * 1000 < cutoff) continue;
      (out[e.camera_id] ??= []).push(e);
    }
    return out;
  }, [activity]);

  // Refresh per-camera current-preset every 2s so zone overlays come live
  // immediately after a goto/jog. The PtzPanel + sensor goto routes all
  // call into preset_state server-side; we just poll the result.
  useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      try {
        const body = await fetchCurrentPresets();
        if (!cancelled) setCurrentPresets(body.cameras ?? {});
      } catch {
        if (!cancelled) setCurrentPresets({});
      }
    };
    pull();
    const t = setInterval(() => { if (!document.hidden) pull(); }, 2000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

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
                currentPreset={currentPresets[String(cam.camera_id)] ?? null}
                activity={activityByCamera[cam.camera_id] ?? []}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function CameraTile({
  cam, showZones, isControlled, onTakeControl, currentPreset, activity,
}: {
  cam: Camera;
  showZones: boolean;
  isControlled: boolean;
  onTakeControl: () => void;
  currentPreset: string | null;
  activity: ActivityEvent[];
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

  // Zones the engine actually evaluates right now: always-active ones,
  // plus those bound to whatever preset the camera is currently at.
  // Preserves the original index so colour assignment stays stable as
  // zones come and go between presets.
  const activeZones = useMemo(() => {
    return zones
      .map((z, originalIdx) => ({ z, originalIdx }))
      .filter(({ z }) => !z.preset_name || z.preset_name === currentPreset);
  }, [zones, currentPreset]);

  // Redraw overlay whenever active zones, visibility, or image size changes.
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
    activeZones.forEach(({ z, originalIdx }) => {
      drawZone(g, z, w, h, {
        color: colorForZone(originalIdx),
        width: Math.max(2, Math.round(w / 640) + 1),
        label: z.name || z.zone_id,
        fillAlpha: 0.15,
      });
    });
  }, [activeZones, showZones, imgSize]);

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
    const total = zones.length;
    const active = activeZones.length;
    if (active === total) return `${total} zone${total === 1 ? "" : "s"} active`;
    return `${active}/${total} zone${total === 1 ? "" : "s"} active`;
  }, [zones, activeZones]);

  const dormantCount = zones.length - activeZones.length;
  const presetBanner = useMemo(() => {
    if (zones.length === 0) return null;
    if (currentPreset) {
      return {
        text: dormantCount > 0
          ? `at preset “${currentPreset}” — ${activeZones.length} active, ${dormantCount} hidden`
          : `at preset “${currentPreset}” — ${activeZones.length} active`,
        tone: "ok" as const,
      };
    }
    // No preset known: any preset-bound zones stay hidden so we don't
    // evaluate against the wrong view.
    const hasBound = zones.some((z) => !!z.preset_name);
    if (!hasBound) {
      return { text: `${zones.length} zone${zones.length === 1 ? "" : "s"} (always active)`, tone: "ok" as const };
    }
    return {
      text: `preset unknown — ${dormantCount} preset-bound zone${dormantCount === 1 ? "" : "s"} hidden`,
      tone: "warn" as const,
    };
  }, [zones, activeZones, currentPreset, dormantCount]);

  const hasAlert = activity.length > 0;
  return (
    <article className={`cam${isControlled ? " cam-controlled" : ""}${hasAlert ? " cam-alerting" : ""}`}>
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
        {showZones && presetBanner && (
          <div className={`cam-preset-banner cam-preset-${presetBanner.tone}`}>
            {presetBanner.text}
          </div>
        )}
        {showZones && activeZones.length > 0 && (
          <div className="cam-zone-legend">
            {activeZones.slice(0, 5).map(({ z, originalIdx }) => (
              <span key={z.zone_id} className="cam-zone-chip">
                <span className="dot" style={{ background: colorForZone(originalIdx) }} />
                {z.name || z.zone_id}
              </span>
            ))}
            {activeZones.length > 5 && (
              <span className="cam-zone-chip muted">+{activeZones.length - 5}</span>
            )}
          </div>
        )}
        {activity.length > 0 && (
          <div className="cam-activity">
            {activity.slice(0, 3).map((e) => (
              <span key={e.seq} className={`cam-activity-badge act-${activityTone(e.kind)}`}>
                <span className="act-glyph">{activityGlyph(e.kind)}</span>
                <span className="act-text">
                  {e.label}{e.zone_name && e.kind !== "sensor" ? ` · ${e.zone_name}` : ""}
                </span>
                <span className="act-status">{e.status === "evaluating" ? "evaluating…" : e.status}</span>
              </span>
            ))}
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

// ─── activity badge helpers ───────────────────────────────────────
function activityGlyph(kind: string): string {
  switch (kind) {
    case "line_crossing": return "⊘";
    case "intrusion":     return "⚠";
    case "loitering":     return "◷";
    case "sensor":        return "◉";
    default:              return "•";
  }
}
function activityTone(kind: string): "bad" | "warn" | "info" {
  if (kind === "intrusion" || kind === "line_crossing") return "bad";
  if (kind === "loitering") return "warn";
  return "info";   // sensor + anything else
}
