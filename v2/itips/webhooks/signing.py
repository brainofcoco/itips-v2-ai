"""HMAC-SHA256 signing for webhook deliveries.

Signature scheme follows the Stripe / GitHub convention:

    X-ITIPS-Signature: sha256=<hex>
    X-ITIPS-Timestamp: <unix seconds, integer>

with the signed message constructed as `<timestamp>.<body>` so that
consumers can reject replays of a previously-captured payload by
checking the timestamp is recent (default ±5 minutes — see docs).
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256


SIGNATURE_HEADER = "X-ITIPS-Signature"
TIMESTAMP_HEADER = "X-ITIPS-Timestamp"
EVENT_HEADER = "X-ITIPS-Event"
EVENT_ID_HEADER = "X-ITIPS-Event-ID"
DELIVERY_HEADER = "X-ITIPS-Delivery"


def generate_secret(*, n_bytes: int = 32) -> str:
    """Random secret for a new subscriber. URL-safe base64, 32 bytes of entropy."""
    return "whsec_" + secrets.token_urlsafe(n_bytes)


def sign(*, secret: str, timestamp: int, body: bytes) -> str:
    """Returns the `sha256=<hex>` value placed in X-ITIPS-Signature.

    The signed string is `<timestamp>.<body>`. Including the timestamp
    binds the signature to a moment in time so a leaked, captured POST
    can't be replayed indefinitely.
    """
    msg = f"{int(timestamp)}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), msg, sha256).hexdigest()
    return f"sha256={digest}"


def verify(*, secret: str, timestamp: int, body: bytes, signature: str,
           tolerance_s: int = 300) -> bool:
    """Reference verifier — consumers don't import this, but tests do.

    `tolerance_s` is the maximum age the signature is allowed to be.
    Pass 0 to disable the time check (not recommended in production).
    """
    if not signature or not signature.startswith("sha256="):
        return False
    if tolerance_s > 0:
        import time
        if abs(time.time() - timestamp) > tolerance_s:
            return False
    expected = sign(secret=secret, timestamp=timestamp, body=body)
    return hmac.compare_digest(expected, signature)
