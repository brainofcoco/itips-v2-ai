"""AxProListener — alarm-edge dedup + arm-state polling + reconnect.

We never import the real `hikaxpro` library. The listener is built
with a stub `_client` injected directly into the instance, and we
drive `_poll_zones()` / `_refresh_arm_state()` synchronously to
isolate the state-transition logic from the real polling loop's
thread + sleep cadence.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from itips.sensors.axpro_listener import AxProListener


def _make_listener(client=None, dispatcher=None):
    """Build an AxProListener without going through __init__'s thread.

    Skips connection because we inject `_client` directly; the run
    loop never starts. Lets us drive `_poll_zones` and
    `_refresh_arm_state` from the test thread.
    """
    if dispatcher is None:
        dispatcher = MagicMock()
    listener = AxProListener(
        host="hub.local", username="u", password="p",
        dispatcher=dispatcher, poll_interval_s=0.01,
        arm_poll_interval_s=0.01, reconnect_backoff_s=0.01,
    )
    listener._client = client
    listener._connected = client is not None
    return listener, dispatcher


def _zone(zone_id, alarm, *, name=None, detector_type="passiveInfraredDetector"):
    return {"Zone": {
        "id": zone_id,
        "name": name or f"Zone {zone_id}",
        "alarm": alarm,
        "detectorType": detector_type,
    }}


# ─── alarm-edge dedup ───────────────────────────────────────────────


def test_first_poll_records_state_but_does_not_fire(_=None):
    """On the very first poll, a sensor already in alarm shouldn't fire —
    we don't know if it just tripped or was already stuck on at boot.
    Only the next false→true transition counts."""
    client = MagicMock()
    client.zone_status.return_value = {"ZoneList": [_zone(1, alarm=True)]}
    listener, dispatcher = _make_listener(client)
    listener._poll_zones()
    dispatcher.dispatch.assert_not_called()


def test_alarm_rising_edge_fires_one_dispatch():
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [_zone(1, alarm=False)]},
        {"ZoneList": [_zone(1, alarm=True)]},   # ← rising edge
    ]
    listener, dispatcher = _make_listener(client)
    listener._poll_zones()   # records normal
    listener._poll_zones()   # detects edge, fires
    dispatcher.dispatch.assert_called_once()
    sent = dispatcher.dispatch.call_args.args[0]
    assert sent.zone_id == 1
    assert sent.event_state == "alarm"
    assert sent.event_type == "PIR"          # mapped from passiveInfraredDetector
    assert sent.source == "axpro"


def test_held_alarm_does_not_re_fire_on_subsequent_polls():
    """Critical dedup: a sensor stuck in alarm for 10 polls = ONE event."""
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [_zone(1, alarm=False)]},
        {"ZoneList": [_zone(1, alarm=True)]},
        {"ZoneList": [_zone(1, alarm=True)]},
        {"ZoneList": [_zone(1, alarm=True)]},
    ]
    listener, dispatcher = _make_listener(client)
    for _ in range(4):
        listener._poll_zones()
    assert dispatcher.dispatch.call_count == 1


def test_alarm_clear_then_re_arm_fires_again():
    """After a clear (true→false), a new rising edge re-fires."""
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [_zone(1, alarm=False)]},
        {"ZoneList": [_zone(1, alarm=True)]},   # first rising edge
        {"ZoneList": [_zone(1, alarm=False)]},  # cleared
        {"ZoneList": [_zone(1, alarm=True)]},   # second rising edge
    ]
    listener, dispatcher = _make_listener(client)
    for _ in range(4):
        listener._poll_zones()
    assert dispatcher.dispatch.call_count == 2


def test_detector_type_mapping_yields_expected_event_types():
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [
            _zone(1, alarm=False, detector_type="magneticContact"),
            _zone(2, alarm=False, detector_type="vibrationDetector"),
            _zone(3, alarm=False, detector_type="smokeDetector"),
        ]},
        {"ZoneList": [
            _zone(1, alarm=True, detector_type="magneticContact"),
            _zone(2, alarm=True, detector_type="vibrationDetector"),
            _zone(3, alarm=True, detector_type="smokeDetector"),
        ]},
    ]
    listener, dispatcher = _make_listener(client)
    listener._poll_zones()
    listener._poll_zones()
    types = sorted(c.args[0].event_type for c in dispatcher.dispatch.call_args_list)
    assert types == ["doorContact", "smoke", "vibration"]


def test_unknown_detector_type_passes_through_raw():
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [_zone(1, alarm=False, detector_type="newFangledSensor")]},
        {"ZoneList": [_zone(1, alarm=True, detector_type="newFangledSensor")]},
    ]
    listener, dispatcher = _make_listener(client)
    listener._poll_zones()
    listener._poll_zones()
    sent = dispatcher.dispatch.call_args.args[0]
    assert sent.event_type == "newFangledSensor"


def test_zone_with_missing_id_is_skipped():
    """Hub sometimes returns malformed entries (firmware glitches);
    don't let one bad row blow up the whole poll."""
    client = MagicMock()
    client.zone_status.side_effect = [
        {"ZoneList": [
            {"Zone": {"id": None, "alarm": False}},
            _zone(1, alarm=False),
        ]},
        {"ZoneList": [
            {"Zone": {"id": None, "alarm": True}},
            _zone(1, alarm=True),
        ]},
    ]
    listener, dispatcher = _make_listener(client)
    listener._poll_zones()
    listener._poll_zones()
    assert dispatcher.dispatch.call_count == 1
    assert dispatcher.dispatch.call_args.args[0].zone_id == 1


# ─── arm-state cache ────────────────────────────────────────────────


def test_arm_state_reflects_subsystem_status():
    client = MagicMock()
    client.subsystem_status.return_value = {
        "SubSysList": [{"SubSys": {"arming": "away"}}],
    }
    listener, _ = _make_listener(client)
    assert listener.is_armed is False
    listener._refresh_arm_state()
    assert listener.is_armed is True


def test_arm_state_disarmed_when_all_subsystems_disarm():
    client = MagicMock()
    client.subsystem_status.return_value = {
        "SubSysList": [
            {"SubSys": {"arming": "disarm"}},
            {"SubSys": {"arming": "disarm"}},
        ],
    }
    listener, _ = _make_listener(client)
    listener._is_armed = True   # pretend we were armed
    listener._refresh_arm_state()
    assert listener.is_armed is False


def test_arm_state_poll_failure_leaves_cached_value_alone():
    """A transient HTTP error on subsystem_status() must not mark the
    hub as suddenly disarmed."""
    client = MagicMock()
    client.subsystem_status.side_effect = RuntimeError("flaky")
    listener, _ = _make_listener(client)
    listener._is_armed = True
    listener._refresh_arm_state()
    assert listener.is_armed is True


# ─── lifecycle ──────────────────────────────────────────────────────


def test_start_with_missing_hikaxpro_does_not_raise(monkeypatch):
    """Most important defensive behavior: if hikaxpro isn't installed,
    start() must NOT raise — orchestrator's start-all loop is naive and
    would die. Listener should degrade silently with a populated
    last_error so the dashboard pill shows the reason."""
    listener, _ = _make_listener(client=None)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "hikaxpro":
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    listener.start()   # must not raise
    assert listener.is_alive() is False
    assert listener.last_error and "hikaxpro" in listener.last_error.lower()


def test_status_properties_are_safe_before_any_poll():
    listener, _ = _make_listener(client=None)
    assert listener.host == "hub.local"
    assert listener.is_connected is False
    assert listener.is_armed is False
    assert listener.last_error is None
