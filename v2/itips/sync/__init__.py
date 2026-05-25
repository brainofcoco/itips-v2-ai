"""Sync Agent intake — the only outbound contract from the AI pipeline.

The AI pipeline writes IntakePacket records to a local SQLite queue. The
Jetson Sync Agent (backend deliverable, separate process) drains the queue
and makes cloud API calls. AI code never reads, retries, or deletes.
"""

from .intake import IntakeWriter
from .schema import IntakePacket, Priority

__all__ = ["IntakePacket", "IntakeWriter", "Priority"]
