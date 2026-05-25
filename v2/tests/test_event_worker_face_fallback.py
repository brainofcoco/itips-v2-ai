"""Event-worker face fallback wiring.

Drives `_handle_face_detection` with a stub FaceEngine + CapabilityRouter
to confirm the dispatcher promotes bare FaceDetection events to either
`handle_personnel_seen` or `handle_face_intruder` when the camera lacks
native FaceRecognition.

No InsightFace, no Dahua. Pure routing test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from itips.ml.capability_router import Capability, CapabilityRouter, CapabilitySnapshot
from itips.ml.face_engine import RecognitionResult
from itips.runtime.event_worker import DahuaEventDispatcher, WorkerDeps
from itips.sensors.dahua_events import DahuaEvent


def _make_event(bbox=(100, 100, 200, 200)) -> DahuaEvent:
    return DahuaEvent(
        camera_id=1,
        code="FaceDetection",
        action="Pulse",
        index=0,
        data={"Face": {"BoundingBox": list(bbox)}},
        jpeg=None,
    )


def _make_dispatcher(face_engine, capability_router, alert_engine):
    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=MagicMock(),
        recorders={},
        event_tap=None,
        capability_router=capability_router,
        face_engine=face_engine,
    )
    # Stub out network init by handing it an unparseable rtsp_url —
    # dispatcher will set _listener=None and never run the network loop.
    d = DahuaEventDispatcher.__new__(DahuaEventDispatcher)
    d.camera_id = 1
    d.deps = deps
    return d


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype="uint8")


# ─── routing decisions ──────────────────────────────────────────────


def test_native_fr_path_just_logs_bbox(monkeypatch):
    """Capability router says FR is native → engine MUST NOT be called."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: True},
    ))
    engine = MagicMock()  # would explode if called
    d = _make_dispatcher(engine, router, alert)

    d._handle_face_detection(_make_event(), _frame())

    engine.recognize.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()
    call = alert.handle_behaviour_alert_simple.call_args.kwargs
    assert call["alert_type"] == "face_detected"


def test_fallback_match_promotes_to_personnel_seen():
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: False},
    ))
    engine = MagicMock()
    engine.recognize.return_value = RecognitionResult(
        matched=True, person_id="p-42", full_name="Sam",
        similarity=0.78, embedding=None,
    )
    d = _make_dispatcher(engine, router, alert)

    d._handle_face_detection(_make_event(), _frame())

    engine.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-42"
    assert kw["name"] == "Sam"
    assert kw["similarity"] == 78  # 0.78 * 100
    alert.handle_behaviour_alert_simple.assert_not_called()


def test_fallback_no_match_promotes_to_face_intruder():
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: False},
    ))
    engine = MagicMock()
    engine.recognize.return_value = RecognitionResult(
        matched=False, person_id=None, full_name=None,
        similarity=0.10, embedding=None,
    )
    d = _make_dispatcher(engine, router, alert)

    d._handle_face_detection(_make_event(), _frame())

    alert.handle_face_intruder.assert_called_once()
    kw = alert.handle_face_intruder.call_args.kwargs
    assert kw["name"] == "INTRUDER"
    alert.handle_personnel_seen.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_not_called()


def test_engine_crash_degrades_to_bare_bbox():
    """A broken face engine must not block the rest of the lifecycle."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: False},
    ))
    engine = MagicMock()
    engine.recognize.side_effect = RuntimeError("model OOM")
    d = _make_dispatcher(engine, router, alert)

    d._handle_face_detection(_make_event(), _frame())

    alert.handle_personnel_seen.assert_not_called()
    alert.handle_face_intruder.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()
    assert alert.handle_behaviour_alert_simple.call_args.kwargs["alert_type"] == "face_detected"


def test_no_frame_means_no_fallback_even_if_capability_says_so():
    """Without a frame we can't run inference — bare bbox is right."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: False},
    ))
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)

    d._handle_face_detection(_make_event(), None)

    engine.recognize.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()


def test_no_router_and_no_engine_keeps_baseline_behavior():
    """Vanilla v2 deploy with no ML wired → existing behavior."""
    alert = MagicMock()
    d = _make_dispatcher(face_engine=None, capability_router=None, alert_engine=alert)
    d._handle_face_detection(_make_event(), _frame())
    alert.handle_behaviour_alert_simple.assert_called_once()


# ─── FaceRecognition override path ────────────────────────────────────


def _fr_event(*, candidates) -> DahuaEvent:
    """Camera-native FaceRecognition event with a Candidates list."""
    return DahuaEvent(
        camera_id=1, code="FaceRecognition", action="Pulse", index=0,
        data={"Candidates": candidates,
              "Face": {"BoundingBox": [100, 100, 200, 200]}},
        jpeg=None,
    )


def test_face_recognition_trusts_camera_candidates_by_default():
    """No override → camera's Candidates win, Jetson FR not called."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: True},
    ))
    engine = MagicMock()
    d = _make_dispatcher(engine, router, alert)
    d._handle_face_recognition(
        _fr_event(candidates=[{
            "Person": {"UID": "cam-uid-7", "GroupID": "g1", "Name": "Alice"},
            "Similarity": 92,
        }]),
        _frame(),
    )
    engine.recognize.assert_not_called()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "cam-uid-7"
    assert kw["name"] == "Alice"
    assert kw["group_id"] == "g1"   # camera's group, not "jetson-fallback"
    assert kw["similarity"] == 92


def test_face_recognition_routes_through_jetson_when_override_forces_fallback():
    """Operator-forced fallback → ignore camera Candidates, run Jetson FR."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: True},
    ))
    router.set_override(1, Capability.FACE_RECOGNITION, True)
    engine = MagicMock()
    engine.recognize.return_value = RecognitionResult(
        matched=True, person_id="p-99", full_name="Sam",
        similarity=0.82, embedding=None,
    )
    d = _make_dispatcher(engine, router, alert)
    d._handle_face_recognition(
        _fr_event(candidates=[{
            "Person": {"UID": "stale-cam-uid", "GroupID": "g1", "Name": "WrongPerson"},
            "Similarity": 51,
        }]),
        _frame(),
    )
    # Jetson decided identity, not the camera.
    engine.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-99"
    assert kw["name"] == "Sam"
    assert kw["group_id"] == "jetson-fallback"


def test_face_recognition_override_with_no_jetson_match_fires_intruder():
    """Forced Jetson FR + Jetson says no match → INTRUDER (not the camera's)."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: True},
    ))
    router.set_override(1, Capability.FACE_RECOGNITION, True)
    engine = MagicMock()
    engine.recognize.return_value = RecognitionResult(
        matched=False, person_id=None, full_name=None,
        similarity=0.18, embedding=None,
    )
    d = _make_dispatcher(engine, router, alert)
    d._handle_face_recognition(
        _fr_event(candidates=[{
            "Person": {"UID": "cam-thinks-known", "GroupID": "g1", "Name": "GhostMatch"},
            "Similarity": 70,
        }]),
        _frame(),
    )
    alert.handle_face_intruder.assert_called_once()
    alert.handle_personnel_seen.assert_not_called()
