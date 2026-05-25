"""Single source of truth for non-secret configuration.

Every tunable lives here and reads from the environment (`.env` in dev,
container env in prod). Code reads attributes off `settings`; nothing
else should call `os.getenv` directly.

Secrets are kept out of this module — they belong in `.env` and are read
on-demand by the components that need them.
"""

from __future__ import annotations

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

    def active(self) -> dict[int, str]:
        return {cam_id: url for cam_id, url in self.rtsp_urls.items() if url}


@dataclass(frozen=True)
class DahuaConfig:
    """Knobs that govern Dahua client behaviour."""

    workers_group_name: str = field(
        default_factory=lambda: _env("ITIPS_DAHUA_WORKERS_GROUP", "ITIPS_Workers")
    )
    face_similarity: int = field(
        default_factory=lambda: _env_int("ITIPS_DAHUA_FACE_SIMILARITY", 80)
    )
    event_cooldown_s: float = field(
        default_factory=lambda: _env_float("ITIPS_DAHUA_EVENT_COOLDOWN_S", 1.5)
    )


@dataclass(frozen=True)
class IntakeConfig:
    db_path: Path = field(default_factory=lambda: Path(_env(
        "ITIPS_INTAKE_DB_PATH", "/opt/itips/var/intake.sqlite",
    )))
    max_age_days: int = field(default_factory=lambda: _env_int("ITIPS_INTAKE_MAX_AGE_DAYS", 30))


@dataclass(frozen=True)
class EvidenceConfig:
    store_path: Path = field(default_factory=lambda: Path(_env(
        "ITIPS_EVIDENCE_STORE_PATH", "/opt/itips/evidence_store",
    )))
    pre_event_seconds: int = field(default_factory=lambda: _env_int("ITIPS_PRE_EVENT_BUFFER_SECONDS", 60))
    post_event_seconds: int = field(default_factory=lambda: _env_int("ITIPS_POST_EVENT_BUFFER_SECONDS", 120))

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
class Settings:
    mode: str = field(default_factory=lambda: _env("ITIPS_MODE", "prod"))
    log_level: str = field(default_factory=lambda: _env("ITIPS_LOG_LEVEL", "INFO"))
    tenant: TenantConfig = field(default_factory=TenantConfig)
    cameras: CameraConfig = field(default_factory=CameraConfig)
    dahua: DahuaConfig = field(default_factory=DahuaConfig)
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    incident: IncidentConfig = field(default_factory=IncidentConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


# Singleton — components import this, never re-instantiate.
settings = Settings()
