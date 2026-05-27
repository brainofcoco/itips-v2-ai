import { useCallback, useEffect, useState } from "react";
import {
  createWebhookSubscriber, deleteWebhookSubscriber, fetchWebhookDeliveries,
  fetchWebhookKinds, fetchWebhookSubscribers, rotateWebhookSecret,
  testWebhookSubscriber,
} from "../api/client";
import type { WebhookDelivery, WebhookEventKind, WebhookSubscriber } from "../api/types";
import Pill from "../components/Pill";
import Section from "../components/Section";
import { usePolling } from "../hooks/usePolling";

export default function Webhooks() {
  const [kinds, setKinds] = useState<WebhookEventKind[]>([]);
  const [subs, setSubs] = useState<WebhookSubscriber[]>([]);
  const [newSecret, setNewSecret] = useState<{ id: string; secret: string } | null>(null);

  const deliveries = usePolling<{ deliveries: WebhookDelivery[] }>(
    fetchWebhookDeliveries, 5000,
  );

  const reloadAll = useCallback(async () => {
    const [k, s] = await Promise.all([
      fetchWebhookKinds(), fetchWebhookSubscribers(),
    ]);
    setKinds(Array.isArray(k.kinds) ? k.kinds : []);
    setSubs(s.subscribers ?? []);
  }, []);

  useEffect(() => { reloadAll().catch(console.error); }, [reloadAll]);

  return (
    <>
      <h1 className="page-title">Webhooks</h1>

      {newSecret && (
        <Section title={<>Subscriber created — secret <Pill tone="warn">save it now</Pill></>}>
          <p>
            Subscriber <code>{newSecret.id}</code> created. This is the only
            time you'll see the HMAC signing secret in cleartext:
          </p>
          <pre className="json-view">{newSecret.secret}</pre>
          <button onClick={() => setNewSecret(null)}>Dismiss</button>
        </Section>
      )}

      <AddSubscriberForm
        kinds={kinds}
        onSaved={(id, secret) => {
          if (secret) setNewSecret({ id, secret });
          reloadAll();
        }}
      />

      <Section title="Subscribers"
        actions={<button onClick={reloadAll}>Refresh</button>}
      >
        {subs.length === 0 ? (
          <p className="muted">No subscribers yet.</p>
        ) : (
          <ul className="row-list">
            {subs.map((s) => (
              <SubscriberRow
                key={s.id} sub={s}
                onChanged={reloadAll}
                onRotated={(id, secret) => setNewSecret({ id, secret })}
              />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Recent deliveries" actions={<button onClick={deliveries.refresh}>Refresh</button>}>
        <DeliveriesTable deliveries={deliveries.data?.deliveries ?? []} />
      </Section>
    </>
  );
}

function AddSubscriberForm({
  kinds, onSaved,
}: {
  kinds: WebhookEventKind[];
  onSaved: (id: string, secret?: string) => void;
}) {
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const toggle = (k: string) => {
    const next = new Set(selected);
    if (next.has(k)) next.delete(k); else next.add(k);
    setSelected(next);
  };

  const submit = useCallback(async () => {
    if (!url.trim()) { alert("URL required"); return; }
    setBusy(true);
    try {
      const reply = await createWebhookSubscriber({
        url: url.trim(),
        event_filter: selected.size ? Array.from(selected) : ["*"],
        description: description.trim(),
        enabled: true,
      });
      const created = reply.subscriber;
      if (created) onSaved(created.id, created.secret);
      setUrl(""); setDescription(""); setSelected(new Set());
    } catch (e) {
      alert("Create failed: " + e);
    } finally {
      setBusy(false);
    }
  }, [url, description, selected, onSaved]);

  return (
    <Section title="Add subscriber">
      <label className="field">
        <span>POST URL</span>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)}
               placeholder="https://example.com/itips-webhook" />
      </label>
      <label className="field">
        <span>Description (optional)</span>
        <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="field">
        <span>Event filter — leave empty to receive everything</span>
      </label>
      <div className="form-row" style={{ gap: "0.4rem" }}>
        {kinds.map(({ name, description: desc }) => (
          <label key={name} className="inline" style={{ alignItems: "flex-start", padding: "0.35rem 0" }}>
            <input
              type="checkbox"
              checked={selected.has(name)}
              onChange={() => toggle(name)}
            />
            <span>
              <code>{name}</code>
              <div className="muted" style={{ fontSize: "0.78rem" }}>{desc}</div>
            </span>
          </label>
        ))}
      </div>
      <button className="primary" disabled={busy} onClick={submit}>
        {busy ? "Creating…" : "Create subscriber"}
      </button>
    </Section>
  );
}

function SubscriberRow({
  sub, onChanged, onRotated,
}: {
  sub: WebhookSubscriber;
  onChanged: () => void;
  onRotated: (id: string, secret: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  const doTest = async () => {
    setBusy("test");
    try {
      const r = await testWebhookSubscriber(sub.id);
      if (!r.ok) alert(r.error || "test failed");
    } finally { setBusy(null); }
  };
  const doDelete = async () => {
    if (!confirm(`Delete subscriber ${sub.url}?`)) return;
    setBusy("delete");
    try { await deleteWebhookSubscriber(sub.id); onChanged(); }
    finally { setBusy(null); }
  };
  const doRotate = async () => {
    if (!confirm("Rotate the signing secret? Existing consumers must be updated with the new value.")) return;
    setBusy("rotate");
    try {
      const r = await rotateWebhookSecret(sub.id);
      const secret = r.subscriber?.secret;
      if (secret) onRotated(sub.id, secret);
    } finally { setBusy(null); }
  };

  return (
    <li>
      <div className="row">
        <span className="kind">{sub.url}</span>
        <Pill tone={sub.enabled ? "ok" : "idle"}>{sub.enabled ? "enabled" : "disabled"}</Pill>
        {(sub.consecutive_failures ?? 0) > 0 && (
          <Pill tone="warn">{sub.consecutive_failures} fail in a row</Pill>
        )}
        <span className="spacer" />
        <button disabled={!!busy} onClick={doTest}>{busy === "test" ? "…" : "Test ping"}</button>
        <button disabled={!!busy} onClick={doRotate}>{busy === "rotate" ? "…" : "Rotate secret"}</button>
        <button disabled={!!busy} onClick={doDelete}>{busy === "delete" ? "…" : "Delete"}</button>
      </div>
      <div className="muted" style={{ fontSize: "0.78rem", marginTop: "0.25rem" }}>
        <code>{sub.id}</code> · filter: {(sub.event_filter ?? []).join(", ") || "*"}
        {sub.description ? ` · ${sub.description}` : ""}
      </div>
    </li>
  );
}

function DeliveriesTable({ deliveries }: { deliveries: WebhookDelivery[] }) {
  if (deliveries.length === 0) {
    return <p className="muted">No deliveries yet.</p>;
  }
  return (
    <table className="thin-table">
      <thead>
        <tr>
          <th>When</th><th>Kind</th><th>Subscriber</th><th>Status</th><th>HTTP</th><th>Attempt</th><th>Δ ms</th><th>Error</th>
        </tr>
      </thead>
      <tbody>
        {deliveries.map((d) => {
          const ok = d.status_code != null && d.status_code >= 200 && d.status_code < 300;
          const tone: "ok" | "bad" | "warn" =
            ok ? "ok" : d.status_code != null || d.error ? "bad" : "warn";
          const when = formatDeliveredAt(d.delivered_at);
          return (
            <tr key={d.id}>
              <td title={whenTitle(d.delivered_at)}>{when}</td>
              <td><code>{d.event_kind}</code></td>
              <td><code>{(d.subscriber_id ?? "").slice(0, 10)}…</code></td>
              <td><Pill tone={tone}>{ok ? "ok" : "error"}</Pill></td>
              <td>{d.status_code ?? "—"}</td>
              <td>{d.attempt ?? "—"}</td>
              <td>{d.duration_ms ?? "—"}</td>
              <td className="muted">{d.error ?? ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function formatDeliveredAt(t: number | null | undefined): string {
  if (t == null || !Number.isFinite(t) || t <= 0) return "—";
  return new Date(t * 1000).toLocaleTimeString();
}
function whenTitle(t: number | null | undefined): string {
  if (t == null || !Number.isFinite(t) || t <= 0) return "";
  return new Date(t * 1000).toLocaleString();
}
