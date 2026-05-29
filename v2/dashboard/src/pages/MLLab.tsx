import { useCallback, useEffect, useRef, useState } from "react";
import {
  analyseBehavior, fetchCameras, fetchCapabilities, fetchEnrolledFaces,
  fetchMLStatus, fetchOpenAIStatus, readPlate, recognizeFace,
  runOpenAIScenario, setCapabilityOverride, snapshotUrl, warmupML,
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
      <OpenAIPanel cameras={cameras} data={openai} onRun={reloadAll} />
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

// ─── shared source picker ─────────────────────────────────────────

type SourceKind = "file" | "live";

interface SourcePickerProps {
  source: SourceKind;
  onSourceChange: (s: SourceKind) => void;
  fileRef: React.RefObject<HTMLInputElement>;
  cameraId: number | null;
  onCameraChange: (id: number | null) => void;
  cameras: Camera[];
  requireCamera?: boolean;
}

// Lets the user pick whether the model runs on an uploaded still or on
// a fresh JPEG grabbed from a camera's snapshot endpoint. The camera
// selector serves a different role per mode: in "live" it's the
// snapshot source AND the dispatch context; in "file" it's just the
// dispatch context (telling AlertEngine "pretend this image came from
// cam N").
function SourcePicker({
  source, onSourceChange, fileRef, cameraId, onCameraChange, cameras, requireCamera,
}: SourcePickerProps) {
  const [livePreviewTs, setLivePreviewTs] = useState(0);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileObjectUrl, setFileObjectUrl] = useState<string | null>(null);

  useEffect(() => () => { if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl); }, [fileObjectUrl]);

  // Auto-refresh the live thumbnail when the user switches into live mode
  // or changes camera, so they see what they're about to run on.
  useEffect(() => {
    if (source === "live") setLivePreviewTs(Date.now());
  }, [source, cameraId]);

  const handleFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) { setFileName(null); setFileObjectUrl(null); return; }
    setFileName(f.name);
    if (fileObjectUrl) URL.revokeObjectURL(fileObjectUrl);
    setFileObjectUrl(URL.createObjectURL(f));
  };

  const camHelp = source === "live"
    ? "live source — snapshot pulled from this camera, and dispatch context."
    : "dispatch context — AlertEngine treats the upload as if it came from this camera.";

  return (
    <div className="lab-source">
      <div className="lab-source-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={source === "file"}
          className={`lab-tab${source === "file" ? " active" : ""}`}
          onClick={() => onSourceChange("file")}
        >
          📁 Upload image
        </button>
        <button
          role="tab"
          aria-selected={source === "live"}
          className={`lab-tab${source === "live" ? " active" : ""}`}
          onClick={() => onSourceChange("live")}
          disabled={cameras.length === 0}
          title={cameras.length === 0 ? "No cameras configured" : "Grab a fresh snapshot from a camera"}
        >
          📷 Live snapshot
        </button>
      </div>

      <div className="lab-source-grid">
        <div className="lab-source-input">
          {source === "file" ? (
            <label className="field">
              <span>Image file</span>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                onChange={handleFilePicked}
              />
              {fileObjectUrl && (
                <img className="lab-thumb" src={fileObjectUrl} alt={fileName ?? "preview"} />
              )}
            </label>
          ) : (
            <div className="field">
              <span>Live snapshot</span>
              {cameraId != null ? (
                <div className="lab-live">
                  <img
                    className="lab-thumb"
                    src={`${snapshotUrl(cameraId)}&_=${livePreviewTs}`}
                    alt={`cam${cameraId} preview`}
                  />
                  <button
                    className="lab-refresh"
                    onClick={() => setLivePreviewTs(Date.now())}
                    title="Refresh preview — the Run button always pulls a fresh frame"
                  >
                    ↻ Refresh preview
                  </button>
                </div>
              ) : (
                <p className="muted small">Pick a camera to preview.</p>
              )}
            </div>
          )}
        </div>

        <label className="field">
          <span>
            Camera {source === "live" ? "(live source)" : "(dispatch context)"}
          </span>
          <select
            value={cameraId ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              onCameraChange(v === "" ? null : parseInt(v, 10));
            }}
          >
            {!requireCamera && source === "file" && (
              <option value="">— none —</option>
            )}
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                cam{c.camera_id} · {c.endpoint}
              </option>
            ))}
            {cameras.length === 0 && <option value="">(no cameras)</option>}
          </select>
          <span className="muted small">{camHelp}</span>
        </label>
      </div>
    </div>
  );
}

