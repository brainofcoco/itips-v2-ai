"""Single source of truth for non-secret configuration.

Every tunable lives here and reads from the environment (`.env` in dev,
container env in prod). Code reads attributes off `settings`; nothing
else should call `os.getenv` directly.

Secrets are kept out of this module — they belong in `.env` and are read
on-demand by the components that need them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw else default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw else default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_json(key: str, default):
    raw = _env(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


@dataclass(frozen=True)
class TenantConfig:
    site_id: str = field(default_factory=lambda: _env("ITIPS_SITE_ID"))
    operator_id: str = field(default_factory=lambda: _env("ITIPS_OPERATOR_ID"))
    device_id: str = field(default_factory=lambda: _env("ITIPS_DEVICE_ID"))
    latitude: float = field(default_factory=lambda: _env_float("ITIPS_SITE_LATITUDE", 0.0))
    longitude: float = field(default_factory=lambda: _env_float("ITIPS_SITE_LONGITUDE", 0.0))
    state: str = field(default_factory=lambda: _env("ITIPS_SITE_STATE"))
    zone: str = field(default_factory=lambda: _env("ITIPS_SITE_GEOPOLITICAL_ZONE"))


@dataclass(frozen=True)
class CameraConfig:
    rtsp_urls: dict[int, str] = field(default_factory=lambda: {
        i: _env(f"ITIPS_CAMERA_{i}_RTSP") for i in (1, 2, 3, 4)
    })
    max_frame_width: int = field(default_factory=lambda: _env_int("ITIPS_CAMERA_MAX_FRAME_WIDTH", 1920))

    def active(self) -> dict[int, str]:
        return {cam_id: url for cam_id, url in self.rtsp_urls.items() if url}


@dataclass(frozen=True)
class DetectionConfig:
    yolo_model: str = field(default_factory=lambda: _env("ITIPS_YOLO_MODEL", "yolo11n.pt"))
    yolo_fallback: str = field(default_factory=lambda: _env("ITIPS_YOLO_FALLBACK_MODEL", "yolo11n.pt"))
    yolo_img_size: int = field(default_factory=lambda: _env_int("ITIPS_YOLO_IMG_SIZE", 640))
    yolo_confidence: float = field(default_factory=lambda: _env_float("ITIPS_YOLO_CONFIDENCE", 0.35))
    yolo_iou: float = field(default_factory=lambda: _env_float("ITIPS_YOLO_IOU", 0.5))
    insightface_pack: str = field(default_factory=lambda: _env("ITIPS_INSIGHTFACE_PACK", "buffalo_l"))
    insightface_det_size: int = field(default_factory=lambda: _env_int("ITIPS_INSIGHTFACE_DET_SIZE", 640))
    face_match_threshold: float = field(default_factory=lambda: _env_float("ITIPS_FACE_MATCH_THRESHOLD", 0.6))
    face_margin_threshold: float = field(default_factory=lambda: _env_float("ITIPS_FACE_MARGIN_THRESHOLD", 0.05))
    face_auth_ttl_seconds: int = field(default_factory=lambda: _env_int("ITIPS_FACE_AUTH_TTL_SECONDS", 120))
    plate_recognizer_url: str = field(default_factory=lambda: _env("ITIPS_PLATE_RECOGNIZER_URL"))
    plate_recognizer_token: str = field(default_factory=lambda: _env("ITIPS_PLATE_RECOGNIZER_TOKEN"))


@dataclass(frozen=True)
class BehaviourConfig:
    loitering_seconds: int = field(default_factory=lambda: _env_int("ITIPS_LOITERING_SECONDS", 120))
    loitering_gate_seconds: int = field(default_factory=lambda: _env_int("ITIPS_LOITERING_GATE_SECONDS", 30))
    generator_tampering_person_count: int = field(default_factory=lambda: _env_int("ITIPS_GENERATOR_TAMPERING_PERSON_COUNT", 2))


@dataclass(frozen=True)
class SensorConfig:
    host: str = field(default_factory=lambda: _env("AXPRO_HOST"))
    port: int = field(default_factory=lambda: _env_int("ITIPS_AX_PRO_PORT", 80))
    username: str = field(default_factory=lambda: _env("AXPRO_USERNAME"))
    password: str = field(default_factory=lambda: _env("AXPRO_PASSWORD"))
    poll_interval_ms: int = field(default_factory=lambda: _env_int("ITIPS_SENSOR_POLL_INTERVAL_MS", 500))
    zone_to_ptz: dict = field(default_factory=lambda: _env_json("ITIPS_SENSOR_ZONE_PTZ_MAP", {}))


@dataclass(frozen=True)
class IntakeConfig:
    db_path: Path = field(default_factory=lambda: Path(_env("ITIPS_INTAKE_DB_PATH", "/opt/itips/var/intake.sqlite")))
    max_age_days: int = field(default_factory=lambda: _env_int("ITIPS_INTAKE_MAX_AGE_DAYS", 30))


@dataclass(frozen=True)
class ZoneConfig:
    seed_path: Path = field(default_factory=lambda: Path(_env("ITIPS_ZONES_SEED_PATH", "config/zones.example.json")))
    runtime_path: Path = field(default_factory=lambda: Path(_env("ITIPS_ZONES_RUNTIME_PATH", "/opt/itips/var/zones.json")))
    presets_path: Path = field(default_factory=lambda: Path(_env("ITIPS_PRESETS_CONFIG_PATH", "config/presets.example.json")))


@dataclass(frozen=True)
class EvidenceConfig:
    store_path: Path = field(default_factory=lambda: Path(_env("ITIPS_EVIDENCE_STORE_PATH", "/opt/itips/evidence_store")))
    pre_event_seconds: int = field(default_factory=lambda: _env_int("ITIPS_PRE_EVENT_BUFFER_SECONDS", 60))
    post_event_seconds: int = field(default_factory=lambda: _env_int("ITIPS_POST_EVENT_BUFFER_SECONDS", 120))
    # Read on-demand from env to keep the key out of the dataclass repr.
    @property
    def hmac_key_hex(self) -> str:
        return _env("ITIPS_DEVICE_HMAC_KEY")


@dataclass(frozen=True)
class IncidentConfig:
    confirmation_dwell_seconds: float = field(default_factory=lambda: _env_float("ITIPS_INCIDENT_CONFIRMATION_DWELL_SECONDS", 5.0))
    confirmation_window_seconds: float = field(default_factory=lambda: _env_float("ITIPS_INCIDENT_CONFIRMATION_WINDOW_SECONDS", 30.0))
    idle_timeout_seconds: float = field(default_factory=lambda: _env_float("ITIPS_INCIDENT_IDLE_TIMEOUT_SECONDS", 15.0))


@dataclass(frozen=True)
class ApiConfig:
    public_host: str = field(default_factory=lambda: _env("ITIPS_PUBLIC_API_HOST", "0.0.0.0"))
    public_port: int = field(default_factory=lambda: _env_int("ITIPS_PUBLIC_API_PORT", 5050))
    inbound_host: str = field(default_factory=lambda: _env("ITIPS_INBOUND_API_HOST", "0.0.0.0"))
    inbound_port: int = field(default_factory=lambda: _env_int("ITIPS_INBOUND_API_PORT", 8443))
    inbound_tls_cert: str = field(default_factory=lambda: _env("ITIPS_INBOUND_TLS_CERT"))
    inbound_tls_key: str = field(default_factory=lambda: _env("ITIPS_INBOUND_TLS_KEY"))
    @property
    def inbound_token(self) -> str:
        return _env("ITIPS_INBOUND_TOKEN")


@dataclass(frozen=True)
class FeatureFlags:
    zone_editor: bool = field(default_factory=lambda: _env_bool("ITIPS_ENABLE_ZONE_EDITOR", False))
    face_enrollment: bool = field(default_factory=lambda: _env_bool("ITIPS_ENABLE_FACE_ENROLLMENT", False))
    test_endpoints: bool = field(default_factory=lambda: _env_bool("ITIPS_ENABLE_TEST_ENDPOINTS", False))
    simulate: bool = field(default_factory=lambda: _env_bool("ITIPS_SIMULATE", False))


@dataclass(frozen=True)
class Settings:
    mode: str = field(default_factory=lambda: _env("ITIPS_MODE", "prod"))
    log_level: str = field(default_factory=lambda: _env("ITIPS_LOG_LEVEL", "INFO"))
    tenant: TenantConfig = field(default_factory=TenantConfig)
    cameras: CameraConfig = field(default_factory=CameraConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)
    sensors: SensorConfig = field(default_factory=SensorConfig)
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    zones: ZoneConfig = field(default_factory=ZoneConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    incident: IncidentConfig = field(default_factory=IncidentConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    flags: FeatureFlags = field(default_factory=FeatureFlags)


# Singleton — components import this, never re-instantiate.
settings = Settings()
