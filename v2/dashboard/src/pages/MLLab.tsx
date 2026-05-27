import { useCallback, useEffect, useRef, useState } from "react";
import {
  analyseBehavior, fetchCameras, fetchCapabilities, fetchEnrolledFaces,
  fetchMLStatus, fetchOpenAIStatus, readPlate, recognizeFace,
  runOpenAIScenario, setCapabilityOverride, warmupML,
} from "../api/client";
import type {
  Camera, CapabilitiesResponse, EnrolledFacesResponse, MLEngineState,
  MLStatus, OpenAIStatus,
} from "../api/types";
import JsonView from "../components/JsonView";
import Pill from "../components/Pill";
import Section from "../components/Section";

export default function MLLab() {
  const [status, setStatus] = useState<MLStatus | null>(null);
  const [enrolled, setEnrolled] = useState<EnrolledFacesResponse | null>(null);
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [openai, setOpenai] = useState<OpenAIStatus | null>(null);
  const [dispatch, setDispatch] = useState(false);

  const reloadAll = useCallback(async () => {
    const [s, e, c, cs, o] = await Promise.all([
      fetchMLStatus(), fetchEnrolledFaces(), fetchCapabilities(),
      fetchCameras(), fetchOpenAIStatus(),
    ]);
    setStatus(s); setEnrolled(e); setCaps(c); setCameras(cs); setOpenai(o);
  }, []);

  useEffect(() => { reloadAll().catch(console.error); }, [reloadAll]);

  const warmup = async () => {
    const next = await warmupML();
    setStatus(next);
  };

  return (
    <>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <h1 className="page-title" style={{ margin: 0 }}>ML Lab</h1>
        <EnginePill label="face" state={status?.face} />
        <EnginePill label="plate" state={status?.plate} />
        <EnginePill label="behavior" state={status?.behavior} />
        <span className="spacer" />
        <label className="inline">
          <input type="checkbox" checked={dispatch} onChange={(e) => setDispatch(e.target.checked)} />
          dispatch to AlertEngine
        </label>
        <button onClick={reloadAll}>Refresh</button>
        <button className="primary" onClick={warmup}>Warm up all engines</button>
      </div>

      <FaceLab cameras={cameras} dispatch={dispatch} onRunDone={reloadAll} />
      <PlateLab cameras={cameras} dispatch={dispatch} onRunDone={reloadAll} />
      <BehaviorLab cameras={cameras} dispatch={dispatch} onRunDone={reloadAll} />
      <FaceOverrides cameras={cameras} caps={caps} onChanged={reloadAll} />
      <EnrolledList data={enrolled} />
      <OpenAIPanel data={openai} onRun={reloadAll} />
    </>
  );
}

function EnginePill({ label, state }: { label: string; state?: MLEngineState }) {
  if (!state) return <Pill tone="idle">{label}: …</Pill>;
  if (!state.wired) return <Pill tone="idle">{label}: not wired</Pill>;
  return state.ready
    ? <Pill tone="ok">{label}: ready</Pill>
    : <Pill tone="warn">{label}: warming…</Pill>;
}

// ─── face / plate / behavior labs ─────────────────────────────────

function FileLab({
  title, run, helpText, cameras, dispatch, requireCamera,
}: {
  title: string;
  run: (file: File, cameraId: number | null) => Promise<unknown>;
  helpText: string;
  cameras: Camera[];
  dispatch: boolean;
  requireCamera?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [cameraId, setCameraId] = useState<number | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (cameraId == null && cameras.length) setCameraId(cameras[0].camera_id);
  }, [cameras, cameraId]);

  const onRun = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { setResult({ error: "Pick an image first." }); return; }
    if (requireCamera && cameraId == null) { setResult({ error: "Pick a camera first." }); return; }
    setBusy(true);
    setResult({ status: "running…" });
    try {
      setResult(await run(file, cameraId));
    } catch (e) {
      setResult({ error: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={`${title}${dispatch ? "  ·  routes through AlertEngine" : ""}`}>
      <p className="muted">{helpText}</p>
      <div className="form-row">
        <label className="field">
          <span>Image</span>
          <input ref={fileRef} type="file" accept="image/*" />
        </label>
        <label className="field">
          <span>Camera ID</span>
          <select
            value={cameraId ?? ""}
            onChange={(e) => setCameraId(parseInt(e.target.value, 10))}
          >
            {!requireCamera && <option value="0">— none (no dispatch context) —</option>}
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>cam{c.camera_id} · {c.endpoint}</option>
            ))}
          </select>
        </label>
      </div>
      <button className="primary" disabled={busy} onClick={onRun}>
        {busy ? "Running…" : `Run ${title.toLowerCase()}`}
      </button>
      {result !== null && <JsonView value={result} />}
    </Section>
  );
}

