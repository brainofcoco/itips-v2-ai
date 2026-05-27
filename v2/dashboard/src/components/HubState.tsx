import { useState } from "react";
import {
  armHubAway, bypassZone, disarmHub, startSiren, stopSiren, testSiren,
  unbypassZone,
} from "../api/client";
import type { HubStateResponse, HubSubsystem, HubZone } from "../api/types";
import Pill from "./Pill";

// Friendly labels for Hikvision's detector type strings.
const DETECTOR_LABEL: Record<string, string> = {
  passiveInfraredDetector: "PIR motion",
  magneticContact: "Door / window contact",
  pircam: "PIR camera",
  wirelesTriTechDetector: "Tri-tech motion",
  wirelessTemperatureHumidityDetector: "Temp / humidity",
  smokeDetector: "Smoke",
  glassBreakDetector: "Glass break",
  panicButton: "Panic button",
};

function detectorLabel(t: string): string {
  return DETECTOR_LABEL[t] || t;
}

interface Props {
  data: HubStateResponse | null;
  onMutated: () => void;
}

export default function HubState({ data, onMutated }: Props) {
  if (!data) return <p className="muted">Loading hub state…</p>;
  if (!data.ok) {
    return <p className="alert-banner">Hub state failed — is the AX PRO listener wired and reachable?</p>;
  }

  const subs = (data.subsystems ?? []).filter((s) => s.enabled);
  const zones = data.zones ?? [];
  const zonesBySub = new Map<number, HubZone[]>();
  for (const z of zones) {
    const key = z.subSystemNo ?? 0;
    if (!zonesBySub.has(key)) zonesBySub.set(key, []);
    zonesBySub.get(key)!.push(z);
  }

  return (
    <div className="hub-grid">
      {subs.map((s) => (
        <SubsystemCard
          key={s.id}
          sub={s}
          zones={zonesBySub.get(s.id) ?? []}
          onMutated={onMutated}
        />
      ))}
      {/* Any zones whose subSystemNo isn't in the enabled subsystems list
          still need to be shown so a misconfigured zone is visible. */}
      {Array.from(zonesBySub.keys())
        .filter((k) => !subs.some((s) => s.id === k))
        .map((k) => (
          <article key={`orphan-${k}`} className="hub-card">
            <header className="hub-card-head">
              <h3>Unassigned (sub {k})</h3>
              <Pill tone="warn">no parent subsystem enabled</Pill>
            </header>
            <ZoneList zones={zonesBySub.get(k) ?? []} onMutated={onMutated} />
          </article>
        ))}
    </div>
  );
}

// ─── subsystem card ────────────────────────────────────────────────

