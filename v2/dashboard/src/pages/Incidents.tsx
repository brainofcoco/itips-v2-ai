import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bulkDeleteIncidents, deleteIncident, fetchIncident, fetchIncidents,
  incidentFileUrl, runTestIncident, verifyIncident,
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

  // Selection set for bulk delete. Cleared on every list reload so we
  // never delete an ID that's no longer on disk.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<"delete" | "all" | "one" | null>(null);
  const [bulkStatus, setBulkStatus] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const body = await fetchIncidents();
    setItems(body.incidents ?? []);
    // Drop any selected IDs that no longer exist on disk.
    setSelected((prev) => {
      const onDisk = new Set((body.incidents ?? []).map((i) => i.incident_id));
      const next = new Set<string>();
      prev.forEach((id) => { if (onDisk.has(id)) next.add(id); });
      return next;
    });
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

  // Selection helpers
  const allChecked = items.length > 0 && selected.size === items.length;
  const someChecked = selected.size > 0 && selected.size < items.length;
  const toggleOne = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  const toggleAll = useCallback(() => {
    setSelected((prev) => {
      if (prev.size === items.length) return new Set();
      return new Set(items.map((i) => i.incident_id));
    });
  }, [items]);

  // After any destructive op, drop the active selection if it was deleted.
  const afterDelete = useCallback((deletedIds: Set<string>) => {
    if (activeId && deletedIds.has(activeId)) {
      setActiveId(null);
      setDetail(null);
      setDetailError(null);
      setSig(null);
    }
  }, [activeId]);

  const deleteSingle = useCallback(async (id: string) => {
    if (!confirm(`Delete incident "${id.slice(0, 12)}…" permanently?\n\nThis removes the signed evidence package from disk and cannot be undone.`)) {
      return;
    }
    setBulkBusy("one");
    setBulkStatus(null);
    try {
      const r = await deleteIncident(id);
      if (!r.ok) {
        setBulkStatus("Delete failed: " + (r.error ?? "unknown"));
        return;
      }
      afterDelete(new Set([id]));
      setBulkStatus(`Deleted ${id.slice(0, 12)}…`);
      await reload();
    } finally {
      setBulkBusy(null);
    }
  }, [afterDelete, reload]);

  const deleteSelected = useCallback(async () => {
    if (selected.size === 0) return;
    const count = selected.size;
    if (!confirm(`Delete ${count} selected incident${count === 1 ? "" : "s"} permanently?\n\nThis removes the signed evidence package${count === 1 ? "" : "s"} from disk and cannot be undone.`)) {
      return;
    }
    setBulkBusy("delete");
    setBulkStatus(null);
    try {
      const ids = Array.from(selected);
      const r = await bulkDeleteIncidents({ ids });
      if (!r.ok) {
        setBulkStatus("Bulk delete failed: " + (r.error ?? "unknown"));
        return;
      }
      afterDelete(new Set(ids));
      setBulkStatus(`Deleted ${r.deleted ?? ids.length} of ${r.requested ?? ids.length}.`);
      await reload();
    } finally {
      setBulkBusy(null);
    }
  }, [selected, afterDelete, reload]);

  const deleteAll = useCallback(async () => {
    const count = items.length;
    if (count === 0) return;
    const phrase = `delete ${count}`;
    const typed = prompt(
      `This will permanently delete ALL ${count} incident package${count === 1 ? "" : "s"} on disk, including signed evidence.\n\nType "${phrase}" to confirm.`,
    );
    if ((typed || "").trim().toLowerCase() !== phrase) {
      setBulkStatus("Cancelled.");
      return;
    }
    setBulkBusy("all");
    setBulkStatus(null);
    try {
      const r = await bulkDeleteIncidents({ all: true });
      if (!r.ok) {
        setBulkStatus("Delete-all failed: " + (r.error ?? "unknown"));
        return;
      }
      afterDelete(new Set(items.map((i) => i.incident_id)));
      setBulkStatus(`Deleted ${r.deleted ?? "?"} incident${r.deleted === 1 ? "" : "s"}.`);
      await reload();
    } finally {
      setBulkBusy(null);
    }
  }, [items, afterDelete, reload]);

  const headerSummary = useMemo(() => {
    if (items.length === 0) return "no incidents yet";
    if (selected.size === 0) return `${items.length} package${items.length === 1 ? "" : "s"}`;
    return `${selected.size}/${items.length} selected`;
  }, [items, selected]);

  return (
    <>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <h1 className="page-title" style={{ margin: 0 }}>Incidents</h1>
        <span className="muted">{headerSummary}</span>
        <span className="spacer" />
        <button
          onClick={deleteSelected}
          disabled={selected.size === 0 || bulkBusy !== null}
          title={selected.size === 0 ? "Tick incidents in the list first" : `Delete the ${selected.size} selected package(s)`}
        >
          {bulkBusy === "delete" ? "Deleting…" : `Delete selected (${selected.size})`}
        </button>
        <button
          onClick={deleteAll}
          disabled={items.length === 0 || bulkBusy !== null}
          title="Permanently remove every incident package on disk"
          className="danger-ghost"
        >
          {bulkBusy === "all" ? "Deleting…" : "Delete all"}
        </button>
        <button onClick={() => reload()}>Refresh</button>
        <button className="primary" disabled={busy} onClick={runTest}>
          {busy ? "Building…" : "Run test incident"}
        </button>
      </div>

      {bulkStatus && (
        <div className="status-banner status-ok" style={{ marginBottom: "0.8rem" }}>
          {bulkStatus}
        </div>
      )}

      <div className="split">
        <Section
          title="Packages"
          actions={
            items.length > 0 && (
              <label className="inline small muted" title="Select / clear all">
                <input
                  type="checkbox"
                  checked={allChecked}
                  ref={(el) => { if (el) el.indeterminate = someChecked; }}
                  onChange={toggleAll}
                />
                {allChecked ? "all" : someChecked ? "some" : "select all"}
              </label>
            )
          }
        >
          {items.length === 0 ? (
            <p className="muted">No incidents on disk.</p>
          ) : (
            <ul className="select-list">
              {items.map((it) => {
                const checked = selected.has(it.incident_id);
                return (
                  <li
                    key={it.incident_id}
                    className={it.incident_id === activeId ? "active" : undefined}
                  >
                    <div className="incident-row">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleOne(it.incident_id)}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={`Select incident ${it.incident_id}`}
                      />
                      <div
                        className="incident-row-body"
                        onClick={() => select(it.incident_id)}
                      >
                        <strong>{it.incident_id.slice(0, 12)}…</strong>
                        <small>
                          {(it.started_utc || "—") + (it.finalized ? "  ·  signed" : "  ·  open")}
                        </small>
                      </div>
                      <button
                        className="incident-row-del"
                        title="Delete this incident"
                        disabled={bulkBusy !== null}
                        onClick={(e) => { e.stopPropagation(); deleteSingle(it.incident_id); }}
                      >
                        ✕
                      </button>
                    </div>
                  </li>
                );
              })}
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
              onDelete={() => deleteSingle(detail.incident_id)}
              deleteBusy={bulkBusy !== null}
            />
          )}
        </Section>
      </div>
    </>
  );
}

function IncidentBody({
  detail, sig, onVerify, onDelete, deleteBusy,
}: {
  detail: IncidentDetail;
  sig: SignatureVerifyResponse | "pending" | null;
  onVerify: () => void;
  onDelete: () => void;
  deleteBusy: boolean;
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
    ["Sensor captures", String(detail.sensor_captures?.length ?? 0)],
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
        <span className="spacer" />
        <button
          className="danger-ghost"
          disabled={deleteBusy}
          onClick={onDelete}
          title="Permanently delete this incident package"
        >
          Delete incident
        </button>
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
      {(detail.sensor_captures?.length ?? 0) > 0 && (
        <>
          <h3>
            Sensor captures ({detail.sensor_captures!.length})
            <span className="muted small" style={{ marginLeft: "0.5rem" }}>
              · PIR-cam alarm stills
            </span>
          </h3>
          <CaptureGrid id={detail.incident_id} captures={detail.sensor_captures!} />
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