function FaceLab(props: { cameras: Camera[]; dispatch: boolean; onRunDone: () => void }) {
  return (
    <FileLab
      title="Face recognize"
      helpText="Runs Jetson InsightFace against the uploaded still. Result includes person_id, similarity, embedding stats."
      cameras={props.cameras}
      dispatch={props.dispatch}
      run={async (file, camId) => {
        const r = await recognizeFace(file, camId ?? 0, props.dispatch);
        props.onRunDone();
        return r;
      }}
    />
  );
}
function PlateLab(props: { cameras: Camera[]; dispatch: boolean; onRunDone: () => void }) {
  return (
    <FileLab
      title="Plate read"
      helpText="Runs Jetson EasyOCR against the uploaded still."
      cameras={props.cameras}
      dispatch={props.dispatch}
      run={async (file, camId) => {
        const r = await readPlate(file, camId ?? 0, props.dispatch);
        props.onRunDone();
        return r;
      }}
    />
  );
}
function BehaviorLab(props: { cameras: Camera[]; dispatch: boolean; onRunDone: () => void }) {
  return (
    <FileLab
      title="Behavior analyse"
      helpText="Runs YOLO + tracker on the still, evaluates against this camera's zones."
      cameras={props.cameras}
      dispatch={props.dispatch}
      requireCamera
      run={async (file, camId) => {
        if (camId == null) return { error: "Pick a camera first." };
        const r = await analyseBehavior(camId, file, props.dispatch);
        props.onRunDone();
        return r;
      }}
    />
  );
}

// ─── face override + enrolled list ─────────────────────────────────

function FaceOverrides({
  cameras, caps, onChanged,
}: {
  cameras: Camera[];
  caps: CapabilitiesResponse | null;
  onChanged: () => void;
}) {
  if (!caps) return null;
  const overrides = caps.overrides || {};
  const nativeMap = caps.cameras || {};

  const toggle = async (camId: number, on: boolean) => {
    try {
      await setCapabilityOverride(camId, "face_recognition", on ? true : null);
    } catch (e) {
      alert("Override failed: " + e);
    } finally {
      onChanged();
    }
  };

  return (
    <Section title="Face recognition overrides">
      {cameras.length === 0 ? (
        <p className="muted">No cameras configured.</p>
      ) : (
        <ul className="row-list">
          {cameras.map((c) => {
            const ov = overrides[String(c.camera_id)] || {};
            const forced = ov.face_recognition === true;
            const native = (nativeMap[String(c.camera_id)] || {}).face_recognition;
            return (
              <li key={c.camera_id}>
                <div className="row">
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={forced}
                      onChange={(e) => toggle(c.camera_id, e.target.checked)}
                    />
                    Force Jetson FR on cam {c.camera_id}
                  </label>
                  <span className="muted">{c.endpoint}</span>
                  <span className="spacer" />
                  <Pill tone={native ? "idle" : "ok"}>
                    effective: {native ? "native" : "jetson"}
                  </Pill>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function EnrolledList({ data }: { data: EnrolledFacesResponse | null }) {
  return (
    <Section title="Jetson face DB">
      {!data ? <p className="muted">Loading…</p> :
       !data.available ? <p className="muted">Face engine not wired.</p> :
       !data.people.length ? (
         <p className="muted">Nobody enrolled yet — add workers in the Workers tab.</p>
       ) : (
        <ul className="row-list">
          {data.people.map((p) => (
            <li key={p.person_id}>
              <div className="row">
                <span className="kind">{p.full_name}</span>
                <span className="muted">· {p.person_id}</span>
                <span className="spacer" />
                <span className="muted">{p.dim}-d</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ─── openai validator ─────────────────────────────────────────────

function OpenAIPanel({ data, onRun }: { data: OpenAIStatus | null; onRun: () => void }) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [scenario, setScenario] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [zone, setZone] = useState("");
  const [conf, setConf] = useState("");
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!scenario && data?.scenarios?.length) setScenario(data.scenarios[0]);
  }, [data, scenario]);

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!scenario) { setResult({ error: "Pick a scenario." }); return; }
    if (!file) { setResult({ error: "Pick an image first." }); return; }
    setBusy(true);
    setResult({ status: "running… (vision calls take 2–6s)" });
    try {
      const r = await runOpenAIScenario(scenario, file, {
        camera_id: cameraId, zone_name: zone, confidence: conf,
      });
      setResult(r);
      onRun();
    } catch (e) {
      setResult({ error: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={<>OpenAI validator <OpenAIPill data={data} /></>}>
      {!data ? <p className="muted">Loading…</p> :
       !data.wired ? (
        <p className="muted">{data.reason || "Set ITIPS_OPENAI_ENABLED=true + ITIPS_OPENAI_API_KEY."}</p>
       ) : (
        <>
          <div className="muted">
            model={data.model ?? "?"}, tokens (1h){" "}
            {(data.tokens_used_hour ?? 0).toLocaleString()}
            {data.tokens_cap_hour ? ` / ${data.tokens_cap_hour.toLocaleString()}` : ""}
          </div>
          <div className="form-row" style={{ marginTop: "0.75rem" }}>
            <label className="field"><span>Scenario</span>
              <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
                {(data.scenarios ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="field"><span>Image</span><input ref={fileRef} type="file" accept="image/*" /></label>
            <label className="field"><span>Camera ID</span><input type="text" value={cameraId} onChange={(e) => setCameraId(e.target.value)} /></label>
            <label className="field"><span>Zone name</span><input type="text" value={zone} onChange={(e) => setZone(e.target.value)} /></label>
            <label className="field"><span>Local confidence</span><input type="text" value={conf} onChange={(e) => setConf(e.target.value)} /></label>
          </div>
          <button className="primary" disabled={busy} onClick={submit}>
            {busy ? "Running…" : "Run scenario"}
          </button>
          {result !== null && <JsonView value={result} />}
        </>
       )}
    </Section>
  );
}

function OpenAIPill({ data }: { data: OpenAIStatus | null }) {
  if (!data) return <Pill tone="idle">…</Pill>;
  if (!data.wired) return <Pill tone="idle">not wired</Pill>;
  return <Pill tone={data.enabled ? "ok" : "warn"}>{data.enabled ? "ready" : "disabled"}</Pill>;
}
