import { useCallback, useEffect, useState } from "react";
import { fetchHealth } from "../api/client";
import type { HealthCamera, HealthCheck, ProbeStatus } from "../api/types";
import Pill from "../components/Pill";
import Section from "../components/Section";

const STATUS_ICON: Record<ProbeStatus, string> = {
  ok: "✓", missing: "✗", error: "!", unknown: "?", "n/a": "—",
};
const STATUS_TONE: Record<ProbeStatus, "ok" | "bad" | "warn" | "idle"> = {
  ok: "ok", missing: "bad", error: "bad", unknown: "warn", "n/a": "idle",
};
const CATEGORY_LABEL: Record<HealthCheck["category"], string> = {
  core: "Core", media: "Media", ai: "AI features",
  control: "Control", events: "Events observed (passive)",
};

export default function Health() {
  const [cameras, setCameras] = useState<HealthCamera[] | null>(null);
  const [meta, setMeta] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (force: boolean) => {
    setBusy(true);
    setMeta("Probing every camera… (≤ 15s)");
    try {
      const body = await fetchHealth(force);
      setCameras(body.cameras ?? []);
      const ts = body.timestamp ? new Date(body.timestamp).toLocaleString() : "?";
      setMeta(body.cached ? `Cached result from ${ts} — re-probe to refresh.` : `Probed ${ts}.`);
    } catch (e) {
      setMeta("Failed: " + e);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  return (
    <>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <h1 className="page-title" style={{ margin: 0 }}>Health</h1>
        <span className="muted">{meta}</span>
        <span className="spacer" />
        <button className="primary" disabled={busy} onClick={() => load(true)}>
          {busy ? "Probing…" : "Re-probe"}
        </button>
      </div>

      {cameras === null && <p className="muted">Loading…</p>}
      {cameras !== null && cameras.length === 0 && (
        <p className="muted">No cameras configured.</p>
      )}
      {cameras !== null && cameras.map((c) => <CameraCard key={c.camera_id} camera={c} />)}
    </>
  );
}

function CameraCard({ camera }: { camera: HealthCamera }) {
  const byCat = new Map<string, HealthCheck[]>();
  for (const c of camera.checks) {
    if (!byCat.has(c.category)) byCat.set(c.category, []);
    byCat.get(c.category)!.push(c);
  }

  return (
    <Section
      title={
        <span className="row">
          <span>Camera {camera.camera_id}</span>
          <span className="muted">{camera.endpoint}</span>
          {camera.model && <span className="muted">· {camera.model}</span>}
        </span>
      }
      actions={
        <>
          <Pill tone="ok">{camera.green_count} ok</Pill>
          <Pill tone={camera.red_count ? "bad" : "idle"}>{camera.red_count} missing</Pill>
        </>
      }
    >
      {Array.from(byCat.entries()).map(([cat, list]) => (
        <div key={cat} style={{ marginBottom: "0.85rem" }}>
          <h4 style={{ margin: "0.5rem 0", color: "var(--muted)", fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            {CATEGORY_LABEL[cat as HealthCheck["category"]] || cat}
          </h4>
          <ul className="row-list">
            {list.map((c) => <CheckRow key={c.name} check={c} />)}
          </ul>
        </div>
      ))}
      {camera.backup_recommendations && camera.backup_recommendations.length > 0 && (
        <div className="alert-banner" style={{ background: "rgba(210,153,34,0.08)", color: "#e0b95a", borderColor: "rgba(210,153,34,0.35)" }}>
          <strong>Backups suggested:</strong>
          <ul>
            {camera.backup_recommendations.map((r, i) => (
              <li key={i}><strong>{r.label}</strong> — {r.hint}</li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

function CheckRow({ check }: { check: HealthCheck }) {
  const [open, setOpen] = useState(false);
  const hasDetail = !!check.detail || !!check.backup_hint;
  return (
    <li>
      <div
        className="row"
        style={{ cursor: hasDetail ? "pointer" : "default" }}
        onClick={() => hasDetail && setOpen((v) => !v)}
      >
        <Pill tone={STATUS_TONE[check.status]}>
          {STATUS_ICON[check.status]} {check.status}
        </Pill>
        <span>{check.label}</span>
        <span className="spacer" />
        {hasDetail && <span className="muted">{open ? "▴" : "▾"}</span>}
      </div>
      {hasDetail && open && (
        <div style={{ marginTop: "0.4rem", paddingLeft: "0.5rem" }}>
          {check.detail && <div className="muted">Detail: {check.detail}</div>}
          {check.backup_hint && (
            <div style={{ marginTop: "0.3rem" }}>
              <strong>Backup: </strong>{check.backup_hint}
            </div>
          )}
        </div>
      )}
    </li>
  );
}
