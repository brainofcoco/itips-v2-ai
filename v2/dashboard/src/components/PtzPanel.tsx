import { useCallback, useEffect, useRef, useState } from "react";
import {
  deletePreset, fetchPresets, gotoPreset, ptzJog, saveCurrentPreset,
  type PtzDirection,
} from "../api/client";
import type { Camera, CameraPreset } from "../api/types";

interface Props {
  cameras: Camera[];
  cameraId: number | null;
  onSelectCamera: (id: number) => void;
}

// Press-and-hold PTZ panel. One direction is active at a time — switching
// directions or losing the pointer always sends a `stop` for whatever was
// last started so the camera never "runs away" off-screen.
export default function PtzPanel({ cameras, cameraId, onSelectCamera }: Props) {
  const [speed, setSpeed] = useState(4);
  const [presets, setPresets] = useState<CameraPreset[]>([]);
  const [presetIndex, setPresetIndex] = useState<number | "">("");
  const [newPresetName, setNewPresetName] = useState("");
  const [busy, setBusy] = useState<"goto" | "save" | "delete" | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // Track which direction is currently jogging so we can guarantee a
  // matching stop even if the pointer leaves the button or the user
  // switches tabs.
  const activeDirRef = useRef<PtzDirection | null>(null);
  const cameraRef = useRef<number | null>(cameraId);
  cameraRef.current = cameraId;

  const reloadPresets = useCallback(async (id: number) => {
    try {
      const body = await fetchPresets(id);
      setPresets(body.presets ?? []);
    } catch {
      setPresets([]);
    }
  }, []);

  useEffect(() => {
    setPresetIndex("");
    setStatus(null);
    if (cameraId == null) { setPresets([]); return; }
    reloadPresets(cameraId);
  }, [cameraId, reloadPresets]);

  const stopActive = useCallback(async () => {
    const dir = activeDirRef.current;
    const cam = cameraRef.current;
    if (dir == null || cam == null) return;
    activeDirRef.current = null;
    try { await ptzJog(cam, dir, "stop", speed); } catch { /* swallow */ }
  }, [speed]);

  // Safety net: always stop on tab hide and on unmount.
  useEffect(() => {
    const onHide = () => { if (document.hidden) stopActive(); };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("blur", stopActive);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("blur", stopActive);
      stopActive();
    };
  }, [stopActive]);

  const start = useCallback(async (dir: PtzDirection) => {
    if (cameraId == null) return;
    // If a different direction is still active, stop it first so the camera
    // doesn't double-up commands.
    if (activeDirRef.current && activeDirRef.current !== dir) {
      const prev = activeDirRef.current;
      activeDirRef.current = null;
      try { await ptzJog(cameraId, prev, "stop", speed); } catch { /* swallow */ }
    }
    activeDirRef.current = dir;
    try {
      const r = await ptzJog(cameraId, dir, "start", speed);
      if (!r.ok) setStatus(`PTZ rejected (${dir}): ${r.error ?? "unknown"}`);
      else setStatus(null);
    } catch (e) {
      setStatus(`PTZ failed: ${e}`);
    }
  }, [cameraId, speed]);

  const stop = useCallback(async (dir: PtzDirection) => {
    if (activeDirRef.current !== dir) return;   // wasn't ours
    activeDirRef.current = null;
    if (cameraId == null) return;
    try { await ptzJog(cameraId, dir, "stop", speed); } catch { /* swallow */ }
  }, [cameraId, speed]);

  const holdProps = (dir: PtzDirection) => ({
    onPointerDown: (e: React.PointerEvent) => {
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      e.preventDefault();
      start(dir);
    },
    onPointerUp:     () => stop(dir),
    onPointerLeave:  () => stop(dir),
    onPointerCancel: () => stop(dir),
    onContextMenu:   (e: React.MouseEvent) => e.preventDefault(),
  });

  // Keyboard arrows + +/- while panel has focus.
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.repeat) return;
    const k = e.key;
    const map: Record<string, PtzDirection> = {
      ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
      "+": "zoom_tele", "=": "zoom_tele", "-": "zoom_wide", "_": "zoom_wide",
    };
    const dir = map[k];
    if (!dir) return;
    e.preventDefault();
    start(dir);
  }, [start]);
  const onKeyUp = useCallback((e: React.KeyboardEvent) => {
    const map: Record<string, PtzDirection> = {
      ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
      "+": "zoom_tele", "=": "zoom_tele", "-": "zoom_wide", "_": "zoom_wide",
    };
    const dir = map[e.key];
    if (!dir) return;
    stop(dir);
  }, [stop]);

  const onGotoPreset = useCallback(async () => {
    if (cameraId == null || presetIndex === "") return;
    setBusy("goto");
    try {
      const r = await gotoPreset(cameraId, presetIndex);
      if (!r.ok) setStatus(`Goto failed: ${r.error ?? "unknown"}`);
      else setStatus(null);
    } finally {
      setBusy(null);
    }
  }, [cameraId, presetIndex]);

  const onSavePreset = useCallback(async () => {
    if (cameraId == null) return;
    const name = newPresetName.trim();
    if (!name) { setStatus("Give the preset a name."); return; }
    if (presets.some((p) => p.name === name) &&
        !confirm(`Preset "${name}" already exists — overwrite?`)) {
      return;
    }
    setBusy("save");
    try {
      const r = await saveCurrentPreset(cameraId, name);
      if (!r.ok || !r.preset) {
        setStatus(`Save failed: ${r.error ?? "unknown"}`);
        return;
      }
      await reloadPresets(cameraId);
      setPresetIndex(r.preset.index);
      setNewPresetName("");
      // Camera may keep its auto-name (firmware quirk). Tell the operator.
      if (r.name_warning) {
        setStatus(r.name_warning);
      } else {
        setStatus(`Saved preset "${r.preset.name}" (#${r.preset.index}).`);
      }
    } finally {
      setBusy(null);
    }
  }, [cameraId, newPresetName, presets, reloadPresets]);

  const onDeletePreset = useCallback(async () => {
    if (cameraId == null || presetIndex === "") return;
    const p = presets.find((x) => x.index === presetIndex);
    const label = p ? `"${p.name}" (#${p.index})` : `preset #${presetIndex}`;
    if (!confirm(`Delete ${label} from cam${cameraId}? This removes it from the camera.`)) return;
    setBusy("delete");
    try {
      const r = await deletePreset(cameraId, presetIndex);
      if (!r.ok) { setStatus(`Delete failed: ${r.error ?? "unknown"}`); return; }
      await reloadPresets(cameraId);
      setPresetIndex("");
      setStatus(`Deleted ${label}.`);
    } finally {
      setBusy(null);
    }
  }, [cameraId, presetIndex, presets, reloadPresets]);

  const disabled = cameraId == null;

  return (
    <section
      className="ptz-panel"
      tabIndex={0}
      onKeyDown={onKeyDown}
      onKeyUp={onKeyUp}
      aria-label="Camera PTZ controls"
    >
      <div className="ptz-head">
        <span className="ptz-title">PTZ</span>
        <label className="inline">
          <span className="muted small">camera</span>
          <select
            value={cameraId ?? ""}
            onChange={(e) => onSelectCamera(parseInt(e.target.value, 10))}
          >
            {cameras.length === 0 && <option value="">(no cameras)</option>}
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                cam{c.camera_id} · {c.endpoint}
                {c.ptz_connected === false ? " (no PTZ)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="inline ptz-speed">
          <span className="muted small">speed {speed}</span>
          <input
            type="range" min={1} max={8} step={1}
            value={speed}
            onChange={(e) => setSpeed(parseInt(e.target.value, 10))}
          />
        </label>
        {status && <span className="muted small ptz-status">{status}</span>}
      </div>

      <div className="ptz-grid">
        <div className="dpad" aria-label="Pan / tilt">
          <button className="dpad-btn nw" disabled={disabled} {...holdProps("left_up")} aria-label="up-left">↖</button>
          <button className="dpad-btn n"  disabled={disabled} {...holdProps("up")}      aria-label="up">▲</button>
          <button className="dpad-btn ne" disabled={disabled} {...holdProps("right_up")} aria-label="up-right">↗</button>
          <button className="dpad-btn w"  disabled={disabled} {...holdProps("left")}    aria-label="left">◀</button>
          <button className="dpad-btn c"  disabled={disabled}
                  onClick={onGotoPreset}
                  title="Go to selected preset"
                  aria-label="go to preset">⌂</button>
          <button className="dpad-btn e"  disabled={disabled} {...holdProps("right")}   aria-label="right">▶</button>
          <button className="dpad-btn sw" disabled={disabled} {...holdProps("left_down")}  aria-label="down-left">↙</button>
          <button className="dpad-btn s"  disabled={disabled} {...holdProps("down")}       aria-label="down">▼</button>
          <button className="dpad-btn se" disabled={disabled} {...holdProps("right_down")} aria-label="down-right">↘</button>
        </div>

        <div className="ptz-zoom">
          <button className="zoom-btn" disabled={disabled} {...holdProps("zoom_tele")} aria-label="zoom in">
            <span className="zoom-glyph">+</span>
            <span className="zoom-label">zoom in</span>
          </button>
          <button className="zoom-btn" disabled={disabled} {...holdProps("zoom_wide")} aria-label="zoom out">
            <span className="zoom-glyph">−</span>
            <span className="zoom-label">zoom out</span>
          </button>
        </div>

        <div className="ptz-presets">
          <div className="ptz-preset-row">
            <select
              value={presetIndex === "" ? "" : String(presetIndex)}
              onChange={(e) => setPresetIndex(e.target.value === "" ? "" : parseInt(e.target.value, 10))}
              disabled={disabled || presets.length === 0}
            >
              <option value="">— preset —</option>
              {presets.map((p) => (
                <option key={p.index} value={p.index}>#{p.index} · {p.name}</option>
              ))}
            </select>
            <button
              disabled={disabled || presetIndex === "" || busy === "goto"}
              onClick={onGotoPreset}
            >
              {busy === "goto" ? "…" : "Go"}
            </button>
            <button
              disabled={disabled || presetIndex === "" || busy === "delete"}
              onClick={onDeletePreset}
              title="Delete this preset from the camera"
            >
              {busy === "delete" ? "…" : "Delete"}
            </button>
          </div>
          <div className="ptz-preset-row">
            <input
              type="text"
              value={newPresetName}
              onChange={(e) => setNewPresetName(e.target.value)}
              placeholder="Save current as…"
              disabled={disabled}
            />
            <button
              disabled={disabled || !newPresetName.trim() || busy === "save"}
              onClick={onSavePreset}
            >
              {busy === "save" ? "…" : "Save"}
            </button>
          </div>
          <span className="muted small ptz-hint">
            Hold a button to move · arrow keys + / − also work
          </span>
        </div>
      </div>
    </section>
  );
}
