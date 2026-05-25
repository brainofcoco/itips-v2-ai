"""Capability router — pure-Python; no ML deps."""

from __future__ import annotations

from itips.ml.capability_router import Capability, CapabilityRouter, CapabilitySnapshot


def _health_check(name: str, status: str) -> dict:
    return {"name": name, "label": name, "status": status,
            "category": "test", "detail": None, "backup_hint": None}


def _camera(cam_id: int, statuses: dict[str, str]) -> dict:
    return {"camera_id": cam_id, "checks": [_health_check(n, s) for n, s in statuses.items()]}


def test_router_starts_empty():
    r = CapabilityRouter()
    # Unknown camera → never asks for a fallback (conservative).
    assert r.needs_fallback(99, Capability.FACE_RECOGNITION) is False


def test_face_recognition_native_when_both_probes_ok():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(1, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    assert r.needs_fallback(1, Capability.FACE_RECOGNITION) is False


def test_face_recognition_needs_fallback_when_one_probe_missing():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(1, {
        "face_recognition_db": "ok",
        "face_group_channel": "missing",  # ← group not bound to channel
    })]})
    assert r.needs_fallback(1, Capability.FACE_RECOGNITION) is True


def test_anpr_needs_fallback_when_event_attach_fails():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(2, {
        "anpr_redlist": "ok",
        "anpr_event_attach": "missing",
    })]})
    assert r.needs_fallback(2, Capability.ANPR) is True


def test_ivs_rules_native_only_when_rule_types_deployed():
    r = CapabilityRouter()
    # Old `ivs_rules` probe says ok but no types parsed → still fallback.
    r.update_from_health({"cameras": [_camera(3, {
        "ivs_rules": "ok",
        "ivs_rule_types": "missing",
    })]})
    assert r.needs_fallback(3, Capability.IVS_RULES) is True


def test_summary_shape_per_camera():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [
        _camera(1, {
            "face_recognition_db": "ok",
            "face_group_channel": "ok",
            "anpr_redlist": "missing",
            "anpr_event_attach": "missing",
            "ivs_rule_types": "ok",
            "deterrence": "ok",
            "snapshot": "ok",
            "sd_storage": "ok",
        }),
    ]})
    s = r.summary()
    assert 1 in s
    assert s[1]["face_recognition"] is True
    assert s[1]["anpr"] is False
    assert s[1]["ivs_rules"] is True


def test_set_camera_direct_injection_for_tests():
    r = CapabilityRouter()
    r.set_camera(CapabilitySnapshot(camera_id=42, native={Capability.FACE_RECOGNITION: False}))
    assert r.needs_fallback(42, Capability.FACE_RECOGNITION) is True


def test_refresh_replaces_state():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(1, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    # Camera 1 disappears from a later probe — router should drop it.
    r.update_from_health({"cameras": [_camera(2, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    assert r.get(1) is None
    assert r.get(2) is not None


# ─── overrides ───────────────────────────────────────────────────────


def test_override_forces_fallback_even_when_probe_says_native():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(4, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    assert r.needs_fallback(4, Capability.FACE_RECOGNITION) is False
    r.set_override(4, Capability.FACE_RECOGNITION, True)
    assert r.needs_fallback(4, Capability.FACE_RECOGNITION) is True


def test_override_can_pin_to_native_even_when_probe_says_missing():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(1, {
        "face_recognition_db": "missing",
        "face_group_channel": "missing",
    })]})
    assert r.needs_fallback(1, Capability.FACE_RECOGNITION) is True
    r.set_override(1, Capability.FACE_RECOGNITION, False)
    assert r.needs_fallback(1, Capability.FACE_RECOGNITION) is False


def test_override_clear_falls_back_to_probe():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(2, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    r.set_override(2, Capability.FACE_RECOGNITION, True)
    r.set_override(2, Capability.FACE_RECOGNITION, None)  # clear
    assert r.needs_fallback(2, Capability.FACE_RECOGNITION) is False


def test_overrides_persist_to_disk(tmp_path):
    path = tmp_path / "overrides.json"
    r1 = CapabilityRouter(overrides_path=path)
    r1.set_override(4, Capability.FACE_RECOGNITION, True)
    r1.set_override(7, Capability.ANPR, False)
    # Fresh instance reads back what was written.
    r2 = CapabilityRouter(overrides_path=path)
    assert r2.needs_fallback(4, Capability.FACE_RECOGNITION) is True
    assert r2.needs_fallback(7, Capability.ANPR) is False


def test_overrides_dict_shape_matches_dashboard_expectation():
    r = CapabilityRouter()
    r.set_override(4, Capability.FACE_RECOGNITION, True)
    r.set_override(4, Capability.ANPR, False)
    out = r.overrides()
    assert out == {4: {"face_recognition": True, "anpr": False}}


def test_summary_reflects_override_for_effective_native_flag():
    r = CapabilityRouter()
    r.update_from_health({"cameras": [_camera(4, {
        "face_recognition_db": "ok",
        "face_group_channel": "ok",
    })]})
    # Native says yes, but operator forces fallback → effective="not native".
    r.set_override(4, Capability.FACE_RECOGNITION, True)
    summary = r.summary()
    assert summary[4]["face_recognition"] is False
