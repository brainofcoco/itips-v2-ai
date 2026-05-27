import { useState } from "react";
import {
  fetchCameras, fetchHubState, fetchListenerStatus, fetchOpenAIStatus,
  fireDeterrence, standdownDeterrence,
} from "../api/client";
import type {
  Camera, HubStateResponse, ListenerStatus, OpenAIStatus,
} from "../api/types";
import HubState from "../components/HubState";
import Pill from "../components/Pill";
import Section from "../components/Section";
import { usePolling } from "../hooks/usePolling";

export default function Settings() {
  const listener = usePolling<ListenerStatus>(fetchListenerStatus, 5000);
  const openai = usePolling<OpenAIStatus>(fetchOpenAIStatus, 10000);
  const cameras = usePolling<Camera[]>(fetchCameras, 30000);
  const hub = usePolling<HubStateResponse>(fetchHubState, 5000);

  return (
    <>
      <h1 className="page-title">Settings</h1>

      <ListenerBanner status={listener.data} />

      <Section
        title={<>Hub state <HubSummaryPill data={hub.data} /></>}
        actions={<button onClick={hub.refresh}>Refresh</button>}
      >
        <HubState data={hub.data} onMutated={hub.refresh} />
      </Section>

      <DeterrencePanel cameras={cameras.data ?? []} />

      <Section title={<>OpenAI validator <OpenAIPill data={openai.data} /></>}>
        {!openai.data ? (
          <p className="muted">Loading…</p>
        ) : !openai.data.wired ? (
          <p className="muted">{openai.data.reason || "Not configured. Set ITIPS_OPENAI_ENABLED=true + ITIPS_OPENAI_API_KEY."}</p>
        ) : (
          <>
            <div className="muted">
              model = <code>{openai.data.model ?? "?"}</code>
              {" · "}tokens used (last hour):{" "}
              {(openai.data.tokens_used_hour ?? 0).toLocaleString()}
              {openai.data.tokens_cap_hour
                ? ` / ${openai.data.tokens_cap_hour.toLocaleString()}`
                : ""}
            </div>
            {openai.data.recent && openai.data.recent.length > 0 && (
              <>
                <h4 style={{ marginTop: "0.75rem" }}>Recent verdicts</h4>
                <ul className="row-list">
                  {openai.data.recent.map((v, i) => (
                    <li key={i}>
                      <div className="row">
                        <span className="kind">{v.scenario}</span>
                        <Pill tone={v.verdict === "real" ? "bad" : v.verdict === "false_positive" ? "ok" : "warn"}>
                          {v.verdict}/{v.category}
                        </Pill>
                        <span className="muted">conf={(v.confidence ?? 0).toFixed(2)}</span>
                        <span className="muted">{v.cached ? "cached" : `${v.tokens_used ?? 0}t`}</span>
                        <span className="ts">{v.ts ? new Date(v.ts * 1000).toLocaleTimeString() : ""}</span>
                      </div>
                      {v.summary && <pre>{v.summary}</pre>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </Section>
    </>
  );
}

function OpenAIPill({ data }: { data: OpenAIStatus | null }) {
  if (!data) return null;
  if (!data.wired) return <Pill tone="idle">not wired</Pill>;
  return <Pill tone={data.enabled ? "ok" : "warn"}>{data.enabled ? "ready" : "disabled"}</Pill>;
}

function ListenerBanner({ status }: { status: ListenerStatus | null }) {
  if (!status) return null;
  if (!status.wired) {
    return (
      <Section title={<>AX PRO listener <Pill tone="idle">not wired</Pill></>}>
        <p className="muted">
          {status.reason || "AX PRO credentials not configured — set ITIPS_AXPRO_HOST/USERNAME/PASSWORD."}
        </p>
      </Section>
    );
  }
  const tone: "ok" | "warn" | "bad" = !status.connected ? "bad" : status.armed ? "ok" : "warn";
  const label = !status.connected ? "disconnected" : status.armed ? "any subsystem armed" : "all disarmed";
  return (
    <Section title={<>AX PRO listener <Pill tone={tone}>{label}</Pill></>}>
      <div className="muted">
        host={status.host}
        {status.last_error ? ` · last_error=${status.last_error}` : ""}
      </div>
    </Section>
  );
}

function HubSummaryPill({ data }: { data: HubStateResponse | null }) {
  if (!data) return <Pill tone="idle">…</Pill>;
  if (!data.ok) return <Pill tone="bad">unreachable</Pill>;
  const subs = (data.subsystems ?? []).filter((s) => s.enabled);
  const armed = subs.filter((s) => s.arming !== "disarm").length;
  const alarm = subs.some((s) => s.alarm) || (data.zones ?? []).some((z) => z.alarm);
  if (alarm) return <Pill tone="bad">ALARM ACTIVE</Pill>;
  return (
    <Pill tone={armed ? "ok" : "idle"}>
      {armed}/{subs.length} subsystems armed · {(data.zones ?? []).length} zones
    </Pill>
  );
}

function DeterrencePanel({ cameras }: { cameras: Camera[] }) {
  const [busy, setBusy] = useState<number | null>(null);

  const fire = async (id: number) => {
    setBusy(id);
    try { await fireDeterrence(id); } catch (e) { alert(String(e)); } finally { setBusy(null); }
  };
  const stand = async (id: number) => {
    setBusy(id);
    try { await standdownDeterrence(id); } catch (e) { alert(String(e)); } finally { setBusy(null); }
  };

  return (
    <Section title="Deterrence test (light + speaker)">
      {cameras.length === 0 ? (
        <p className="muted">No cameras configured.</p>
      ) : (
        <ul className="row-list">
          {cameras.map((c) => (
            <li key={c.camera_id}>
              <div className="row">
                <span className="kind">cam{c.camera_id}</span>
                <span className="muted">{c.endpoint}</span>
                <span className="spacer" />
                <button className="primary" disabled={busy === c.camera_id} onClick={() => fire(c.camera_id)}>
                  {busy === c.camera_id ? "…" : "Fire"}
                </button>
                <button disabled={busy === c.camera_id} onClick={() => stand(c.camera_id)}>Stand down</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
