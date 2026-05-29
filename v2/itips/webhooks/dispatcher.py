"""Background webhook dispatcher.

Producers publish `WebhookEvent` instances via `dispatch(event)`. The
dispatcher fans out to every matching subscriber, signs with HMAC, and
POSTs with a retry budget. Delivery is best-effort and in-memory — a
process restart loses any in-flight retries.

The dispatcher is deliberately *not* coupled to AlertEngine or the
OpenAIValidator. It exposes adapter helpers (`bind_alert_engine`,
`bind_openai_validator`, `bind_sensor_dispatcher`) so the producers
stay ignorant of the webhook subsystem.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import requests

from itips.webhooks.events import (
    EVENT_KINDS, WebhookEvent, alert_record_to_event,
)
from itips.webhooks.signing import (
    DELIVERY_HEADER, EVENT_HEADER, EVENT_ID_HEADER, SIGNATURE_HEADER,
    TIMESTAMP_HEADER, sign,
)
from itips.webhooks.store import Subscriber, SubscriberStore

logger = logging.getLogger(__name__)


# Auto-disable after this many consecutive failures across deliveries
# (not retries of a single event). Operator must re-enable manually.
_AUTODISABLE_THRESHOLD = 20


@dataclass
class _Job:
    subscriber_id: str
    event: WebhookEvent
    attempt: int = 1


class WebhookDispatcher:
    MAX_RETRIES = 5
    BASE_BACKOFF_S = 2.0

    def __init__(
        self,
        *,
        store: SubscriberStore,
        timeout_s: float = 10.0,
        workers: int = 2,
        max_queue: int = 1000,
    ) -> None:
        self._store = store
        self._timeout_s = float(timeout_s)
        self._n_workers = max(1, int(workers))
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=max_queue)
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # ─── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self._threads:
            return
        for i in range(self._n_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"webhook-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info("WebhookDispatcher: started %d workers", self._n_workers)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._threads:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()

    # ─── public surface ───────────────────────────────────────────

    def dispatch(self, event: WebhookEvent) -> int:
        """Fan out `event` to every matching subscriber. Returns the
        number of deliveries queued."""
        if event.kind not in EVENT_KINDS:
            logger.warning("WebhookDispatcher: refusing unknown kind %r", event.kind)
            return 0
        count = 0
        for sub in self._store.list():
            if not sub.matches(event.kind):
                continue
            try:
                self._queue.put_nowait(_Job(subscriber_id=sub.id, event=event))
                count += 1
            except queue.Full:
                logger.warning(
                    "WebhookDispatcher: queue full, dropping %s for %s",
                    event.kind, sub.id,
                )
        return count

    def dispatch_test(self, subscriber_id: str, *,
                      tenant) -> int:
        """Fire a `test.ping` to one subscriber, bypassing event_filter.

        `tenant` is the global TenantConfig — used to stamp the same
        site_id / operator_id / device_id real events would carry."""
        sub = self._store.get(subscriber_id)
        if sub is None or not sub.enabled:
            return 0
        event = WebhookEvent(
            kind="test.ping",
            data={"message": "ITIPS webhook test ping"},
            site_id=tenant.site_id or None,
            operator_id=tenant.operator_id or None,
            device_id=tenant.device_id or None,
        )
        try:
            self._queue.put_nowait(_Job(subscriber_id=sub.id, event=event))
            return 1
        except queue.Full:
            return 0

    # ─── producer bindings ────────────────────────────────────────

    def bind_alert_engine(self, alert_engine) -> None:
        """Attach to AlertEngine's `on_event` hook."""
        if alert_engine is None or not hasattr(alert_engine, "add_event_listener"):
            return

        def _listener(record: dict[str, Any]) -> None:
            evt = alert_record_to_event(record)
            if evt is not None:
                self.dispatch(evt)

        alert_engine.add_event_listener(_listener)

        # Lifecycle hook — separate from per-record events because
        # incident open/promote/finalize are higher-level signals than
        # the leaf alert kinds.
        if hasattr(alert_engine, "add_lifecycle_listener"):
            def _life(stage: str, info: dict[str, Any]) -> None:
                kind = f"incident.{stage}"
                if kind not in EVENT_KINDS:
                    return
                self.dispatch(WebhookEvent(
                    kind=kind,
                    data=info,
                    incident_id=info.get("incident_id"),
                    site_id=info.get("site_id"),
                    operator_id=info.get("operator_id"),
                    device_id=info.get("device_id"),
                ))
            alert_engine.add_lifecycle_listener(_life)

    def bind_openai_validator(self, validator) -> None:
        if validator is None or not hasattr(validator, "add_validation_listener"):
            return

        def _listener(result, context: dict[str, Any]) -> None:
            self.dispatch(WebhookEvent(
                kind="ai.validation",
                data={
                    "scenario":         result.scenario,
                    "verdict":          result.verdict,
                    "category":         result.category,
                    "confidence":       result.confidence,
                    "summary":          result.summary,
                    "model":            result.model,
                    "tokens_used":      result.tokens_used,
                    "cached":           result.cached,
                    "should_suppress":  result.should_suppress,
                    "should_escalate":  result.should_escalate,
                    "context":          context,
                },
                site_id=context.get("site_id"),
                incident_id=context.get("incident_id"),
            ))

        validator.add_validation_listener(_listener)

    def bind_sensor_dispatcher(self, sensor_dispatcher) -> None:
        if (sensor_dispatcher is None
                or not hasattr(sensor_dispatcher, "add_event_listener")):
            return

        def _listener(payload: dict[str, Any]) -> None:
            self.dispatch(WebhookEvent(
                kind="sensor.event",
                data=payload,
                site_id=payload.get("site_id"),
                incident_id=payload.get("incident_id"),
            ))

        sensor_dispatcher.add_event_listener(_listener)

    def bind_threat_evaluator(self, evaluator, *, tenant=None) -> None:
        """Publish `incident.verdict` once per closed decision window.

        The verdict is what alarm panels and the hub should actually
        listen for — `incident.confirmed` only fires for the INTRUDER
        path, while this kind covers AUTHORIZED and UNCERTAIN too. Pass
        `tenant` so the event carries site/operator/device IDs (the
        evaluator doesn't know about them otherwise)."""
        if (evaluator is None
                or not hasattr(evaluator, "add_verdict_listener")):
            return

        site_id = getattr(tenant, "site_id", None) if tenant else None
        operator_id = getattr(tenant, "operator_id", None) if tenant else None
        device_id = getattr(tenant, "device_id", None) if tenant else None

        def _listener(payload: dict[str, Any]) -> None:
            self.dispatch(WebhookEvent(
                kind="incident.verdict",
                data=payload,
                site_id=site_id,
                operator_id=operator_id,
                device_id=device_id,
            ))

        evaluator.add_verdict_listener(_listener)

    def bind_activity_tap(self, activity_tap, *, tenant=None) -> None:
        """Publish `activity.detection` for every raw detection the instant
        it's tapped — earlier than `alert.*` (pre-validation) and the
        finest-grained 'someone is here' signal a subscriber can get."""
        if activity_tap is None or not hasattr(activity_tap, "add_listener"):
            return

        site_id = getattr(tenant, "site_id", None) if tenant else None
        operator_id = getattr(tenant, "operator_id", None) if tenant else None
        device_id = getattr(tenant, "device_id", None) if tenant else None

        def _listener(entry: dict[str, Any]) -> None:
            self.dispatch(WebhookEvent(
                kind="activity.detection",
                data=entry,
                site_id=site_id,
                operator_id=operator_id,
                device_id=device_id,
            ))

        activity_tap.add_listener(_listener)

    # ─── internals ────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job = self._queue.get()
            if job is None:
                break
            try:
                self._deliver(job)
            except Exception:
                logger.exception("WebhookDispatcher: delivery crashed")

    def _deliver(self, job: _Job) -> None:
        sub = self._store.get(job.subscriber_id)
        if sub is None or not sub.enabled:
            return
        body = job.event.to_json()
        timestamp = int(time.time())
        delivery_id = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "itips-webhooks/1",
            TIMESTAMP_HEADER:  str(timestamp),
            SIGNATURE_HEADER:  sign(secret=sub.secret, timestamp=timestamp, body=body),
            EVENT_HEADER:      job.event.kind,
            EVENT_ID_HEADER:   job.event.id,
            DELIVERY_HEADER:   delivery_id,
        }
        start = time.monotonic()
        status_code: int | None = None
        error: str | None = None
        try:
            resp = requests.post(sub.url, data=body, headers=headers,
                                 timeout=self._timeout_s)
            status_code = resp.status_code
            success = 200 <= status_code < 300
            if not success:
                error = f"http {status_code}"
        except requests.exceptions.RequestException as exc:
            success = False
            error = f"{exc.__class__.__name__}: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000.0)

        self._store.record_delivery(
            subscriber_id=sub.id,
            event_id=job.event.id,
            event_kind=job.event.kind,
            attempt=job.attempt,
            status_code=status_code,
            error=error,
            duration_ms=duration_ms,
            success=success,
        )
        if success:
            return

        # Retry with exponential backoff up to MAX_RETRIES.
        if job.attempt < self.MAX_RETRIES:
            delay = self.BASE_BACKOFF_S * (2 ** (job.attempt - 1))
            job.attempt += 1
            threading.Timer(delay, self._requeue, args=(job,)).start()
            logger.info(
                "WebhookDispatcher: %s attempt %d failed (%s); retrying in %.1fs",
                sub.id, job.attempt - 1, error, delay,
            )
            return

        logger.warning(
            "WebhookDispatcher: %s gave up after %d attempts (%s)",
            sub.id, job.attempt, error,
        )
        # Auto-disable a subscriber whose endpoint has been silent for
        # long enough that further deliveries are almost certainly noise.
        latest = self._store.get(sub.id)
        if latest and latest.consecutive_failures >= _AUTODISABLE_THRESHOLD:
            self._store.update(sub.id, enabled=False)
            logger.warning(
                "WebhookDispatcher: auto-disabled %s after %d consecutive failures",
                sub.id, latest.consecutive_failures,
            )

    def _requeue(self, job: _Job) -> None:
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            logger.warning("WebhookDispatcher: retry queue full, dropping retry")


__all__ = ["WebhookDispatcher"]
