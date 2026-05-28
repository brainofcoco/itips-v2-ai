"""Tiny helpers for handling JPEG byte streams that come off Dahua cameras.

Dahua HTTP responses (snapshot.cgi and the multipart event JPEGs alike)
routinely append vendor metadata after the JPEG end-of-image marker
(FF D9). The decoded image is fine — libjpeg-turbo just prints a noisy

  Corrupt JPEG data: N extraneous bytes before marker 0xfe

warning to stderr for every frame. The cure is to truncate the buffer
at the EOI marker before handing it to cv2.imdecode / Pillow. Centralised
here so anyone touching camera-source JPEG bytes uses the same trim
instead of half the call sites being clean and half being noisy.
"""

from __future__ import annotations


def trim_to_jpeg_eoi(blob: bytes) -> bytes:
    """Return `blob` truncated to the last `FF D9` (JPEG EOI marker).

    If no EOI is found, returns the original buffer unchanged — the
    caller will get the same decode failure they would have anyway,
    and we don't want to hide a genuinely malformed image.
    """
    if not blob:
        return blob
    eoi = blob.rfind(b"\xff\xd9")
    if eoi == -1:
        return blob
    return blob[: eoi + 2]
