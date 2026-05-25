"""Pydantic models for the local intake interface.

These shapes ARE the contract between the AI team and the backend Sync
Agent. Any change here requires written sign-off from the backend lead
per PRD §4.7. Adding a field is allowed; renaming or changing semantics
is a contract break.

Priority follows PRD §2.4 verbatim — the Sync Agent drains in ascending
priority order, then ascending id.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Priority(IntEnum):
    """Drain priority per PRD §2.4."""

    RAPID_DISPATCH = 1
    INCIDENT_EVENT = 2
    MEDIA_CAPTURE = 3
    EVIDENCE_PACKAGE = 4
    HEARTBEAT = 5


class IntakePacket(BaseModel):
    """One unit of work for the Sync Agent.

    `endpoint_hint` names the PRD §4.7 outbound endpoint the Sync Agent
    will translate this packet into (A1, A2, ..., A8). The AI side has no
    visibility into how that translation happens — it only emits structure.
    """

    priority: Priority
    endpoint_hint: str = Field(min_length=2, max_length=8)
    payload: dict[str, Any]
    timestamp_utc: str = Field(min_length=20, max_length=40)
    monotonic_ns: int = Field(ge=0)
    incident_id: Optional[str] = None
    operator_id: Optional[str] = None
    site_id: Optional[str] = None

    model_config = {"extra": "forbid"}
