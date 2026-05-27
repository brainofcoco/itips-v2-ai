"""AX PRO hub-control helpers.

The `hikaxpro` library's `recover_bypass_zone` and `arm_away` work on
some firmwares and silently fail on others:

  • `recover_bypass_zone` PUTs `/ISAPI/SecurityCP/control/Recoverbypass/<id>`
    which returns 404 `notSupport` on current firmware. The library
    swallows the response and returns False, hiding the cause.
  • `arm_away` rejects the call with 400 `armedStatus` when the
    subsystem is already armed in another mode — you must disarm first.

This module is a thin, opinionated wrapper that:

  • uses `/ISAPI/SecurityCP/control/bypassRecover/<id>` (the working
    path on this firmware),
  • runs `disarm → operate → arm_away` so the hub doesn't reject the
    operation,
  • surfaces the camera-side error body to callers (so the dashboard
    can show "zoneFault" instead of a silent False).

It does *not* try to be a full hikaxpro replacement — it leans on the
library for everything that already works.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AxProAdminError(RuntimeError):
    """Hub rejected a control operation. `payload` carries the hub's body."""

    def __init__(self, message: str, *, payload: Any = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.payload = payload
        self.http_status = http_status


class AxProAdmin:
    """Hub-control surface on top of an authenticated hikaxpro client."""

    BYPASS_RECOVER_PATH = "/ISAPI/SecurityCP/control/bypassRecover/{zone_id}"
    BYPASS_PATH         = "/ISAPI/SecurityCP/control/bypass/{zone_id}"
    ZONE_CONFIG_PATH    = "/ISAPI/SecurityCP/Configuration/zones/{zone_id}"

    # Siren control via "One-Key Alarm" — the AX PRO's manual panic
    # surface. PUT to `oneKeyAlarm/{sub_id}` with a body of
    # `{"OneKeyAlarm": {}}` triggers every siren assigned to that
    # subsystem; `clearAlarm/{sub_id}` silences them.
    SIREN_START_PATH    = "/ISAPI/SecurityCP/control/oneKeyAlarm/{sub_id}"
    SIREN_STOP_PATH     = "/ISAPI/SecurityCP/control/clearAlarm/{sub_id}"

    def __init__(self, *, host: str, client_supplier) -> None:
        self._host = host
        self._client_supplier = client_supplier

    # ─── subsystem state ──────────────────────────────────────────

    def disarm_all(self, sub_ids: list[int]) -> list[dict]:
        client = self._require_client()
        results = []
        for sid in sub_ids:
            try:
                payload = client.disarm(sub_id=sid)
                results.append({"sub_id": sid, "ok": True, "payload": payload})
            except Exception as exc:  # noqa: BLE001
                logger.warning("disarm(%d) failed: %s", sid, exc)
                results.append({"sub_id": sid, "ok": False, "error": str(exc)})
        return results

    def arm_away_all(self, sub_ids: list[int], *, disarm_first: bool = True) -> list[dict]:
        """`arm_away` requires the hub to be disarmed first. We chain
        disarm → arm-away so callers don't have to know that."""
        client = self._require_client()
        if disarm_first:
            self.disarm_all(sub_ids)
        results = []
        for sid in sub_ids:
            try:
                payload = client.arm_away(sub_id=sid)
                results.append({"sub_id": sid, "ok": True, "payload": payload})
            except Exception as exc:  # noqa: BLE001
                results.append({"sub_id": sid, "ok": False, "error": str(exc)})
        return results

    # ─── zone bypass ──────────────────────────────────────────────

    def unbypass_zone(self, zone_id: int) -> dict:
        """Clear an operational bypass on `zone_id`.

        The hub rejects bypass changes while the parent subsystem is
        armed; if that happens we surface the error verbatim so the
        caller can decide whether to disarm + retry.
        """
        return self._control_put(
            self.BYPASS_RECOVER_PATH.format(zone_id=zone_id),
        )

    def bypass_zone(self, zone_id: int) -> dict:
        return self._control_put(
            self.BYPASS_PATH.format(zone_id=zone_id),
        )

    # ─── siren ────────────────────────────────────────────────────

    def start_siren(self, sub_id: int = 1) -> dict:
        """Sound the panic alarm on `sub_id`. Triggers every siren
        bound to that subsystem. Returns the hub's ack payload.

        Safe to call when already sounding — the hub just no-ops.
        """
        return self._control_put(
            self.SIREN_START_PATH.format(sub_id=sub_id),
            json_body={"OneKeyAlarm": {}},
        )

    def stop_siren(self, sub_id: int = 1) -> dict:
        """Silence sirens on `sub_id`. Returns the ack payload.

        Note: this clears *all* active alarms on the subsystem — same
        button the Hik-Connect app uses on the alarm-popup banner.
        """
        return self._control_put(
            self.SIREN_STOP_PATH.format(sub_id=sub_id),
        )

    def test_siren(self, sub_id: int = 1, *, duration_s: float = 2.0) -> dict:
        """Short burst — start, wait, stop. For the dashboard's TEST
        button. Always pairs the stop with the start, even if the
        intervening sleep is interrupted, so a failed test can't leave
        the siren ringing."""
        import time
        result = {"start": None, "stop": None}
        try:
            result["start"] = self.start_siren(sub_id)
            time.sleep(max(0.5, min(10.0, float(duration_s))))
        finally:
            try:
                result["stop"] = self.stop_siren(sub_id)
            except Exception as exc:  # noqa: BLE001
                # If even stop fails, surface it loudly — operators need
                # to know the siren may still be live.
                logger.exception("test_siren: stop failed after start")
                result["stop_error"] = str(exc)
        return result

    def siren_status(self) -> list[dict]:
        """List every paired siren with its connection state, signal,
        charge, and tamper flag. Backs the dashboard's siren pill."""
        client = self._require_client()
        ps = client.peripherals_status() or {}
        sirens = ps.get("ExDevStatus", {}).get("SirenList", [])
        return [s.get("Siren", {}) for s in sirens]

    # ─── zone config ──────────────────────────────────────────────

    def get_zone_config(self, zone_id: int) -> dict:
        import requests
        cookies = self._cookies()
        url = f"http://{self._host}{self.ZONE_CONFIG_PATH.format(zone_id=zone_id)}?format=json"
        r = requests.get(url, cookies=cookies, timeout=5)
        if r.status_code != 200:
            raise AxProAdminError(
                f"GET zone config failed: HTTP {r.status_code}",
                payload=r.text, http_status=r.status_code,
            )
        return r.json()

    def set_zone_config_flag(self, zone_id: int, *, key: str, value: Any) -> dict:
        """Patch a single key on the zone config. Useful for flipping
        `armNoBypassEnabled` after a faulty install is fixed.
        """
        cfg = self.get_zone_config(zone_id)
        if "Zone" not in cfg:
            raise AxProAdminError("zone config missing 'Zone' wrapper", payload=cfg)
        cfg["Zone"][key] = value
        return self._control_put(
            self.ZONE_CONFIG_PATH.format(zone_id=zone_id),
            json_body=cfg,
        )

    # ─── internals ────────────────────────────────────────────────

    def _require_client(self):
        client = self._client_supplier()
        if client is None:
            raise AxProAdminError("hikaxpro client not connected")
        return client

    def _cookies(self) -> dict:
        client = self._require_client()
        cookie = getattr(client, "_cookie", None) or getattr(client, "cookie", None)
        if not cookie:
            raise AxProAdminError("hikaxpro session cookie missing")
        return {"WebSession": cookie}

    def _control_put(self, path: str, *, json_body: dict | None = None) -> dict:
        import requests
        cookies = self._cookies()
        url = f"http://{self._host}{path}?format=json"
        kwargs: dict[str, Any] = {"cookies": cookies, "timeout": 5}
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {"Content-Type": "application/json"}
        r = requests.put(url, **kwargs)
        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text[:300]}
        # Hub uses statusCode==1 for OK regardless of HTTP status; honour both.
        ok = (r.status_code == 200) and (
            isinstance(payload, dict) and payload.get("statusCode") == 1
        )
        if not ok:
            sub = payload.get("subStatusCode") if isinstance(payload, dict) else None
            raise AxProAdminError(
                f"hub rejected {path}: {sub or 'HTTP ' + str(r.status_code)}",
                payload=payload, http_status=r.status_code,
            )
        return payload
