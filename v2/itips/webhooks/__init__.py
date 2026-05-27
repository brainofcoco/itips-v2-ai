"""Outbound webhooks — push events to subscriber URLs instead of polling.

Architecture
------------
AlertEngine / OpenAIValidator / SensorDispatcher publish events via
in-process callbacks. `WebhookDispatcher` listens on those callbacks,
filters by each subscriber's `event_filter`, signs the body with the
subscriber's HMAC secret, and POSTs to the registered URL with a small
retry budget. Subscribers are persisted in a sqlite registry managed
through the dashboard API (`/api/webhooks/*`).

This deliberately lives next to the AI pipeline rather than going
through the Sync Agent — local-only consumers (gate controllers, alarm
panels, on-site automation) get sub-second delivery without a cloud
round-trip. The trade-off is that delivery is best-effort across a
process restart; see WebhookDispatcher.MAX_RETRIES.
"""

from itips.webhooks.dispatcher import WebhookDispatcher
from itips.webhooks.events import EVENT_KINDS, WebhookEvent
from itips.webhooks.store import Subscriber, SubscriberStore

__all__ = [
    "EVENT_KINDS",
    "Subscriber",
    "SubscriberStore",
    "WebhookDispatcher",
    "WebhookEvent",
]
