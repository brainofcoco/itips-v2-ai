import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteZone, fetchCameras, fetchCurrentPresets, fetchPresets, fetchZones,
  saveZone, snapshotUrl,
} from "../api/client";
import type {
  Camera, CameraPreset, Zone, ZoneDirection, ZoneType,
} from "../api/types";
import Section from "../components/Section";
import {
  colorForZone, drawHandles, drawInProgress, drawZone, findHandleAt, findSegmentAt,
  normFromPx, pxFromNorm, type Point,
} from "../lib/zoneCanvas";

type DraftMode = "new" | "edit";

interface Draft {
  mode: DraftMode;
  originalZoneId?: string;   // set in edit mode, used to detect rename collisions
  zone_id: string;
  zone_type: ZoneType;
  name: string;
  direction: ZoneDirection;
  points: Point[];
  preset_name: string;       // "" means always-active
}

const EMPTY_DRAFT: Draft = {
  mode: "new",
  zone_id: "",
  zone_type: "region",
  name: "",
  direction: "Any",
  points: [],
  preset_name: "",
};

export default function Zones() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraId, setCameraId] = useState<number | null>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draggingIdx, setDraggingIdx] = useState<number | null>(null);
  const [hoverHandle, setHoverHandle] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string>("crosshair");
  const [presets, setPresets] = useState<CameraPreset[]>([]);
  const [currentPreset, setCurrentPreset] = useState<string | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Load cameras once.
  useEffect(() => {
    fetchCameras().then((cs) => {
      setCameras(cs);
      if (cs.length) setCameraId(cs[0].camera_id);
    }).catch(console.error);
  }, []);

  // Load snapshot + zones when camera changes.
  useEffect(() => {
    if (cameraId == null) return;
    setImage(null);
    setZones([]);
    setDraft(null);
    setPresets([]);
    setCurrentPreset(null);
    const img = new Image();
    img.src = snapshotUrl(cameraId);
    img.onload = () => setImage(img);
    img.onerror = () => setImage(null);
    fetchZones(cameraId)
      .then((body) => {
        if (!body.available) {
          setStatus({ text: "ML zone store not wired — zones disabled.", ok: false });
          setZones([]);
        } else {
          setStatus(null);
          setZones(body.zones ?? []);
        }
      })
      .catch((e) => {
        setStatus({ text: "Failed to load zones: " + e, ok: false });
        setZones([]);
      });
    // Preset list for the "Active when at preset" dropdown.
    fetchPresets(cameraId)
      .then((body) => setPresets(body.presets ?? []))
      .catch(() => setPresets([]));
    fetchCurrentPresets()
      .then((body) => setCurrentPreset(body.cameras?.[String(cameraId)] ?? null))
      .catch(() => setCurrentPreset(null));
  }, [cameraId]);

  const refreshZones = useCallback(async () => {
    if (cameraId == null) return;
    try {
      const body = await fetchZones(cameraId);
      setZones(body.zones ?? []);
    } catch (e) {
      setStatus({ text: "Failed to reload zones: " + e, ok: false });
    }
  }, [cameraId]);

  // Render everything on every state change.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const W = image?.naturalWidth || 1280;
    const H = image?.naturalHeight || 720;
    if (c.width !== W) c.width = W;
    if (c.height !== H) c.height = H;
    const g = c.getContext("2d")!;
    g.clearRect(0, 0, W, H);
    if (image) {
      g.drawImage(image, 0, 0, W, H);
    } else {
      g.fillStyle = "#0a0d13";
      g.fillRect(0, 0, W, H);
      g.fillStyle = "#3a4256";
      g.font = "14px system-ui, sans-serif";
      g.textAlign = "center";
      g.fillText("Snapshot unavailable — you can still draw zones", W / 2, H / 2);
      g.textAlign = "start";
    }
    const editingId = draft?.mode === "edit" ? draft.originalZoneId : undefined;
    zones.forEach((z, idx) => {
      if (z.zone_id === editingId) return;   // editing copy is drawn instead
      drawZone(g, z, W, H, {
        color: colorForZone(idx),
        width: Math.max(2, Math.round(W / 640)),
        label: z.name || z.zone_id,
        fillAlpha: 0.13,
      });
    });
    if (draft) {
      const editIdx = editingId
        ? zones.findIndex((z) => z.zone_id === editingId)
        : zones.length;
      const color = colorForZone(editIdx === -1 ? zones.length : editIdx);
      const previewZone: Zone = {
        zone_id: draft.zone_id || "(new)",
        zone_type: draft.zone_type,
        name: draft.name,
        direction: draft.direction,
        points: draft.points,
      };
      if (draft.points.length >= (draft.zone_type === "region" ? 3 : 2)) {
        drawZone(g, previewZone, W, H, {
          color,
          width: Math.max(2, Math.round(W / 640) + 1),
          label: draft.name || draft.zone_id || (draft.mode === "edit" ? "(editing)" : "(new)"),
          fillAlpha: 0.22,
        });
      } else {
        drawInProgress(
          g, draft.points, W, H, draft.zone_type, draft.direction,
        );
      }
      drawHandles(g, draft.points.map((p) => pxFromNorm(p, W, H)), color);
    }
  }, [image, zones, draft]);

  // Mouse → canvas coordinates, in the canvas's internal pixel space.
  const canvasXY = useCallback((evt: { clientX: number; clientY: number }): [number, number] | null => {
    const c = canvasRef.current;
    if (!c) return null;
    const rect = c.getBoundingClientRect();
    return [
      (evt.clientX - rect.left) * (c.width / rect.width),
      (evt.clientY - rect.top) * (c.height / rect.height),
    ];
  }, []);

  const handlesPx = useCallback((): Point[] => {
    const c = canvasRef.current;
    if (!c || !draft) return [];
    return draft.points.map((p) => pxFromNorm(p, c.width, c.height));
  }, [draft]);

  const onCanvasMouseDown = useCallback((evt: React.MouseEvent<HTMLCanvasElement>) => {
    if (evt.button !== 0) return;     // left only
    if (!draft || cameraId == null) return;
    const xy = canvasXY(evt);
    if (!xy) return;
    const [x, y] = xy;
    const hIdx = findHandleAt(handlesPx(), x, y);
    if (hIdx !== -1) {
      setDraggingIdx(hIdx);
      evt.preventDefault();
    }
  }, [draft, cameraId, canvasXY, handlesPx]);

  const onCanvasMouseMove = useCallback((evt: React.MouseEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current;
    if (!c) return;
    const xy = canvasXY(evt);
    if (!xy) return;
    const [x, y] = xy;
    if (draggingIdx !== null && draft) {
      const W = c.width, H = c.height;
      const np = normFromPx(Math.max(0, Math.min(W, x)), Math.max(0, Math.min(H, y)), W, H);
      setDraft({
        ...draft,
        points: draft.points.map((p, i) => (i === draggingIdx ? np : p)),
      });
      return;
    }
    if (!draft) {
      setCursor("crosshair");
      setHoverHandle(null);
      return;
    }
    const hpx = handlesPx();
    const hIdx = findHandleAt(hpx, x, y);
    setHoverHandle(hIdx === -1 ? null : hIdx);
    if (hIdx !== -1) { setCursor("grab"); return; }
    const closed = draft.zone_type === "region" && draft.points.length >= 3;
    const seg = findSegmentAt(hpx, x, y, closed);
    setCursor(seg ? "copy" : "crosshair");
  }, [canvasXY, draft, draggingIdx, handlesPx]);

  const onCanvasMouseUp = useCallback(() => {
    if (draggingIdx !== null) setDraggingIdx(null);
  }, [draggingIdx]);

  const onCanvasClick = useCallback((evt: React.MouseEvent<HTMLCanvasElement>) => {
    if (draggingIdx !== null) return;       // drag-release, not a click
    const c = canvasRef.current;
    if (!c || cameraId == null) return;
    const xy = canvasXY(evt);
    if (!xy) return;
    const [x, y] = xy;

    // No draft → clicking a zone selects it for editing.
    if (!draft) {
      const idx = zoneUnderPoint(zones, x / c.width, y / c.height);
      if (idx !== -1) {
        beginEdit(zones[idx]);
      } else {
        beginNew([normFromPx(x, y, c.width, c.height)]);
      }
      return;
    }

    // Have a draft: avoid double-acting if mousedown ate the event for drag.
    const hpx = handlesPx();
    if (findHandleAt(hpx, x, y) !== -1) return;
    const closed = draft.zone_type === "region" && draft.points.length >= 3;
    const seg = findSegmentAt(hpx, x, y, closed);
    if (seg) {
      const np = normFromPx(x, y, c.width, c.height);
      setDraft({
        ...draft,
        points: insertAt(draft.points, seg.insertAt, np),
      });
      return;
    }
    // Append vertex.
    const np = normFromPx(x, y, c.width, c.height);
    setDraft({ ...draft, points: [...draft.points, np] });
  }, [canvasXY, cameraId, draft, draggingIdx, handlesPx, zones]);

  const onCanvasContext = useCallback((evt: React.MouseEvent<HTMLCanvasElement>) => {
    if (!draft) return;
    evt.preventDefault();
    const xy = canvasXY(evt);
    if (!xy) return;
    const [x, y] = xy;
    const hIdx = findHandleAt(handlesPx(), x, y);
    if (hIdx === -1) return;
    const minPts = draft.zone_type === "region" ? 3 : 2;
    if (draft.points.length <= minPts) {
      setStatus({ text: `Can't remove — ${draft.zone_type} needs at least ${minPts} points.`, ok: false });
      return;
    }
    setDraft({
      ...draft,
      points: draft.points.filter((_, i) => i !== hIdx),
    });
  }, [canvasXY, draft, handlesPx]);

  const beginNew = useCallback((seedPoints: Point[] = []) => {
    setDraft({ ...EMPTY_DRAFT, points: seedPoints });
    setStatus(null);
  }, []);

  const beginEdit = useCallback((z: Zone) => {
    setDraft({
      mode: "edit",
      originalZoneId: z.zone_id,
      zone_id: z.zone_id,
      zone_type: z.zone_type,
      name: z.name ?? "",
      direction: z.direction ?? "Any",
      points: z.points.map((p) => [p[0], p[1]] as Point),
      preset_name: z.preset_name ?? "",
    });
    setStatus({ text: `Editing "${z.zone_id}" — drag handles, right-click to remove, click segments to insert.`, ok: true });
  }, []);

  const onCancelDraft = useCallback(() => {
    setDraft(null);
    setStatus(null);
  }, []);

  const onUndoLastPoint = useCallback(() => {
    if (!draft || draft.points.length === 0) return;
    setDraft({ ...draft, points: draft.points.slice(0, -1) });
  }, [draft]);

  const onClearPoints = useCallback(() => {
    if (!draft) return;
    setDraft({ ...draft, points: [] });
  }, [draft]);

  const onSave = useCallback(async () => {
    if (!draft || cameraId == null) return;
    if (!draft.zone_id.trim()) {
      setStatus({ text: "Zone ID is required.", ok: false });
      return;
    }
    const minPts = draft.zone_type === "region" ? 3 : 2;
    if (draft.points.length < minPts) {
      setStatus({ text: `${draft.zone_type} needs at least ${minPts} points.`, ok: false });
      return;
    }
    if (
      draft.mode === "new" &&
      zones.some((z) => z.zone_id === draft.zone_id.trim())
    ) {
      setStatus({ text: `Zone ID "${draft.zone_id.trim()}" already exists.`, ok: false });
      return;
    }
    try {
      // If they renamed an existing zone, delete the old one after the new one upserts.
      const renamed =
        draft.mode === "edit" &&
        draft.originalZoneId &&
        draft.originalZoneId !== draft.zone_id.trim();
      const reply = await saveZone(cameraId, {
        zone_id: draft.zone_id.trim(),
        zone_type: draft.zone_type,
        name: draft.name.trim(),
        direction: draft.zone_type === "line" ? draft.direction : "Any",
        points: draft.points,
        preset_name: draft.preset_name.trim() || null,
      });
      if (!reply.ok) {
        setStatus({ text: "Save failed: " + (reply.error || "unknown"), ok: false });
        return;
      }
      if (renamed && draft.originalZoneId) {
        await deleteZone(cameraId, draft.originalZoneId);
      }
      setStatus({
        text: draft.mode === "edit" ? `Updated "${draft.zone_id.trim()}".` : `Saved "${draft.zone_id.trim()}".`,
        ok: true,
      });
      setDraft(null);
      await refreshZones();
    } catch (e) {
      setStatus({ text: "Save failed: " + e, ok: false });
    }
  }, [cameraId, draft, refreshZones, zones]);

  const onDelete = useCallback(async (zid: string) => {
    if (cameraId == null) return;
    if (!confirm(`Delete zone "${zid}"?`)) return;
    try {
      const r = await deleteZone(cameraId, zid);
      if (!r.ok) { setStatus({ text: "Delete failed: " + (r.error || "unknown"), ok: false }); return; }
      setStatus({ text: `Deleted "${zid}".`, ok: true });
      if (draft?.mode === "edit" && draft.originalZoneId === zid) setDraft(null);
      await refreshZones();
    } catch (e) {
      setStatus({ text: "Delete failed: " + e, ok: false });
    }
  }, [cameraId, draft, refreshZones]);

  const hint = useMemo(() => {
    if (!draft) {
      if (zones.length === 0) return "Click on the snapshot to start drawing a zone.";
      return "Click an existing zone to edit it — or click anywhere empty to start a new one.";
    }
    const minPts = draft.zone_type === "region" ? 3 : 2;
    if (draft.points.length < minPts) {
      const remain = minPts - draft.points.length;
      return `Click to add ${remain} more point${remain === 1 ? "" : "s"} (${minPts} min for ${draft.zone_type}).`;
    }
    return draft.mode === "edit"
      ? "Drag any handle to move it · click an edge to add a vertex · right-click a handle to remove."
      : "Add more points, drag to refine, then press Save zone.";
  }, [draft, zones]);

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Zones</h1>
        <div className="page-actions">
          <select
            value={cameraId ?? ""}
            onChange={(e) => setCameraId(parseInt(e.target.value, 10))}
            style={{ width: "auto", minWidth: 180 }}
          >
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                cam{c.camera_id} · {c.endpoint}
              </option>
            ))}
            {cameras.length === 0 && <option value="">(no cameras)</option>}
          </select>
          {!draft ? (
            <button className="primary" onClick={() => beginNew()}>+ New zone</button>
          ) : (
            <button onClick={onCancelDraft}>Cancel</button>
          )}
        </div>
      </div>

      <div className="zones-layout">
        <Section
          title="Snapshot"
          actions={
            <span className="muted small" title="ITIPS-tracked PTZ orientation">
              camera at:&nbsp;
              <strong style={{ color: currentPreset ? "var(--accent)" : "var(--muted)" }}>
                {currentPreset ?? "unknown preset"}
              </strong>
            </span>
          }
        >
          <div className="canvas-wrap">
            <canvas
              ref={canvasRef}
              style={{ cursor }}
              onClick={onCanvasClick}
              onMouseDown={onCanvasMouseDown}
              onMouseMove={onCanvasMouseMove}
              onMouseUp={onCanvasMouseUp}
              onMouseLeave={() => { setHoverHandle(null); setDraggingIdx(null); }}
              onContextMenu={onCanvasContext}
            />
            <div className="canvas-hint">{hint}</div>
            {draft && (
              <div className="canvas-toolbar">
                <button onClick={onUndoLastPoint} disabled={draft.points.length === 0}>
                  ↶ Undo point
                </button>
                <button onClick={onClearPoints} disabled={draft.points.length === 0}>
                  Clear points
                </button>
                <span className="muted small">{draft.points.length} pts{hoverHandle !== null ? ` · point ${hoverHandle + 1}` : ""}</span>
              </div>
            )}
          </div>
          {status && (
            <div
              className={status.ok ? "status-banner status-ok" : "alert-banner"}
              style={{ marginTop: "0.6rem" }}
            >
              {status.text}
            </div>
          )}
        </Section>

        <div className="zones-side">
          {draft && (
            <Section title={draft.mode === "edit" ? `Edit zone` : `New zone`}>
              <div className="form-grid">
                <label className="field">
                  <span>Zone ID</span>
                  <input
                    value={draft.zone_id}
                    onChange={(e) => setDraft({ ...draft, zone_id: e.target.value })}
                    placeholder="e.g. yard_north"
                  />
                </label>
                <label className="field">
                  <span>Name (optional)</span>
                  <input
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    placeholder="Friendly label"
                  />
                </label>
                <label className="field">
                  <span>Type</span>
                  <select
                    value={draft.zone_type}
                    onChange={(e) => setDraft({ ...draft, zone_type: e.target.value as ZoneType })}
                  >
                    <option value="region">Region — intrusion / loitering</option>
                    <option value="line">Line — crossing trip-wire</option>
                  </select>
                </label>
                <label className="field">
                  <span>Direction (line only)</span>
                  <select
                    disabled={draft.zone_type !== "line"}
                    value={draft.direction}
                    onChange={(e) => setDraft({ ...draft, direction: e.target.value as ZoneDirection })}
                  >
                    <option value="Any">Any direction</option>
                    <option value="LeftToRight">Left → Right</option>
                    <option value="RightToLeft">Right → Left</option>
                  </select>
                </label>
                <label className="field" style={{ gridColumn: "1 / -1" }}>
                  <span>Active when at preset</span>
                  <select
                    value={draft.preset_name}
                    onChange={(e) => setDraft({ ...draft, preset_name: e.target.value })}
                  >
                    <option value="">Always active (no preset binding)</option>
                    {presets.map((p) => (
                      <option key={p.index} value={p.name}>
                        #{p.index} · {p.name}
                        {currentPreset === p.name ? "  (camera currently here)" : ""}
                      </option>
                    ))}
                  </select>
                  <span className="muted small">
                    PTZ cameras: bind the zone to the preset it was drawn under, so the
                    engine only evaluates and the Live overlay only draws it while the
                    camera is at that view.
                  </span>
                </label>
              </div>
              <div className="row" style={{ marginTop: "0.6rem", justifyContent: "flex-end" }}>
                <button onClick={onCancelDraft}>Cancel</button>
                <button className="primary" onClick={onSave}>
                  {draft.mode === "edit" ? "Save changes" : "Create zone"}
                </button>
              </div>
            </Section>
          )}

          <Section
            title={`Zones (${zones.length})`}
            actions={!draft && (
              <button className="primary small" onClick={() => beginNew()}>+ New</button>
            )}
          >
            {zones.length === 0 ? (
              <p className="muted">No zones yet for this camera. Click the snapshot or press <strong>+ New zone</strong>.</p>
            ) : (
              <ul className="zone-cards">
                {zones.map((z, idx) => {
                  const color = colorForZone(idx);
                  const isEditing = draft?.mode === "edit" && draft.originalZoneId === z.zone_id;
                  return (
                    <li key={z.zone_id} className={`zone-card${isEditing ? " editing" : ""}`}>
                      <span className="zone-card-swatch" style={{ background: color }} />
                      <div className="zone-card-body">
                        <div className="zone-card-title">
                          <span className="kind">{z.name || z.zone_id}</span>
                          {z.name && <span className="muted small">({z.zone_id})</span>}
                        </div>
                        <div className="zone-card-meta">
                          <span className="pill" style={{ background: color + "26", color }}>
                            {z.zone_type}
                          </span>
                          {z.zone_type === "line" && (
                            <span className="muted small">{labelDirection(z.direction)}</span>
                          )}
                          <span className="muted small">{z.points.length} pts</span>
                          {z.preset_name ? (
                            <span
                              className={`pill ${currentPreset === z.preset_name ? "pill-ok" : "pill-idle"}`}
                              title={
                                currentPreset === z.preset_name
                                  ? "Camera is at this preset — zone is live"
                                  : "Zone is dormant — camera is at a different view"
                              }
                            >
                              @ {z.preset_name}
                            </span>
                          ) : (
                            <span className="pill pill-ok" title="Always active">always</span>
                          )}
                        </div>
                      </div>
                      <div className="zone-card-actions">
                        {isEditing ? (
                          <span className="pill pill-ok">editing</span>
                        ) : (
                          <button onClick={() => beginEdit(z)} disabled={!!draft}>Edit</button>
                        )}
                        <button onClick={() => onDelete(z.zone_id)} disabled={isEditing}>
                          Delete
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

// ─── helpers ───────────────────────────────────────────────────────

function insertAt<T>(arr: T[], i: number, v: T): T[] {
  const out = arr.slice();
  out.splice(i, 0, v);
  return out;
}

function labelDirection(d?: ZoneDirection): string {
  if (d === "LeftToRight") return "Left → Right";
  if (d === "RightToLeft") return "Right → Left";
  return "Any direction";
}

// Returns the index of the topmost zone under a normalised (0..1) point,
// or -1. Regions use point-in-polygon; lines use distance to segment.
function zoneUnderPoint(zones: Zone[], nx: number, ny: number): number {
  for (let i = zones.length - 1; i >= 0; i--) {
    const z = zones[i];
    if (z.zone_type === "region") {
      if (pointInPolygon(nx, ny, z.points as Point[])) return i;
    } else if (z.zone_type === "line") {
      // ~1.5% of the frame width tolerance.
      if (distanceToPolyline(nx, ny, z.points as Point[]) < 0.015) return i;
    }
  }
  return -1;
}

function pointInPolygon(x: number, y: number, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    const intersect = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function distanceToPolyline(x: number, y: number, pts: Point[]): number {
  if (pts.length < 2) return Infinity;
  let best = Infinity;
  for (let i = 0; i < pts.length - 1; i++) {
    const [ax, ay] = pts[i], [bx, by] = pts[i + 1];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 === 0 ? 0 : ((x - ax) * dx + (y - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const qx = ax + t * dx, qy = ay + t * dy;
    best = Math.min(best, Math.hypot(x - qx, y - qy));
  }
  return best;
}
