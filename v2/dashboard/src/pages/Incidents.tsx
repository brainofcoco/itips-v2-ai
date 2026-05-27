import { useCallback, useEffect, useState } from "react";
import {
  fetchIncident, fetchIncidents, incidentFileUrl, runTestIncident,
  verifyIncident,
} from "../api/client";
import type { IncidentDetail, IncidentSummary, SignatureVerifyResponse } from "../api/types";
import JsonView from "../components/JsonView";
import Pill from "../components/Pill";
import Section from "../components/Section";

export default function Incidents() {
  const [items, setItems] = useState<IncidentSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sig, setSig] = useState<SignatureVerifyResponse | "pending" | null>(null);

  const reload = useCallback(async () => {
    const body = await fetchIncidents();
    setItems(body.incidents ?? []);
  }, []);

  useEffect(() => { reload().catch(console.error); }, [reload]);

  const select = useCallback(async (id: string) => {
    setActiveId(id);
    setDetail(null);
    setDetailError(null);
    setSig(null);
    try {
      setDetail(await fetchIncident(id));
    } catch (e) {
      setDetailError(String(e));
    }
  }, []);

  const runTest = useCallback(async () => {
    setBusy(true);
    try {
      const r = await runTestIncident();
      if (!r.ok) {
        alert("Test incident failed");
        return;
      }
      await reload();
      if (r.incident_id) select(r.incident_id);
    } finally {
      setBusy(false);
    }
  }, [reload, select]);

  const doVerify = useCallback(async (id: string) => {
    setSig("pending");
    try {
      setSig(await verifyIncident(id));
    } catch (e) {
      setSig({ ok: false, files: [{ name: "error", status: String(e) }] });
    }
  }, []);

  return (
    <>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <h1 className="page-title" style={{ margin: 0 }}>Incidents</h1>
        <span className="muted">
          {items.length ? `${items.length} package(s)` : "no incidents yet"}
        </span>
        <span className="spacer" />
        <button onClick={() => reload()}>Refresh</button>
        <button className="primary" disabled={busy} onClick={runTest}>
          {busy ? "Building…" : "Run test incident"}
        </button>
      </div>

      <div className="split">
        <Section title="Packages">
          {items.length === 0 ? (
            <p className="muted">No incidents on disk.</p>
          ) : (
            <ul className="select-list">
              {items.map((it) => (
                <li
                  key={it.incident_id}
                  className={it.incident_id === activeId ? "active" : undefined}
                  onClick={() => select(it.incident_id)}
                >
                  <strong>{it.incident_id.slice(0, 12)}…</strong>
                  <small>
                    {(it.started_utc || "—") + (it.finalized ? "  ·  signed" : "  ·  open")}
                  </small>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Detail">
          {!activeId && <p className="muted">Pick an incident to inspect.</p>}
          {activeId && detailError && <p className="alert-banner">{detailError}</p>}
          {activeId && !detail && !detailError && <p className="muted">Loading…</p>}
          {detail && (
            <IncidentBody
              detail={detail}
              sig={sig}
              onVerify={() => doVerify(detail.incident_id)}
            />
          )}
        </Section>
      </div>
    </>
  );
}

function IncidentBody({
  detail, sig, onVerify,
}: {
  detail: IncidentDetail;
  sig: SignatureVerifyResponse | "pending" | null;
  onVerify: () => void;
}) {
  const meta = detail.metadata || {};
  const summary: Array<[string, string]> = [
    ["Status", String(meta.status ?? "?")],
    ["Closed reason", String(meta.closed_reason ?? "—")],
    ["Started (UTC)", String(meta.started_utc ?? "—")],
    ["Finalized (UTC)", String(meta.finalized_utc ?? "—")],
    ["Cameras active", (meta.camera_ids_active || []).join(", ") || "—"],
    ["Classification", String(meta.incident_classification ?? "—")],
    ["Events", String(meta.event_count ?? "—")],
    ["Sensor events", String(meta.sensor_event_count ?? "—")],
    ["Face captures", String(meta.face_capture_count ?? detail.face_captures.length ?? 0)],
    ["Plate captures", String(meta.plate_capture_count ?? detail.plate_captures.length ?? 0)],
  ];

  const sigTone =
    sig === "pending" ? "idle" :
    sig?.ok ? "ok" :
    sig ? "bad" : "idle";
  const sigText =
    sig === "pending" ? "signature: verifying…" :
    sig?.ok ? "signature: valid" :
    sig ? `signature: ${(sig.files || []).filter(f => f.status !== "ok").length} mismatch` :
    "signature: ?";

  return (
    <>
      <div className="row" style={{ marginBottom: "0.5rem" }}>
        <span className="kind">{detail.incident_id}</span>
        <Pill tone={sigTone}>{sigText}</Pill>
        <button className="primary" onClick={onVerify}>Verify signature</button>
        {detail.has_pdf && (
          <a
            target="_blank" rel="noopener"
            href={incidentFileUrl(detail.incident_id, "incident_summary.pdf", true)}
          >
            Open PDF ↗
          </a>
        )}
      </div>

      <h3>Summary</h3>
      <table className="thin-table">
        <tbody>
          {summary.map(([k, v]) => (
            <tr key={k}><th>{k}</th><td>{v}</td></tr>
          ))}
        </tbody>
      </table>

      {detail.face_captures?.length > 0 && (
        <>
          <h3>Face captures ({detail.face_captures.length})</h3>
          <CaptureGrid id={detail.incident_id} captures={detail.face_captures} />
        </>
      )}
      {detail.plate_captures?.length > 0 && (
        <>
          <h3>Plate captures ({detail.plate_captures.length})</h3>
          <CaptureGrid id={detail.incident_id} captures={detail.plate_captures} />
        </>
      )}
      {detail.video_files && detail.video_files.length > 0 && (
        <>
          <h3>Video evidence</h3>
          <ul>
            {detail.video_files.map((f) => (
              <li key={f}>
                <a target="_blank" href={incidentFileUrl(detail.incident_id, f, true)}>{f}</a>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Manifest</h3>
      <JsonView value={detail.manifest || {}} />
      <h3>Signature</h3>
      <JsonView value={detail.signature || {}} />
    </>
  );
}

function CaptureGrid({ id, captures }: { id: string; captures: IncidentDetail["face_captures"] }) {
  return (
    <div className="cam-grid">
      {captures.map((c) => (
        <article key={c.filename} className="cam">
          <div className="feed">
            <img src={incidentFileUrl(id, c.filename, true)} alt={c.filename} />
          </div>
          <header>
            <h2>{c.filename.split("/").pop()}</h2>
            {c.bytes != null && <span className="muted">{c.bytes} B</span>}
          </header>
        </article>
      ))}
    </div>
  );
}
