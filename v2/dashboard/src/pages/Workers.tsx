import { useCallback, useEffect, useRef, useState } from "react";
import { deleteWorker, enrollWorker, fetchWorkers } from "../api/client";
import type { Worker } from "../api/types";
import Pill from "../components/Pill";
import Section from "../components/Section";

export default function Workers() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [jetsonAvailable, setJetsonAvailable] = useState(true);
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null);

  const reload = useCallback(async () => {
    const body = await fetchWorkers();
    setWorkers(body.workers ?? []);
    setJetsonAvailable(body.jetson_available ?? false);
  }, []);

  useEffect(() => { reload().catch(console.error); }, [reload]);

  return (
    <>
      <h1 className="page-title">Workers</h1>

      {!jetsonAvailable && (
        <div className="alert-banner" style={{ marginBottom: "0.75rem" }}>
          Face engine unavailable — enrolment is disabled until it loads.
        </div>
      )}

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
    </>
  );
}

function WorkerRow({ w, onDelete }: { w: Worker; onDelete: () => void }) {
  const handleDelete = async () => {
    if (!confirm(`Delete worker ${w.full_name}?`)) return;
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
          {w.jetson_enrolled ? "enrolled ✓" : "embedding missing"}
        </Pill>
        <span className="spacer" />
        <button onClick={handleDelete}>Delete</button>
      </div>
    </li>
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
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const submit = useCallback(async () => {
    const file = fileRef.current?.files?.[0];
    if (!name.trim()) { onStatus({ text: "Name required.", ok: false }); return; }
    if (!file) { onStatus({ text: "Select an image first.", ok: false }); return; }
    setBusy(true);
    onStatus({ text: "Enrolling…", ok: true });
    try {
      const r = await enrollWorker(name.trim(), file, personId.trim() || undefined);
      if (!r.ok) { onStatus({ text: r.error || "enroll failed", ok: false }); return; }
      onStatus({ text: `Enrolled ${name} (${r.person_id}).`, ok: true });
      setName(""); setPersonId("");
      if (fileRef.current) fileRef.current.value = "";
      onSaved();
    } catch (e) {
      onStatus({ text: "enroll failed: " + e, ok: false });
    } finally {
      setBusy(false);
    }
  }, [name, personId, onStatus, onSaved]);

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
          <span>Image (JPEG/PNG)</span>
          <input ref={fileRef} type="file" accept="image/*" />
        </label>
      </div>
      <button className="primary" disabled={busy} onClick={submit}>
        {busy ? "Enrolling…" : "Enroll worker"}
      </button>
    </Section>
  );
}