function SubsystemCard({
  sub, zones, onMutated,
}: {
  sub: HubSubsystem;
  zones: HubZone[];
  onMutated: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  const armed = sub.arming !== "disarm";
  const inAlarm = sub.alarm;

  const wrap = async (key: string, op: () => Promise<unknown>) => {
    setBusy(key);
    try { await op(); }
    catch (e) { alert(String(e)); }
    finally { setBusy(null); onMutated(); }
  };

  const toggleArming = async () => {
    if (armed) await wrap("arm", () => disarmHub([sub.id]));
    else      await wrap("arm", () => armHubAway([sub.id]));
  };

  return (
    <article className={`hub-card${inAlarm ? " hub-card-alarm" : ""}`}>
      <header className="hub-card-head">
        <div>
          <h3>{sub.name}</h3>
          <span className="muted" style={{ fontSize: "0.78rem" }}>area {sub.id}</span>
        </div>
        <div className="row">
          {inAlarm && <Pill tone="bad">ALARM</Pill>}
          <Pill tone={armed ? "ok" : "idle"}>{sub.arming.toUpperCase()}</Pill>
        </div>
      </header>

      <div className="hub-card-controls">
        <label className="switch" title={armed ? "Disarm" : "Arm Away"}>
          <input
            type="checkbox"
            checked={armed}
            disabled={busy !== null}
            onChange={toggleArming}
          />
          <span className="switch-slider" />
          <span className="switch-label">{armed ? "Armed" : "Disarmed"}</span>
        </label>

        <span className="spacer" />

        <button
          className="primary"
          disabled={busy !== null}
          onClick={() => {
            if (!confirm(`Sound siren for "${sub.name}"? This triggers a real 100+ dB alarm on-site.`)) return;
            wrap("sound", () => startSiren(sub.id));
          }}
        >
          {busy === "sound" ? "Sounding…" : "Sound siren"}
        </button>
        <button
          disabled={busy !== null}
          onClick={() => wrap("silence", () => stopSiren(sub.id))}
        >
          {busy === "silence" ? "…" : "Silence"}
        </button>
        <button
          disabled={busy !== null}
          onClick={() => wrap("test", () => testSiren(sub.id, 2))}
          title="2-second test burst"
        >
          {busy === "test" ? "…" : "Test (2s)"}
        </button>
      </div>

      <ZoneList zones={zones} onMutated={onMutated} />
    </article>
  );
}

// ─── zones inside a subsystem ──────────────────────────────────────

function ZoneList({ zones, onMutated }: { zones: HubZone[]; onMutated: () => void }) {
  if (zones.length === 0) {
    return <p className="muted" style={{ padding: "0.6rem 0.8rem 0.9rem" }}>No zones bound to this subsystem.</p>;
  }
  return (
    <ul className="zone-list">
      {zones.map((z) => <ZoneRow key={z.id} zone={z} onMutated={onMutated} />)}
    </ul>
  );
}

function ZoneRow({ zone, onMutated }: { zone: HubZone; onMutated: () => void }) {
  const [busy, setBusy] = useState(false);

  const isOnline = zone.status === "online";
  const battery = zone.chargeValue ?? null;
  const batteryTone: "ok" | "warn" | "bad" | "idle" =
    battery == null ? "idle" :
    battery >= 60 ? "ok" :
    battery >= 30 ? "warn" : "bad";

  const wrap = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try { await op(); }
    catch (e) { alert(String(e)); }
    finally { setBusy(false); onMutated(); }
  };

  const toggleBypass = async () => {
    if (zone.bypassed) await wrap(() => unbypassZone(zone.id));
    else                await wrap(() => bypassZone(zone.id));
  };

  return (
    <li className={`zone-row${!isOnline ? " zone-offline" : ""}`}>
      <div className="zone-main">
        <div className="zone-name">
          <strong>{zone.name}</strong>
          <span className="muted"> · {detectorLabel(zone.detectorType)}</span>
        </div>
        <div className="zone-badges">
          <Pill tone={isOnline ? "ok" : "bad"}>{zone.status}</Pill>
          {zone.alarm && <Pill tone="bad">alarm</Pill>}
          {zone.tamperEvident && <Pill tone="bad">tamper</Pill>}
          {zone.bypassed && <Pill tone="warn">bypassed</Pill>}
          {zone.armed && !zone.bypassed && <Pill tone="ok">armed</Pill>}
          {zone.magnetOpenStatus && <Pill tone="warn">open</Pill>}
        </div>
      </div>

      <div className="zone-meta">
        {battery != null && (
          <Pill tone={batteryTone} title={`battery ${battery}%`}>{batteryIcon(battery)} {battery}%</Pill>
        )}
        {zone.signal != null && (
          <span className="muted" title={`signal ${zone.signal}`}>{signalIcon(zone.signal)} {zone.signal}</span>
        )}
        {zone.temperature != null && <span className="muted">{zone.temperature}°C</span>}
        {zone.humidity != null && <span className="muted">{zone.humidity}%RH</span>}
        <span className="spacer" />
        <button disabled={busy} onClick={toggleBypass}>
          {busy ? "…" : zone.bypassed ? "Un-bypass" : "Bypass"}
        </button>
      </div>
    </li>
  );
}

function batteryIcon(pct: number): string {
  if (pct >= 80) return "🔋";
  if (pct >= 30) return "🪫";
  return "🪫";
}

function signalIcon(s: number): string {
  if (s >= 130) return "📶";
  if (s >= 80)  return "📶";
  return "📶";
}