// Pull a fresh JPEG from the snapshot endpoint and wrap it as a File so
// the existing multipart-upload APIs work unchanged.
async function fetchSnapshotAsFile(cameraId: number): Promise<File> {
  const url = snapshotUrl(cameraId);
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) {
    throw new Error(`snapshot ${cameraId} → ${res.status} ${res.statusText}`);
  }
  const blob = await res.blob();
  return new File(
    [blob],
    `cam${cameraId}-${Date.now()}.jpg`,
    { type: blob.type || "image/jpeg" },
  );
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
  const [source, setSource] = useState<SourceKind>("file");
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (cameraId == null && cameras.length) setCameraId(cameras[0].camera_id);
  }, [cameras, cameraId]);

  const onRun = async () => {
    let file: File | null = null;
    if (source === "file") {
      file = fileRef.current?.files?.[0] ?? null;
      if (!file) { setResult({ error: "Pick an image file first." }); return; }
    } else {
      if (cameraId == null) {
        setResult({ error: "Pick a camera to snapshot from." });
        return;
      }
      setBusy(true);
      setResult({ status: `grabbing snapshot from cam${cameraId}…` });
      try {
        file = await fetchSnapshotAsFile(cameraId);
      } catch (e) {
        setResult({ error: String(e) });
        setBusy(false);
        return;
      }
    }
    if (requireCamera && cameraId == null) {
      setResult({ error: "Pick a camera first." });
      setBusy(false);
      return;
    }
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
      <SourcePicker
        source={source}
        onSourceChange={setSource}
        fileRef={fileRef}
        cameraId={cameraId}
        onCameraChange={setCameraId}
        cameras={cameras}
        requireCamera={requireCamera}
      />
      <button className="primary" disabled={busy} onClick={onRun}>
        {busy ? "Running…" : source === "live"
          ? `Snapshot + run ${title.toLowerCase()}`
          : `Run ${title.toLowerCase()}`}
      </button>
      {result !== null && <JsonView value={result} />}
    </Section>
  );
}

function FaceLab(props: { cameras: Camera[]; dispatch: boolean; onRunDone: () => void }) {
  return (
    <FileLab
      title="Face recognize"
      helpText="InsightFace against the chosen image. Returns person_id, similarity, and embedding stats."
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
      helpText="PaddleOCR against the chosen image. Returns plate text + per-character confidence."
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
      helpText="YOLO + tracker on the chosen image, evaluated against the selected camera's zones."
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

function OpenAIPanel({
  cameras, data, onRun,
}: { cameras: Camera[]; data: OpenAIStatus | null; onRun: () => void }) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [scenario, setScenario] = useState("");
  const [cameraId, setCameraId] = useState<number | null>(null);
  const [source, setSource] = useState<SourceKind>("file");
  const [zone, setZone] = useState("");
  const [conf, setConf] = useState("");
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!scenario && data?.scenarios?.length) setScenario(data.scenarios[0]);
  }, [data, scenario]);

  useEffect(() => {
    if (cameraId == null && cameras.length) setCameraId(cameras[0].camera_id);
  }, [cameras, cameraId]);

  const submit = async () => {
    if (!scenario) { setResult({ error: "Pick a scenario." }); return; }
    let file: File | null = null;
    if (source === "file") {
      file = fileRef.current?.files?.[0] ?? null;
      if (!file) { setResult({ error: "Pick an image file first." }); return; }
    } else {
      if (cameraId == null) {
        setResult({ error: "Pick a camera to snapshot from." });
        return;
      }
      setBusy(true);
      setResult({ status: `grabbing snapshot from cam${cameraId}…` });
      try {
        file = await fetchSnapshotAsFile(cameraId);
      } catch (e) {
        setResult({ error: String(e) });
        setBusy(false);
        return;
      }
    }
    setBusy(true);
    setResult({ status: "running… (vision calls take 2–6s)" });
    try {
      const r = await runOpenAIScenario(scenario, file, {
        camera_id: cameraId != null ? String(cameraId) : "",
        zone_name: zone,
        confidence: conf,
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
            <label className="field"><span>Zone name</span>
              <input type="text" value={zone} onChange={(e) => setZone(e.target.value)} />
            </label>
            <label className="field"><span>Local confidence</span>
              <input type="text" value={conf} onChange={(e) => setConf(e.target.value)} />
            </label>
          </div>
          <SourcePicker
            source={source}
            onSourceChange={setSource}
            fileRef={fileRef}
            cameraId={cameraId}
            onCameraChange={setCameraId}
            cameras={cameras}
          />
          <button className="primary" disabled={busy} onClick={submit}>
            {busy ? "Running…" : source === "live" ? "Snapshot + run scenario" : "Run scenario"}
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
