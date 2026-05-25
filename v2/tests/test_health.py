"""Health probes — mocked HTTP, no real cameras."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from itips.camera import dahua_health
from itips.runtime.event_tap import EventTap


def _fake_endpoint(host="192.168.0.124"):
    ep = MagicMock()
    ep.safe_label.return_value = f"{host}:80"
    return ep


def _resp(status=200, text="", headers=None, content=b""):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    r.content = content
    r.close = MagicMock()
    return r


# ─── individual probes ──────────────────────────────────────────────


def test_reachable_ok():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "type=DH-SDT4E425\n")
    result = dahua_health.probe_reachable(ep)
    assert result.status == "ok"
    assert "DH-SDT4E425" in result.detail


def test_reachable_missing():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(401, "")
    result = dahua_health.probe_reachable(ep)
    assert result.status == "missing"


def test_reachable_network_error():
    ep = _fake_endpoint()
    ep.get.side_effect = requests.ConnectTimeout()
    result = dahua_health.probe_reachable(ep)
    assert result.status == "error"
    assert result.backup_hint  # error → hint is filled


def test_snapshot_ok_via_jpeg_magic():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "", {"Content-Type": "application/octet-stream"},
                                content=b"\xff\xd8\xff\xe0...rest")
    result = dahua_health.probe_snapshot(ep)
    assert result.status == "ok"


def test_face_db_ok_with_groups():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "GroupList[0].groupID=1\nGroupList[0].groupName=workers\n")
    result = dahua_health.probe_face_db(ep)
    assert result.status == "ok"


def test_face_db_missing_on_404():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(404, "")
    result = dahua_health.probe_face_db(ep)
    assert result.status == "missing"
    assert "Jetson InsightFace" in result.backup_hint


def test_anpr_missing_on_400():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(400, "")
    result = dahua_health.probe_anpr(ep)
    assert result.status == "missing"
    assert "Plate Recognizer" in result.backup_hint


def test_deterrence_missing_on_non_tioc():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "status.somethingElse=on")
    result = dahua_health.probe_deterrence(ep)
    assert result.status == "missing"


def test_deterrence_ok_with_whitelight():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "status.whitelight=off\nstatus.speaker=off")
    result = dahua_health.probe_deterrence(ep)
    assert result.status == "ok"


def test_mjpeg_sub_ok_with_multipart():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "",
        {"Content-Type": "multipart/x-mixed-replace; boundary=myboundary"})
    result = dahua_health.probe_mjpeg_sub(ep)
    assert result.status == "ok"


def test_mjpeg_main_missing():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(401, "")
    result = dahua_health.probe_mjpeg_main(ep)
    assert result.status == "missing"


# ─── new probes: PRD/dahua-doc gaps ─────────────────────────────────


def test_ivs_rule_types_extracted():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200,
        "VideoAnalyseRule[0].Object.RuleType=CrossLineDetection\n"
        "VideoAnalyseRule[1].Object.RuleType=CrossRegionDetection\n"
    )
    result = dahua_health.probe_ivs_rule_types(ep)
    assert result.status == "ok"
    assert "CrossLineDetection" in result.detail
    assert "CrossRegionDetection" in result.detail


def test_ivs_rule_types_class_fallback_syntax():
    # Older firmware exposes the rule kind under .Class= instead.
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "VideoAnalyseRule[0].Class=WanderDetection\n")
    result = dahua_health.probe_ivs_rule_types(ep)
    assert result.status == "ok"
    assert "WanderDetection" in result.detail


def test_ivs_rule_types_missing_when_no_rule():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "VideoAnalyseRule[0].Enable=false\n")
    result = dahua_health.probe_ivs_rule_types(ep)
    assert result.status == "missing"
    assert result.backup_hint  # falls back to Jetson zone logic


def test_face_group_channel_ok():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "info.groupID[0]=10000\ninfo.groupID[1]=10001\n")
    result = dahua_health.probe_face_group_channel(ep)
    assert result.status == "ok"
    assert "2 binding" in result.detail


def test_face_group_channel_missing_when_unbound():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "info.totalGroups=0\n")
    result = dahua_health.probe_face_group_channel(ep)
    assert result.status == "missing"
    assert "InsightFace" in result.backup_hint


def test_anpr_event_attach_ok_with_multipart():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(
        200, "",
        {"Content-Type": "multipart/x-mixed-replace; boundary=anpr"},
    )
    result = dahua_health.probe_anpr_event_attach(ep)
    assert result.status == "ok"


def test_anpr_event_attach_missing_on_404():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(404, "")
    result = dahua_health.probe_anpr_event_attach(ep)
    assert result.status == "missing"
    assert "Plate Recognizer" in result.backup_hint


def test_white_light_ok():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200,
        "table.Lighting[0][0].Mode=Manual\ntable.Lighting[0][0].MiddleLight[0].Light=50\n",
    )
    result = dahua_health.probe_white_light(ep)
    assert result.status == "ok"


def test_white_light_missing_when_no_lighting_array():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "Error: name not supported\n")
    result = dahua_health.probe_white_light(ep)
    assert result.status == "missing"


def test_alarm_out_ok_when_channels_present():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200,
        "table.AlarmOut[0].Name=alarmOut1\ntable.AlarmOut[0].Mode=0\n",
    )
    result = dahua_health.probe_alarm_out(ep)
    assert result.status == "ok"
    assert "1 channel" in result.detail


def test_alarm_out_missing_on_empty_array():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "")
    result = dahua_health.probe_alarm_out(ep)
    assert result.status == "missing"
    assert "AX PRO horn" in result.backup_hint


def test_sd_storage_reports_capacity_when_total_bytes_present():
    ep = _fake_endpoint()
    # 64 GiB → 68_719_476_736 bytes.
    ep.get.return_value = _resp(200,
        "list.info[0].Detail[0].Path=/dev/mmcblk0\n"
        "list.info[0].Detail[0].TotalBytes=68719476736\n",
    )
    result = dahua_health.probe_sd_storage(ep)
    assert result.status == "ok"
    assert "64 GB" in result.detail


def test_sd_storage_missing_when_endpoint_empty():
    ep = _fake_endpoint()
    ep.get.return_value = _resp(200, "")
    result = dahua_health.probe_sd_storage(ep)
    assert result.status == "missing"


# ─── passive event checks ──────────────────────────────────────────


def test_passive_checks_observed_event_is_ok():
    tap = EventTap()
    tap.publish(camera_id=4, code="FaceRecognition", action="Pulse", index=0, data={})
    seen = tap.codes_seen_by_camera()
    checks = dahua_health._passive_checks(4, seen)
    fr = next(c for c in checks if c.name == "event:FaceRecognition")
    assert fr.status == "ok"


def test_passive_checks_unobserved_event_is_unknown():
    tap = EventTap()
    seen = tap.codes_seen_by_camera()
    checks = dahua_health._passive_checks(1, seen)
    fr = next(c for c in checks if c.name == "event:FaceRecognition")
    assert fr.status == "unknown"


# ─── full per-camera run ───────────────────────────────────────────


def test_run_for_camera_assembles_full_report():
    ep = _fake_endpoint()
    # Sequence of responses for all active probes, in registration order:
    # reachable, snapshot, mjpeg_sub, mjpeg_main, face_db, anpr, ptz,
    # deterrence, ivs_rules, ivs_rule_types, face_group_channel,
    # anpr_event_attach, white_light, alarm_out, sd_storage.
    responses = [
        _resp(200, "type=DH-SDT4E425"),
        _resp(200, "", {"Content-Type": "image/jpeg"}, content=b"\xff\xd8\xff\xe0"),
        _resp(200, "", {"Content-Type": "multipart/x-mixed-replace; boundary=x"}),
        _resp(401, ""),  # main stream not available
        _resp(200, ""),  # face db reachable (no groups yet)
        _resp(400, ""),  # ANPR list missing
        _resp(200, "presets[0].Index=1\npresets[0].Name=home\n"),
        _resp(200, "status.whitelight=off\nstatus.speaker=off"),
        _resp(200, "VideoAnalyseRule[0].Name=line"),
        # New probes ↓
        _resp(200, "VideoAnalyseRule[0].Object.RuleType=CrossLineDetection\n"),
        _resp(200, "info.groupID[0]=10000\n"),
        _resp(200, "", {"Content-Type": "multipart/x-mixed-replace; boundary=a"}),
        _resp(200, ""),  # no Lighting array → white_light missing
        _resp(200, "table.AlarmOut[0].Name=alarmOut1\n"),
        _resp(200, "list.info[0].Detail[0].TotalBytes=68719476736\n"),
    ]
    ep.get.side_effect = responses

    tap = EventTap()
    tap.publish(camera_id=4, code="FaceDetection", action="Pulse", index=0, data={})
    seen = tap.codes_seen_by_camera()

    health = dahua_health.run_for_camera(ep, camera_id=4, codes_seen=seen)
    assert health.camera_id == 4
    assert health.model and "DH-SDT4E425" in health.model
    by_name = {c.name: c for c in health.checks}
    assert by_name["reachable"].status == "ok"
    assert by_name["snapshot"].status == "ok"
    assert by_name["mjpeg_sub"].status == "ok"
    assert by_name["mjpeg_main"].status == "missing"
    assert by_name["anpr_redlist"].status == "missing"
    assert by_name["ptz"].status == "ok"
    assert by_name["deterrence"].status == "ok"
    assert by_name["event:FaceDetection"].status == "ok"
    assert by_name["event:FaceRecognition"].status == "unknown"
    # New probes
    assert by_name["ivs_rule_types"].status == "ok"
    assert "CrossLineDetection" in by_name["ivs_rule_types"].detail
    assert by_name["face_group_channel"].status == "ok"
    assert by_name["anpr_event_attach"].status == "ok"
    assert by_name["white_light"].status == "missing"
    assert by_name["alarm_out"].status == "ok"
    assert by_name["sd_storage"].status == "ok"
    # mjpeg_main, anpr_redlist, white_light all missing → ≥3 backup hints.
    backup_count = sum(1 for c in health.checks if c.backup_hint)
    assert backup_count >= 3
