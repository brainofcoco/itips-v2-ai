// Types mirroring the Python public API responses.
// Update these as the API evolves — they are the single source of
// truth for what each page expects from the backend.

// ─── cameras ─────────────────────────────────────────────────────────
export interface Camera {
  camera_id: number;
  endpoint: string;
  workers_group_id?: string | null;
  ptz_connected?: boolean;
}
export interface CamerasResponse { cameras: Camera[] }

export interface CameraPreset { name: string; index: number }
export interface PresetsResponse { presets: CameraPreset[] }

// ─── incidents ───────────────────────────────────────────────────────
export interface IncidentSummary {
  incident_id: string;
  started_utc?: string;
  finalized?: boolean;
}
export interface IncidentsResponse { incidents: IncidentSummary[] }

export interface IncidentCapture {
  filename: string;
  bytes?: number;
}

export interface IncidentDetail {
  incident_id: string;
  metadata?: Record<string, any>;
  manifest?: Record<string, any>;
  signature?: Record<string, any>;
  face_captures: IncidentCapture[];
  plate_captures: IncidentCapture[];
  sensor_captures?: IncidentCapture[];
  video_files?: string[];
  has_pdf?: boolean;
}

export interface SignatureVerifyResponse {
  ok: boolean;
  files?: Array<{ name: string; status: string }>;
}

// ─── sensors ─────────────────────────────────────────────────────────
export interface ListenerStatus {
  wired: boolean;
  reason?: string;
  host?: string;
  connected?: boolean;
  armed?: boolean;
  last_error?: string;
}

export interface SensorMapping {
  zone_id: number;
  camera_id: number;
  preset_name: string;
  sensor_type?: string;
  description?: string;
}
export interface SensorMappingsResponse {
  available: boolean;
  mappings: SensorMapping[];
}

export interface SensorEvent {
  zone_id: number;
  event_type: string;
  source: string;
  received_ts: number;
  outcome?: Record<string, any>;
}
export interface SensorEventsResponse {
  available: boolean;
  events: SensorEvent[];
}

export interface SirenSummary { ok: boolean; sirens?: Array<{ status: string; tamperEvident?: boolean }> }

// ─── hub state (AX PRO snapshot) ─────────────────────────────────────
export type ArmingMode = "away" | "stay" | "disarm" | string;

export interface HubSubsystem {
  id: number;
  name: string;
  enabled: boolean;
  arming: ArmingMode;
  alarm: boolean;
  delayTime?: number;
}

export interface HubZone {
  id: number;
  name: string;
  detectorType: string;
  status: "online" | "offline" | string;
  armed: boolean;
  bypassed: boolean;
  alarm: boolean;
  tamperEvident: boolean;
  subSystemNo: number;
  charge?: "normal" | string;
  chargeValue?: number;
  signal?: number;
  temperature?: number;
  humidity?: number;
  zoneType?: string;
  model?: string;
  version?: string;
  magnetOpenStatus?: boolean;
}

export interface HubStateResponse {
  ok: boolean;
  subsystems: HubSubsystem[];
  zones: HubZone[];
}

// ─── workers ─────────────────────────────────────────────────────────
export interface Worker {
  person_id: string;
  full_name: string;
  cameras?: Record<string, string>;
  jetson_enrolled?: boolean;
}
export interface WorkersResponse {
  workers: Worker[];
  available_cameras?: number[];
}
export interface CameraWorkersResponse {
  people: Array<{ uid: string; name: string; sex?: string }>;
}

// ─── health ──────────────────────────────────────────────────────────
export type ProbeStatus = "ok" | "missing" | "error" | "unknown" | "n/a";
export interface HealthCheck {
  name: string;
  label: string;
  status: ProbeStatus;
  category: "core" | "media" | "ai" | "control" | "events";
  detail?: string;
  backup_hint?: string;
}
export interface HealthCamera {
  camera_id: number;
  endpoint: string;
  model?: string;
  green_count: number;
  red_count: number;
  checks: HealthCheck[];
  backup_recommendations?: Array<{ label: string; hint: string }>;
}
export interface HealthResponse {
  cameras: HealthCamera[];
  timestamp?: string;
  cached?: boolean;
}

export interface CapabilitiesResponse {
  available: boolean;
  cameras: Record<string, Record<string, boolean>>;
  overrides: Record<string, Record<string, boolean>>;
  behavior_engine_ready?: boolean;
}

// ─── ml ──────────────────────────────────────────────────────────────
export interface MLEngineState { wired: boolean; ready: boolean }
export interface MLStatus { face: MLEngineState; plate: MLEngineState; behavior: MLEngineState }
export interface EnrolledFace { person_id: string; full_name: string; dim: number }
export interface EnrolledFacesResponse { available: boolean; people: EnrolledFace[] }

export interface OpenAIVerdict {
  scenario: string;
  verdict: string;
  category: string;
  confidence: number;
  summary?: string;
  tokens_used?: number;
  cached?: boolean;
  ts?: number;
}
export interface OpenAIStatus {
  wired: boolean;
  enabled?: boolean;
  reason?: string;
  model?: string;
  tokens_used_hour?: number;
  tokens_cap_hour?: number;
  scenarios?: string[];
  recent?: OpenAIVerdict[];
}

// ─── zones ───────────────────────────────────────────────────────────
export type ZoneType = "region" | "line";
export type ZoneDirection = "Any" | "LeftToRight" | "RightToLeft";
export interface Zone {
  zone_id: string;
  zone_type: ZoneType;
  points: Array<[number, number]>;
  name?: string;
  direction?: ZoneDirection;
  // Camera-side PTZ preset this zone is bound to. null/undefined means
  // "always active" (suits fixed-view cameras).
  preset_name?: string | null;
}
export interface CurrentPresetsResponse {
  available: boolean;
  cameras: Record<string, string | null>;
}

// ─── activity feed (real-time detections) ────────────────────────────
export interface ActivityEvent {
  seq: number;
  camera_id: number;
  kind: string;            // line_crossing | intrusion | loitering | sensor
  label: string;
  zone_id?: string | number | null;
  zone_name?: string;
  detail?: Record<string, any>;
  status: string;          // detected | evaluating
  source: string;          // behavior | camera | axpro
  ts: number;              // unix seconds
}
export interface ActivityResponse {
  available: boolean;
  events: ActivityEvent[];
}
export interface ZonesResponse {
  available: boolean;
  zones: Zone[];
}

// ─── webhooks ────────────────────────────────────────────────────────
export interface WebhookSubscriber {
  id: string;
  url: string;
  event_filter: string[];
  enabled: boolean;
  secret_present?: boolean;
  description?: string;
  created_at?: string;
  last_delivery_at?: string;
  consecutive_failures?: number;
}
export interface WebhookEventKind {
  name: string;
  description: string;
}
export interface WebhookEventKindsResponse {
  kinds: WebhookEventKind[];
}
export interface WebhookSubscribersResponse {
  subscribers: WebhookSubscriber[];
}
export interface WebhookDelivery {
  id: string;
  subscriber_id: string;
  event_id: string;
  event_kind: string;
  attempt: number;
  status_code: number | null;
  error: string | null;
  duration_ms: number;
  delivered_at: number;  // unix seconds (Python time.time())
}
export interface WebhookDeliveriesResponse {
  deliveries: WebhookDelivery[];
}
