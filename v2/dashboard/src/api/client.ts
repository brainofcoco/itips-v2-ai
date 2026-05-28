// Typed fetch wrappers for the Python /api/* endpoints.
// All paths go through the Vite dev proxy → itips:5050, or nginx in prod.
import type {
  CameraWorkersResponse, CamerasResponse, Camera, CapabilitiesResponse,
  EnrolledFacesResponse, HealthResponse, IncidentDetail, IncidentsResponse,
  ListenerStatus, MLStatus, OpenAIStatus, PresetsResponse,
  SensorEventsResponse, SensorMappingsResponse, SensorMapping,
  SignatureVerifyResponse, SirenSummary, WebhookDeliveriesResponse,
  WebhookEventKindsResponse, WebhookSubscriber, WebhookSubscribersResponse,
  WorkersResponse, Zone, ZonesResponse,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`${path} → ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} → ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", credentials: "same-origin" });
  if (!res.ok) throw new Error(`${path} → ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

// Multipart POST that doesn't blow up on non-JSON error responses
// (nginx 504 HTML pages, 413 entity-too-large, etc.). Returns the
// parsed JSON on success and an `{error, http_status}` envelope on
// failure so the JsonView in the ML Lab shows something readable
// instead of "Unexpected token '<'".
async function postForm(url: string, form: FormData): Promise<any> {
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", body: form });
  } catch (e) {
    return { error: `network failure: ${e}` };
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try { return await res.json(); }
    catch (e) { return { error: `bad JSON in ${res.status} response: ${e}`, http_status: res.status }; }
  }
  // Fall back to text and try to make sense of common upstream pages.
  const raw = await res.text();
  const stripped = raw.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 280);
  const hint =
    res.status === 504 ? " — request exceeded nginx proxy_read_timeout. Bump it or retry."
    : res.status === 502 ? " — upstream (itips) did not respond. Check container logs."
    : res.status === 413 ? " — payload too large for nginx client_max_body_size."
    : "";
  return {
    error: `HTTP ${res.status} ${res.statusText}${hint}`,
    body_preview: stripped || "(empty)",
    http_status: res.status,
  };
}

// ─── cameras ─────────────────────────────────────────────────────────
export async function fetchCameras(): Promise<Camera[]> {
  const body = await getJson<CamerasResponse | Camera[]>("/api/cameras");
  if (Array.isArray(body)) return body;
  return body.cameras ?? [];
}
export async function fetchPresets(cameraId: number): Promise<PresetsResponse> {
  return getJson<PresetsResponse>(`/api/cameras/${cameraId}/presets`);
}
export async function saveCurrentPreset(
  cameraId: number, name?: string, index?: number,
): Promise<{
  ok: boolean;
  preset?: { index: number; name: string };
  name_warning?: string;
  error?: string;
}> {
  const body: Record<string, unknown> = {};
  if (name) body.name = name;
  if (index != null) body.index = index;
  return postJson(`/api/cameras/${cameraId}/presets`, body);
}
export async function deletePreset(
  cameraId: number, index: number,
): Promise<{ ok: boolean; error?: string }> {
  return del(`/api/cameras/${cameraId}/presets/${index}`);
}
export async function gotoPreset(
  cameraId: number, index: number,
): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/cameras/${cameraId}/presets/${index}/goto`, {});
}

export type PtzDirection =
  | "up" | "down" | "left" | "right"
  | "left_up" | "right_up" | "left_down" | "right_down"
  | "zoom_wide" | "zoom_tele"
  | "focus_near" | "focus_far";

export async function ptzJog(
  cameraId: number, direction: PtzDirection,
  action: "start" | "stop", speed = 4,
): Promise<{ ok: boolean; error?: string; hint?: string }> {
  return postJson(`/api/test/ptz/${cameraId}/${direction}`, { action, speed });
}
export async function fireDeterrence(cameraId: number): Promise<void> {
  await postJson(`/api/deterrence/${cameraId}/fire`, { light: true, speaker: true });
}
export async function standdownDeterrence(cameraId: number): Promise<void> {
  await postJson(`/api/deterrence/${cameraId}/standdown`, {});
}
export function snapshotUrl(cameraId: number, cacheBuster?: number): string {
  return `/api/snapshot/${cameraId}?t=${cacheBuster ?? Date.now()}`;
}
export function liveStreamUrl(cameraId: number): string {
  return `/live/${cameraId}?t=${Date.now()}`;
}

// ─── incidents ───────────────────────────────────────────────────────
export async function fetchIncidents(): Promise<IncidentsResponse> {
  return getJson<IncidentsResponse>("/api/incidents");
}
export async function fetchIncident(id: string): Promise<IncidentDetail> {
  return getJson<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`);
}
export async function verifyIncident(id: string): Promise<SignatureVerifyResponse> {
  return postJson<SignatureVerifyResponse>(
    `/api/incidents/${encodeURIComponent(id)}/verify`,
  );
}
export function incidentFileUrl(id: string, name: string, inline = false): string {
  const base = `/api/incidents/${encodeURIComponent(id)}/files/${encodeURI(name)}`;
  return inline ? `${base}?inline=1` : base;
}
export async function runTestIncident(): Promise<{ ok: boolean; incident_id?: string; error?: string }> {
  return postJson("/api/evidence/test/run");
}

