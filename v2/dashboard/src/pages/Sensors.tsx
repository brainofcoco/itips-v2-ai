import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteSensorMapping, fetchCameras, fetchHubState, fetchListenerStatus, fetchPresets,
  fetchRecentSensorEvents, fetchSensorMappings, fetchSirenStatus, gotoPreset,
  saveCurrentPreset, simulateSensor, startSiren, stopSiren, testSiren,
  upsertSensorMapping,
} from "../api/client";
import type {
  Camera, CameraPreset, HubZone, ListenerStatus, SensorEvent, SensorMapping,
} from "../api/types";
import JsonView from "../components/JsonView";
import Pill from "../components/Pill";
import Section from "../components/Section";
import { usePolling } from "../hooks/usePolling";

export default function Sensors() {
  const status = usePolling<ListenerStatus>(fetchListenerStatus, 3000);
  const events = usePolling(() => fetchRecentSensorEvents(30), 3000);
  const siren = usePolling(fetchSirenStatus, 3000);
  const hub = usePolling(fetchHubState, 5000);

  const [mappings, setMappings] = useState<SensorMapping[]>([]);
  const [mapAvail, setMapAvail] = useState(true);

  const loadMappings = useCallback(async () => {
    const body = await fetchSensorMappings();
    setMapAvail(body.available);
    setMappings(body.mappings ?? []);
  }, []);

  useEffect(() => { loadMappings().catch(console.error); }, [loadMappings]);

  // Resolve AX PRO zone id → human name for the binding rows + form.
  const hubZones = hub.data?.zones ?? [];
  const hubZoneById = useMemo(() => {
    const m = new Map<number, HubZone>();
    hubZones.forEach((z) => m.set(z.id, z));
    return m;
  }, [hubZones]);

  return (
    <>
      <h1 className="page-title">Sensors</h1>

      <ListenerStatusBanner status={status.data} />

      <SirenPanel
        sirens={siren.data?.sirens ?? []}
        onSound={(s) => startSiren(s).catch((e) => alert(String(e)))}
        onSilence={(s) => stopSiren(s).catch((e) => alert(String(e)))}
        onTest={(s) => testSiren(s).catch((e) => alert(String(e)))}
      />

      <Section title="Bindings"
        actions={<button onClick={loadMappings}>Refresh</button>}
      >
        <p className="muted small" style={{ marginTop: 0 }}>
          A binding sends a camera to a saved PTZ view whenever a specific
          alarm-panel sensor fires.
        </p>
        {!mapAvail ? (
          <p className="muted">Sensor map not wired.</p>
        ) : mappings.length === 0 ? (
          <p className="muted">No bindings yet — add one below.</p>
        ) : (
          <ul className="row-list">
            {mappings.map((m) => (
              <MappingRow
                key={m.zone_id}
                m={m}
                hubZone={hubZoneById.get(m.zone_id) ?? null}
                onSimulate={() => simulate(m, events.refresh)}
                onDelete={() => removeMapping(m.zone_id, loadMappings)}
              />
            ))}
          </ul>
        )}
      </Section>

      <AddMappingForm
        hubZones={hubZones}
        existingZoneIds={mappings.map((m) => m.zone_id)}
        onSaved={loadMappings}
      />

      <Section title="Recent events">
        {!events.data?.available ? (
          <p className="muted">Tap not wired.</p>
        ) : events.data.events.length === 0 ? (
          <p className="muted">No sensor events yet.</p>
        ) : (
          <ul className="row-list">
            {events.data.events.map((e, i) => (
              <EventRow key={i} event={e} hubZone={hubZoneById.get(e.zone_id) ?? null} />
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

async function simulate(m: SensorMapping, refresh: () => void) {
  try {
    const r = await simulateSensor(m.zone_id, m.sensor_type);
    if (!r.ok) alert("Simulate failed: " + (r.error || "unknown"));
    setTimeout(refresh, 1500);
    setTimeout(refresh, 4000);
  } catch (e) {
    alert("Simulate failed: " + e);
  }
}

async function removeMapping(zoneId: number, refresh: () => void) {
  if (!confirm(`Remove binding for zone ${zoneId}?`)) return;
  await deleteSensorMapping(zoneId);
  refresh();
}

function ListenerStatusBanner({ status }: { status: ListenerStatus | null }) {
  if (!status) return null;
  if (!status.wired) {
    return (
      <Section title={<>AX PRO listener <Pill tone="idle">not wired</Pill></>}>
        <p className="muted">{status.reason || "Hub credentials not configured."}</p>
      </Section>
    );
  }
  const connected = !!status.connected;
  const tone = !connected ? "bad" : status.armed ? "ok" : "warn";
  const label = !connected
    ? "disconnected"
    : status.armed ? "connected · armed" : "connected · disarmed";
  return (
    <Section
      title={<>AX PRO listener <Pill tone={tone}>{label}</Pill></>}
    >
      <div className="muted">
        host={status.host}
        {status.last_error ? ` · last_error=${status.last_error}` : ""}
      </div>
    </Section>
  );
}

// ─── siren panel ───────────────────────────────────────────────────

function SirenPanel({
  sirens,
  onSound, onSilence, onTest,
}: {
  sirens: Array<{ status: string; tamperEvident?: boolean }>;
  onSound: (subId: number) => void;
  onSilence: (subId: number) => void;
  onTest: (subId: number) => void;
}) {
  const [subId, setSubId] = useState(1);
  const [busy, setBusy] = useState<string | null>(null);

  const wrap = async (op: () => void | Promise<void>, label: string) => {
    setBusy(label);
    try { await op(); } finally { setBusy(null); }
  };

  const total = sirens.length;
  const online = sirens.filter((s) => s.status === "online").length;
  const tamper = sirens.some((s) => s.tamperEvident);
  let tone: "ok" | "warn" | "bad" | "idle" = "ok";
  let label = `${online}/${total} online`;
  if (!total) { tone = "idle"; label = "none paired"; }
  else if (online === 0) { tone = "bad"; label = "all offline"; }
  else if (online < total) { tone = "warn"; label += " (degraded)"; }
  if (tamper) { tone = "bad"; label += " · TAMPER"; }

  return (
    <Section
      title={<>Hub siren <Pill tone={tone}>{label}</Pill></>}
      actions={
        <label className="inline">
          sub_id
          <input
            type="number"
            min={1}
            value={subId}
            onChange={(e) => setSubId(parseInt(e.target.value, 10) || 1)}
            style={{ width: "4rem" }}
          />
        </label>
      }
    >
      <div className="row">
        <button
          className="primary"
          disabled={!!busy}
          onClick={() => {
            if (!confirm("Sound the hub siren now? Real 100+ dB alarm on-site. Use Test for 2s.")) return;
            wrap(() => onSound(subId), "sound");
          }}
        >
          {busy === "sound" ? "Sounding…" : "SOUND siren"}
        </button>
        <button disabled={!!busy} onClick={() => wrap(() => onSilence(subId), "silence")}>
          {busy === "silence" ? "…" : "Silence"}
        </button>
        <button disabled={!!busy} onClick={() => wrap(() => onTest(subId), "test")}>
          {busy === "test" ? "Testing…" : "Test (2s)"}
        </button>
      </div>
    </Section>
  );
}

// ─── mapping rows + add form ───────────────────────────────────────

function MappingRow({
  m, hubZone, onSimulate, onDelete,
}: {
  m: SensorMapping;
  hubZone: HubZone | null;
  onSimulate: () => void;
  onDelete: () => void;
}) {
  return (
    <li>
      <div className="row">
        <span className="kind">
          zone {m.zone_id}{hubZone ? ` · ${hubZone.name}` : ""}
        </span>
        <span className="muted small">
          {m.sensor_type || "?"} → cam{m.camera_id} preset “{m.preset_name}”
        </span>
        <span className="spacer" />
        <button className="primary" onClick={onSimulate}>Simulate trigger</button>
        <button onClick={onDelete}>Delete</button>
      </div>
      {m.description && <pre className="binding-desc">{m.description}</pre>}
    </li>
  );
}

function AddMappingForm({
  hubZones, existingZoneIds, onSaved,
}: {
  hubZones: HubZone[];
  existingZoneIds: number[];
  onSaved: () => void;
}) {
  const [zoneId, setZoneId] = useState<number | "">("");
  const [zoneIdManual, setZoneIdManual] = useState(false);
  const [zoneIdRaw, setZoneIdRaw] = useState("");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraId, setCameraId] = useState<number | null>(null);
  const [presets, setPresets] = useState<CameraPreset[]>([]);
  const [presetIndex, setPresetIndex] = useState<number | "">("");
  const [sensorType, setSensorType] = useState("doorContact");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  // Inline "save current view" form state.
  const [newPresetName, setNewPresetName] = useState("");
  const [savingPreset, setSavingPreset] = useState(false);

  // Preset preview state.
  const [previewBusy, setPreviewBusy] = useState(false);

  useEffect(() => {
    fetchCameras().then((cs) => {
      setCameras(cs);
      if (cs.length && cameraId === null) setCameraId(cs[0].camera_id);
    }).catch(() => setCameras([]));
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const reloadPresets = useCallback(async (cam: number | null) => {
    if (cam == null) { setPresets([]); return; }
    try {
      const body = await fetchPresets(cam);
      setPresets(body.presets ?? []);
    } catch {
      setPresets([]);
    }
  }, []);

  useEffect(() => {
    setPresetIndex("");
    reloadPresets(cameraId);
  }, [cameraId, reloadPresets]);

  const effectiveZoneId: number | null = zoneIdManual
    ? (zoneIdRaw && Number.isFinite(parseInt(zoneIdRaw, 10))
       ? parseInt(zoneIdRaw, 10) : null)
    : (zoneId === "" ? null : zoneId);

  const selectedHubZone = useMemo(
    () => hubZones.find((z) => z.id === effectiveZoneId) ?? null,
    [hubZones, effectiveZoneId],
  );
  const selectedPreset = useMemo(
    () => presets.find((p) => p.index === presetIndex) ?? null,
    [presets, presetIndex],
  );
  const duplicateZone = effectiveZoneId != null && existingZoneIds.includes(effectiveZoneId);

  // Auto-fill sensor_type from the AX PRO detector when picking a zone.
  useEffect(() => {
    if (selectedHubZone) {
      setSensorType(inferSensorType(selectedHubZone.detectorType));
    }
  }, [selectedHubZone]);

  const onPickHubZone = (raw: string) => {
    if (raw === "__manual") {
      setZoneIdManual(true);
      setZoneId("");
      return;
    }
    setZoneIdManual(false);
    setZoneId(raw === "" ? "" : parseInt(raw, 10));
  };

  const onPreviewPreset = useCallback(async () => {
    if (cameraId == null || presetIndex === "") return;
    setPreviewBusy(true);
    try {
      const r = await gotoPreset(cameraId, presetIndex);
      if (!r.ok) alert("Preview failed: " + (r.error || "unknown"));
    } catch (e) {
      alert("Preview failed: " + e);
    } finally {
      setPreviewBusy(false);
    }
  }, [cameraId, presetIndex]);

  const onSavePreset = useCallback(async () => {
    if (cameraId == null) return;
    const name = newPresetName.trim();
    if (!name) { alert("Give the preset a name."); return; }
    if (presets.some((p) => p.name === name)) {
      if (!confirm(`Preset "${name}" already exists on this camera — overwrite?`)) return;
    }
    setSavingPreset(true);
    try {
      const r = await saveCurrentPreset(cameraId, name);
      if (!r.ok || !r.preset) {
        alert("Save preset failed: " + (r.error || "unknown"));
        return;
      }
      await reloadPresets(cameraId);
      setPresetIndex(r.preset.index);
      setNewPresetName("");
    } catch (e) {
      alert("Save preset failed: " + e);
    } finally {
      setSavingPreset(false);
    }
  }, [cameraId, newPresetName, presets, reloadPresets]);

  const submit = useCallback(async () => {
    if (effectiveZoneId == null || effectiveZoneId <= 0) {
      alert("Pick a sensor zone (or enter a positive number).");
      return;
    }
    if (cameraId == null) { alert("Pick a camera."); return; }
    if (!selectedPreset) { alert("Pick a camera view (PTZ preset)."); return; }
    if (duplicateZone) {
      if (!confirm(`Zone ${effectiveZoneId} already has a binding — overwrite?`)) return;
    }
    setBusy(true);
    try {
      const reply = await upsertSensorMapping({
        zone_id: effectiveZoneId,
        camera_id: cameraId,
        preset_name: selectedPreset.name,
        sensor_type: sensorType,
        description: description.trim(),
      });
      if (!reply.ok) { alert("Save failed: " + (reply.error || "unknown")); return; }
      // Reset only the per-binding fields; keep camera selection so the
      // user can quickly add more bindings on the same camera.
      setZoneId("");
      setZoneIdRaw("");
      setZoneIdManual(false);
      setPresetIndex("");
      setDescription("");
      onSaved();
    } finally {
      setBusy(false);
    }
  }, [
    effectiveZoneId, cameraId, selectedPreset, duplicateZone, sensorType,
    description, onSaved,
  ]);

  const availableHubZones = hubZones; // show all, even bound ones (we warn on duplicate)

  return (
    <Section title="Add binding">
      <details className="help-callout" open>
        <summary>How bindings work</summary>
        <ul className="help-list">
          <li>
            <strong>Sensor zone</strong> — the slot a physical sensor occupies on
            your AX PRO alarm panel (door contact, PIR, etc.). The dropdown lists
            zones the panel is currently reporting.
          </li>
          <li>
            <strong>Camera view (PTZ preset)</strong> — a saved pan/tilt/zoom
            position on the camera. Pick from the list, or pan the camera to a new
            spot and use <em>Save current view as preset</em> below.
          </li>
          <li>
            When the chosen sensor fires, ITIPS sends the chosen camera to the
            chosen view and starts evaluating the scene.
          </li>
        </ul>
      </details>

      <div className="form-row">
        <label className="field">
          <span>Sensor zone (alarm panel)</span>
          {!zoneIdManual ? (
            <select
              value={zoneId === "" ? "" : String(zoneId)}
              onChange={(e) => onPickHubZone(e.target.value)}
            >
              <option value="">— pick a sensor —</option>
              {availableHubZones.length === 0 && (
                <option value="" disabled>(hub state unavailable)</option>
              )}
              {availableHubZones.map((z) => (
                <option key={z.id} value={z.id}>
                  zone {z.id} · {z.name} ({prettyDetector(z.detectorType)})
                  {existingZoneIds.includes(z.id) ? "  ✓ bound" : ""}
                </option>
              ))}
              <option value="__manual">Enter a zone number manually…</option>
            </select>
          ) : (
            <div className="row" style={{ gap: "0.4rem" }}>
              <input
                type="number"
                min={0}
                value={zoneIdRaw}
                onChange={(e) => setZoneIdRaw(e.target.value)}
                placeholder="e.g. 12"
              />
              <button onClick={() => { setZoneIdManual(false); setZoneIdRaw(""); }}>
                Use list
              </button>
            </div>
          )}
        </label>

        <label className="field">
          <span>Camera</span>
          <select
            value={cameraId ?? ""}
            onChange={(e) => setCameraId(parseInt(e.target.value, 10))}
          >
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                cam{c.camera_id} · {c.endpoint}
              </option>
            ))}
            {cameras.length === 0 && <option value="">(no cameras)</option>}
          </select>
        </label>

        <label className="field">
          <span>Camera view (PTZ preset)</span>
          <div className="row" style={{ gap: "0.4rem" }}>
            <select
              value={presetIndex === "" ? "" : String(presetIndex)}
              onChange={(e) => setPresetIndex(e.target.value === "" ? "" : parseInt(e.target.value, 10))}
              style={{ flex: 1 }}
            >
              <option value="">— pick a preset —</option>
              {presets.map((p) => (
                <option key={p.index} value={p.index}>
                  #{p.index} · {p.name}
                </option>
              ))}
            </select>
            <button
              onClick={onPreviewPreset}
              disabled={previewBusy || presetIndex === ""}
              title="Pan the camera to this preset so you can confirm the view"
            >
              {previewBusy ? "…" : "Preview"}
            </button>
          </div>
        </label>

        <label className="field">
          <span>Sensor type</span>
          <select value={sensorType} onChange={(e) => setSensorType(e.target.value)}>
            <option value="doorContact">doorContact</option>
            <option value="pir">pir</option>
            <option value="vibration">vibration</option>
            <option value="generic">generic</option>
          </select>
        </label>
      </div>

      <div className="preset-saver">
        <div className="preset-saver-label">
          <strong>Save current view as preset</strong>
          <span className="muted small">
            Pan the camera (Dahua web UI) to the view you want, then save it here.
          </span>
        </div>
        <div className="row" style={{ gap: "0.4rem" }}>
          <input
            type="text"
            value={newPresetName}
            onChange={(e) => setNewPresetName(e.target.value)}
            placeholder="Preset name (e.g. front_gate)"
            disabled={cameraId == null}
            style={{ flex: 1, minWidth: 0 }}
          />
          <button
            onClick={onSavePreset}
            disabled={savingPreset || cameraId == null || !newPresetName.trim()}
          >
            {savingPreset ? "Saving…" : `Save current view${cameraId != null ? ` on cam${cameraId}` : ""}`}
          </button>
        </div>
      </div>

      <label className="field">
        <span>Description (optional)</span>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g. Front gate magnetic contact"
        />
      </label>

      {duplicateZone && (
        <div className="alert-banner">
          Zone {effectiveZoneId} already has a binding — saving will overwrite it.
        </div>
      )}

      <button className="primary" disabled={busy} onClick={submit}>
        {busy ? "Saving…" : "Save binding"}
      </button>
    </Section>
  );
}

// ─── recent events row ─────────────────────────────────────────────

function EventRow({ event, hubZone }: { event: SensorEvent; hubZone: HubZone | null }) {
  const verdict = (event.outcome && event.outcome.verdict) || "?";
  const tone =
    verdict === "authorised" || verdict === "evaluating" ? "ok" :
    verdict === "intruder" ? "bad" :
    verdict?.startsWith("unverified") ? "warn" :
    verdict === "cooldown" ? "idle" : "warn";
  const when = new Date((event.received_ts || 0) * 1000).toLocaleTimeString();
  return (
    <li>
      <div className="row">
        <span className="kind">
          zone {event.zone_id}{hubZone ? ` · ${hubZone.name}` : ""}
        </span>
        <span className="muted small">{event.event_type} · src={event.source}</span>
        <Pill tone={tone}>{verdict}</Pill>
        <span className="ts">{when}</span>
      </div>
      <JsonView value={event.outcome ?? {}} />
    </li>
  );
}

// ─── helpers ───────────────────────────────────────────────────────

function inferSensorType(detector: string): string {
  const d = (detector || "").toLowerCase();
  if (d.includes("magnetic") || d.includes("contact")) return "doorContact";
  if (d.includes("vibration") || d.includes("shock")) return "vibration";
  if (d.includes("pir") || d.includes("passive") || d.includes("infrared") || d.includes("tritech")) return "pir";
  return "generic";
}

function prettyDetector(detector: string): string {
  const d = (detector || "").toLowerCase();
  if (d.includes("magnetic") || d.includes("contact")) return "door contact";
  if (d === "pircam") return "PIR camera";
  if (d.includes("pir") || d.includes("passive") || d.includes("infrared")) return "PIR";
  if (d.includes("tritech") || d.includes("tri-tech") || d.includes("tritric") || d.includes("trtech")) return "tri-tech";
  if (d.includes("vibration") || d.includes("shock")) return "vibration";
  if (d.includes("temperature") || d.includes("humidity")) return "temp/humidity";
  if (d.includes("glassbreak")) return "glass break";
  if (d.includes("smoke")) return "smoke";
  return detector || "sensor";
}
