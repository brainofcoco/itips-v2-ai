import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteWorker, enrollWorker, fetchCameraWorkers, fetchWorkers,
} from "../api/client";
import type { CameraWorkersResponse, Worker } from "../api/types";
import Pill from "../components/Pill";
import Section from "../components/Section";

export default function Workers() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [cameraIds, setCameraIds] = useState<number[]>([]);
  const [perCamera, setPerCamera] = useState<Record<number, CameraWorkersResponse | "loading" | "error">>({});
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null);

  const reload = useCallback(async () => {
    const body = await fetchWorkers();
    setWorkers(body.workers ?? []);
    setCameraIds(body.available_cameras ?? []);
    // Per-camera in parallel.
    const ids = body.available_cameras ?? [];
    const initial: typeof perCamera = {};
    ids.forEach((id) => { initial[id] = "loading"; });
    setPerCamera(initial);
    ids.forEach((id) => {
      fetchCameraWorkers(id)
        .then((r) => setPerCamera((prev) => ({ ...prev, [id]: r })))
        .catch(() => setPerCamera((prev) => ({ ...prev, [id]: "error" })));
    });
  }, []);

  useEffect(() => { reload().catch(console.error); }, [reload]);

  return (
    <>
      <h1 className="page-title">Workers</h1>

      <EnrollForm
        onStatus={setStatus}
        onSaved={reload}
      />
      {status && (
        <div className={status.ok ? "muted" : "alert-banner"} style={{ marginBottom: "0.75rem" }}>
          {status.text}
        </div>
      )}

      <Section
        title="Enrolled"
        actions={<button onClick={reload}>Refresh</button>}
      >
        {workers.length === 0 ? (
          <p className="muted">No workers enrolled yet.</p>
        ) : (
          <ul className="row-list">
            {workers.map((w) => (
              <WorkerRow key={w.person_id} w={w} onDelete={reload} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Per-camera face DBs">
        {cameraIds.length === 0 ? (
          <p className="muted">No cameras.</p>
        ) : (
          <div className="form-row">
            {cameraIds.map((id) => (
              <div key={id} className="panel" style={{ margin: 0 }}>
                <header className="panel-head"><h2>Camera {id}</h2></header>
                <div className="panel-body">
                  <CameraWorkerTable result={perCamera[id]} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

function WorkerRow({ w, onDelete }: { w: Worker; onDelete: () => void }) {
  const entries = Object.entries(w.cameras ?? {});
  const cameras = entries.length
    ? entries.map(([cid, uid]) => `cam${cid}:${uid}`).join(", ")
    : "no cameras";
  const handleDelete = async () => {
    if (!confirm(`Delete worker ${w.full_name} from every camera?`)) return;
    const r = await deleteWorker(w.person_id);
    if (!r.ok) { alert(r.error || "delete failed"); return; }
    onDelete();
  };
  return (
    <li>
      <div className="row">
        <span className="kind">{w.full_name}</span>
        <span className="muted">· {w.person_id}</span>
        <Pill tone={w.jetson_enrolled ? "ok" : "idle"}>
          {w.jetson_enrolled ? "jetson ✓" : "jetson ✗"}
        </Pill>
        <Pill tone={entries.length ? "ok" : "idle"}>
          {entries.length ? `dahua ${entries.length}cam` : "dahua none"}
        </Pill>
        <span className="spacer" />
        <button onClick={handleDelete}>Delete</button>
      </div>
      <pre>{cameras}</pre>
    </li>
  );
}

function CameraWorkerTable({ result }: { result: CameraWorkersResponse | "loading" | "error" | undefined }) {
  if (result === undefined || result === "loading") return <p className="muted">Loading…</p>;
  if (result === "error") return <p className="muted">(unavailable)</p>;
  const people = result.people ?? [];
  if (!people.length) return <p className="muted">No people on this camera.</p>;
  return (
    <table className="thin-table">
      <thead><tr><th>UID</th><th>Name</th><th>Sex</th></tr></thead>
      <tbody>
        {people.map((p) => (
          <tr key={p.uid}>
            <td>{p.uid}</td><td>{p.name}</td><td>{p.sex ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EnrollForm({
  onStatus, onSaved,
}: {
  onStatus: (s: { text: string; ok: boolean }) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [personId, setPersonId] = useState("");
  const [sex, setSex] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const submit = useCallback(async () => {
    const file = fileRef.current?.files?.[0];
    if (!name.trim()) { onStatus({ text: "Name required.", ok: false }); return; }
    if (!file) { onStatus({ text: "Select an image first.", ok: false }); return; }
    setBusy(true);
    onStatus({ text: "Pushing to every camera…", ok: true });
    try {
      const r = await enrollWorker(name.trim(), file, personId.trim() || undefined, sex || undefined);
      if (!r.ok) { onStatus({ text: r.error || "enroll failed", ok: false }); return; }
      const camCount = Object.keys(r.cameras ?? {}).length;
      const fail = (r.failures ?? []).join(", ");
      const parts: string[] = [];
      if (r.ml_enrolled) parts.push("Jetson ✓");
      else if (r.ml_error) parts.push(`Jetson ✗ (${r.ml_error})`);
      parts.push(`Dahua: ${camCount} camera(s)`);
      if (fail) parts.push(`Dahua failures: ${fail}`);
      const overallOk = !!r.ml_enrolled || camCount > 0;
      onStatus({ text: `Enrolled ${name} — ${parts.join(" · ")}`, ok: overallOk });
      setName(""); setPersonId("");
      if (fileRef.current) fileRef.current.value = "";
      onSaved();
    } catch (e) {
      onStatus({ text: "enroll failed: " + e, ok: false });
    } finally {
      setBusy(false);
    }
  }, [name, personId, sex, onStatus, onSaved]);

  return (
    <Section title="Enroll a new worker">
      <div className="form-row">
        <label className="field">
          <span>Full name</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Person ID (optional)</span>
          <input type="text" value={personId} onChange={(e) => setPersonId(e.target.value)} />
        </label>
        <label className="field">
          <span>Sex</span>
          <select value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="">—</option>
            <option value="male">male</option>
            <option value="female">female</option>
          </select>
        </label>
        <label className="field">
          <span>Image (JPEG/PNG)</span>
          <input ref={fileRef} type="file" accept="image/*" />
        </label>
      </div>
      <button className="primary" disabled={busy} onClick={submit}>
        {busy ? "Pushing…" : "Enroll across all cameras"}
      </button>
    </Section>
  );
}