// ─── sensors ─────────────────────────────────────────────────────────
export async function fetchListenerStatus(): Promise<ListenerStatus> {
  return getJson<ListenerStatus>("/api/sensors/listener/status");
}
export async function fetchSensorMappings(): Promise<SensorMappingsResponse> {
  return getJson<SensorMappingsResponse>("/api/sensors/map");
}
export async function upsertSensorMapping(m: SensorMapping): Promise<{ ok: boolean; error?: string }> {
  return postJson("/api/sensors/map", m);
}
export async function deleteSensorMapping(zoneId: number): Promise<{ ok: boolean; error?: string }> {
  return del(`/api/sensors/map/${zoneId}`);
}
export async function simulateSensor(zoneId: number, sensorType?: string): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/sensors/simulate/${zoneId}`, {
    event_type: sensorType || "doorContact",
  });
}
export async function fetchRecentSensorEvents(limit = 30): Promise<SensorEventsResponse> {
  return getJson<SensorEventsResponse>(`/api/sensors/events/recent?limit=${limit}`);
}

// hub controls
export async function fetchSirenStatus(): Promise<SirenSummary> {
  return getJson<SirenSummary>("/api/sensors/hub/siren/status");
}
export async function startSiren(subId: number): Promise<{ ok: boolean; error?: string }> {
  return postJson("/api/sensors/hub/siren/start", { sub_id: subId });
}
export async function stopSiren(subId: number): Promise<{ ok: boolean; error?: string }> {
  return postJson("/api/sensors/hub/siren/stop", { sub_id: subId });
}
export async function testSiren(subId: number, durationS = 2.0): Promise<{ ok: boolean; error?: string }> {
  return postJson("/api/sensors/hub/siren/test", { sub_id: subId, duration_s: durationS });
}
export async function disarmHub(subIds?: number[]): Promise<{ ok: boolean; error?: string }> {
  return postJson("/api/sensors/hub/disarm", subIds ? { sub_ids: subIds } : {});
}
export async function armHubAway(
  subIds?: number[], clearBypass?: number[],
): Promise<{ ok: boolean; error?: string }> {
  const body: Record<string, unknown> = {};
  if (subIds) body.sub_ids = subIds;
  if (clearBypass) body.clear_bypass = clearBypass;
  return postJson("/api/sensors/hub/arm-away", body);
}
export async function bypassZone(zoneId: number): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/sensors/zones/${zoneId}/bypass`, {});
}
export async function unbypassZone(zoneId: number): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/sensors/zones/${zoneId}/unbypass`, {});
}
export async function fetchHubState(): Promise<import("./types").HubStateResponse> {
  return getJson<import("./types").HubStateResponse>("/api/sensors/hub/state");
}

// ─── workers ─────────────────────────────────────────────────────────
export async function fetchWorkers(): Promise<WorkersResponse> {
  return getJson<WorkersResponse>("/api/workers");
}
export async function fetchCameraWorkers(cameraId: number): Promise<CameraWorkersResponse> {
  return getJson<CameraWorkersResponse>(`/api/workers/${cameraId}`);
}
export async function enrollWorker(
  fullName: string, image: File, personId?: string, sex?: string,
): Promise<{ ok: boolean; ml_enrolled?: boolean; ml_error?: string; cameras?: Record<string,string>; failures?: string[]; error?: string }> {
  const form = new FormData();
  form.append("full_name", fullName);
  if (personId) form.append("person_id", personId);
  if (sex) form.append("sex", sex);
  form.append("image", image);
  const res = await fetch("/api/workers", { method: "POST", body: form });
  return res.json();
}
export async function deleteWorker(personId: string): Promise<{ ok: boolean; error?: string }> {
  return del(`/api/workers/${encodeURIComponent(personId)}`);
}

// ─── health ──────────────────────────────────────────────────────────
export async function fetchHealth(force = false): Promise<HealthResponse> {
  const url = force ? "/api/health/cameras?force=1" : "/api/health/cameras";
  return getJson<HealthResponse>(url);
}
export async function fetchCapabilities(): Promise<CapabilitiesResponse> {
  return getJson<CapabilitiesResponse>("/api/health/capabilities");
}
export async function setCapabilityOverride(
  cameraId: number, cap: string, forceFallback: boolean | null,
): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/health/capabilities/${cameraId}/${cap}/override`, {
    force_fallback: forceFallback,
  });
}

