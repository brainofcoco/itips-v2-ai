import { useCallback, useEffect, useMemo, useState } from "react";
import {
  clearVerdicts, fetchRecentVerdicts, fetchVerdictCaptures, verdictCaptureUrl,
} from "../api/client";
import type { VerdictEvent, VerdictOutcome } from "../api/types";
import Section from "../components/Section";

type FilterKey = "all" | "not_escalated" | "authorized" | "uncertain" | "suppressed_disarmed" | "escalated";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "not_escalated", label: "Not escalated" },
  { key: "authorized", label: "Authorised worker" },
  { key: "uncertain", label: "No face / uncertain" },
  { key: "suppressed_disarmed", label: "Disarmed" },
  { key: "escalated", label: "Escalated" },
];

export default function Investigations() {
  const [verdicts, setVerdicts] = useState<VerdictEvent[]>([]);
  const [available, setAvailable] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("not_escalated");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<VerdictEvent | null>(null);
  const [clearing, setClearing] = useState(false);

  const reload = useCallback(async () => {
    try {
      const body = await fetchRecentVerdicts(200);
      setAvailable(body.available);
      setVerdicts(body.verdicts ?? []);
    } catch {
      setVerdicts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
    const t = setInterval(() => { if (!document.hidden) reload(); }, 3000);
    return () => clearInterval(t);
  }, [reload]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {
      all: verdicts.length, not_escalated: 0, authorized: 0,
      uncertain: 0, suppressed_disarmed: 0, escalated: 0,
    };
    for (const v of verdicts) {
      if (!v.escalated) c.not_escalated++;
      if (v.outcome in c) c[v.outcome]++;
    }
    return c;
  }, [verdicts]);

  const shown = useMemo(() => {
    if (filter === "all") return verdicts;
    if (filter === "not_escalated") return verdicts.filter((v) => !v.escalated);
    return verdicts.filter((v) => v.outcome === filter);
  }, [verdicts, filter]);

  const onClear = useCallback(async () => {
    if (!confirm(`Clear all ${verdicts.length} investigation record(s) and their captured images? This cannot be undone.`)) return;
    setClearing(true);
    try {
      await clearVerdicts();
      setSelected(null);
      await reload();
    } finally {
      setClearing(false);
    }
  }, [verdicts.length, reload]);

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Investigations</h1>
        <div className="page-actions">
          <span className="muted small">
            {verdicts.length} evaluation{verdicts.length === 1 ? "" : "s"} · {counts.not_escalated} not escalated
          </span>
          <button onClick={reload}>Refresh</button>
          <button
            className="danger-ghost"
            disabled={clearing || verdicts.length === 0}
            onClick={onClear}
          >
            {clearing ? "Clearing…" : "Clear all"}
          </button>
        </div>
      </div>

      <p className="muted" style={{ marginTop: 0 }}>
        Every threat evaluation after an intrusion or sensor trip — and the reason it did or
        didn’t escalate. Click a row to see the snapshots the camera took during evaluation.
      </p>

      <div className="filter-chips">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip${filter === f.key ? " chip-active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
            <span className="chip-count">{counts[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <Section title={FILTERS.find((f) => f.key === filter)?.label ?? "Verdicts"}>
        {!available ? (
          <p className="muted">Threat evaluator not wired (no face engine) — no verdicts to show.</p>
        ) : loading ? (
          <p className="muted">Loading…</p>
        ) : shown.length === 0 ? (
          <p className="muted">No evaluations in this category yet.</p>
        ) : (
          <table className="thin-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Camera</th>
                <th>Outcome</th>
                <th>Reason</th>
                <th>Captures</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((v) => (
                <tr
                  key={v.seq}
                  className="clickable-row"
                  onClick={() => setSelected(v)}
                >
                  <td className="nowrap">{new Date(v.ts * 1000).toLocaleString()}</td>
                  <td>cam{v.camera_id ?? "?"}</td>
                  <td><OutcomePill v={v} /></td>
                  <td>{v.reason}</td>
                  <td className="muted small">
                    {v.capture_count ? `${v.capture_count} 📷` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {selected && (
        <VerdictDetail verdict={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}

function VerdictDetail({ verdict, onClose }: { verdict: VerdictEvent; onClose: () => void }) {
  const [images, setImages] = useState<string[]>([]);
  const [videos, setVideos] = useState<string[]>([]);
  const [imgLoading, setImgLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setImgLoading(true);
    if (!verdict.capture_id) { setImages([]); setVideos([]); setImgLoading(false); return; }
    fetchVerdictCaptures(verdict.capture_id)
      .then((b) => { if (!cancelled) { setImages(b.images ?? []); setVideos(b.videos ?? []); } })
      .catch(() => { if (!cancelled) { setImages([]); setVideos([]); } })
      .finally(() => { if (!cancelled) setImgLoading(false); });
    return () => { cancelled = true; };
  }, [verdict.capture_id]);

  const rows: Array<[string, string]> = [
    ["Time", new Date(verdict.ts * 1000).toLocaleString()],
    ["Camera", `cam${verdict.camera_id ?? "?"}`],
    ["Outcome", verdict.outcome],
    ["Reason", verdict.reason],
    ["Armed", verdict.armed ? "yes" : "no"],
    ["Escalated", verdict.escalated ? "yes — alarm fired" : "no"],
    ["Triggers", (verdict.triggers ?? []).join(", ") || "—"],
    ["Samples", String(verdict.samples ?? "—") + (verdict.saw_face ? " · face seen" : " · no face")],
    ["Window", verdict.window_s != null ? `${verdict.window_s}s` : "—"],
  ];
  if (verdict.outcome === "authorized") {
    rows.push(["Matched", `${verdict.person_name || "?"}${verdict.similarity != null ? ` (${Math.round(verdict.similarity * 100)}%)` : ""}`]);
  } else if (verdict.best_no_match_sim != null) {
    rows.push(["Best face sim", `${Math.round(verdict.best_no_match_sim * 100)}% (below match threshold)`]);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Investigation · cam{verdict.camera_id ?? "?"}</h2>
          <button onClick={onClose}>✕ Close</button>
        </div>

        <table className="thin-table" style={{ marginBottom: "1rem" }}>
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k}><th style={{ width: "9rem" }}>{k}</th><td>{v}</td></tr>
            ))}
          </tbody>
        </table>

        {verdict.capture_id && videos.length > 0 && (
          <>
            <h3>Evidence clip</h3>
            {videos.map((name) => (
              <div key={name} style={{ marginBottom: "0.75rem" }}>
                <video
                  src={verdictCaptureUrl(verdict.capture_id!, name)}
                  controls
                  preload="metadata"
                  style={{ maxWidth: "100%", borderRadius: "6px" }}
                />
                <div>
                  <a
                    href={verdictCaptureUrl(verdict.capture_id!, name)}
                    download
                    target="_blank"
                    rel="noopener"
                    className="muted small"
                  >
                    Download / share clip ↓
                  </a>
                </div>
              </div>
            ))}
          </>
        )}

        <h3>Captured snapshots {images.length ? `(${images.length})` : ""}</h3>
        {imgLoading ? (
          <p className="muted">Loading…</p>
        ) : !verdict.capture_id || images.length === 0 ? (
          <p className="muted">No snapshots were captured for this evaluation.</p>
        ) : (
          <div className="capture-grid">
            {images.map((name) => (
              <a
                key={name}
                href={verdictCaptureUrl(verdict.capture_id!, name)}
                target="_blank"
                rel="noopener"
                title={name}
              >
                <img src={verdictCaptureUrl(verdict.capture_id!, name)} alt={name} />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function OutcomePill({ v }: { v: VerdictEvent }) {
  const { tone, label } = outcomeStyle(v.outcome, v.escalated);
  return <span className={`pill ${tone}`}>{label}</span>;
}

function outcomeStyle(outcome: VerdictOutcome, escalated: boolean): { tone: string; label: string } {
  switch (outcome) {
    case "escalated":           return { tone: "pill-bad", label: "Escalated" };
    case "authorized":          return { tone: "pill-ok", label: "Authorised" };
    case "uncertain":           return { tone: "pill-warn", label: "Uncertain" };
    case "suppressed_disarmed": return { tone: "pill-idle", label: "Disarmed" };
    default:                    return { tone: escalated ? "pill-bad" : "pill-idle", label: outcome };
  }
}