// ─── ml ──────────────────────────────────────────────────────────────
export async function fetchMLStatus(): Promise<MLStatus> {
  return getJson<MLStatus>("/api/ml/status");
}
export async function warmupML(): Promise<MLStatus> {
  return postJson<MLStatus>("/api/ml/warmup");
}
export async function fetchEnrolledFaces(): Promise<EnrolledFacesResponse> {
  return getJson<EnrolledFacesResponse>("/api/ml/face/enrolled");
}
export async function fetchOpenAIStatus(): Promise<OpenAIStatus> {
  return getJson<OpenAIStatus>("/api/ml/openai/status");
}
export async function recognizeFace(file: File, cameraId = 0, dispatch = false): Promise<any> {
  const form = new FormData();
  form.append("image", file);
  form.append("camera_id", String(cameraId));
  const url = `/api/ml/face/recognize${dispatch ? "?dispatch=1" : ""}`;
  return postForm(url, form);
}
export async function readPlate(file: File, cameraId = 0, dispatch = false): Promise<any> {
  const form = new FormData();
  form.append("image", file);
  form.append("camera_id", String(cameraId));
  const url = `/api/ml/plate/read${dispatch ? "?dispatch=1" : ""}`;
  return postForm(url, form);
}
export async function analyseBehavior(cameraId: number, file: File, dispatch = false): Promise<any> {
  const form = new FormData();
  form.append("image", file);
  const url = `/api/ml/behavior/${cameraId}/analyse${dispatch ? "?dispatch=1" : ""}`;
  return postForm(url, form);
}
export async function runOpenAIScenario(
  scenario: string, file: File, extras: Record<string, string> = {},
): Promise<any> {
  const form = new FormData();
  form.append("image", file);
  for (const [k, v] of Object.entries(extras)) if (v) form.append(k, v);
  return postForm(`/api/ml/openai/validate/${scenario}`, form);
}

// ─── zones ───────────────────────────────────────────────────────────
export async function fetchZones(cameraId: number): Promise<ZonesResponse> {
  return getJson<ZonesResponse>(`/api/zones/${cameraId}`);
}
export async function saveZone(cameraId: number, zone: Zone): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/zones/${cameraId}`, zone);
}
export async function deleteZone(cameraId: number, zoneId: string): Promise<{ ok: boolean; error?: string }> {
  return del(`/api/zones/${cameraId}/${encodeURIComponent(zoneId)}`);
}

// ─── webhooks ────────────────────────────────────────────────────────
export async function fetchWebhookKinds(): Promise<WebhookEventKindsResponse> {
  return getJson<WebhookEventKindsResponse>("/api/webhooks/event-kinds");
}
export async function fetchWebhookSubscribers(): Promise<WebhookSubscribersResponse> {
  return getJson<WebhookSubscribersResponse>("/api/webhooks/subscribers");
}
export async function createWebhookSubscriber(
  payload: { url: string; event_filter?: string[]; description?: string; enabled?: boolean },
): Promise<{ ok: boolean; subscriber: WebhookSubscriber & { secret?: string } }> {
  return postJson("/api/webhooks/subscribers", payload);
}
export async function deleteWebhookSubscriber(id: string): Promise<{ ok: boolean }> {
  return del(`/api/webhooks/subscribers/${encodeURIComponent(id)}`);
}
export async function testWebhookSubscriber(id: string): Promise<{ ok: boolean; queued?: number; error?: string }> {
  return postJson(`/api/webhooks/subscribers/${encodeURIComponent(id)}/test`);
}
export async function rotateWebhookSecret(id: string): Promise<{ ok: boolean; subscriber?: WebhookSubscriber & { secret?: string } }> {
  return postJson(`/api/webhooks/subscribers/${encodeURIComponent(id)}/rotate-secret`);
}
export async function fetchWebhookDeliveries(): Promise<WebhookDeliveriesResponse> {
  return getJson<WebhookDeliveriesResponse>("/api/webhooks/deliveries");
}
