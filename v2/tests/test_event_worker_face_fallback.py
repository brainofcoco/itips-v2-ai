"""Event-worker face routing — ML-only.

Identity is decided solely by the local face engine; the Dahua cameras'
onboard FaceRecognition result (`Candidates`) is ignored. When a threat
evaluator is wired it owns the decision (multi-frame window); otherwise the
dispatcher runs the engine directly on the event frame.

No InsightFace, no Dahua. Pure routing test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from itips.ml.capability_router import Capability, CapabilityRouter, CapabilitySnapshot
from itips.ml.face_engine import RecognitionResult
from itips.runtime.event_worker import DahuaEventDispatcher, WorkerDeps
from itips.sensors.dahua_events import DahuaEvent


def _detection_event(bbox=(100, 100, 200, 200)) -> DahuaEvent:
    return DahuaEvent(
        camera_id=1,
        code="FaceDetection",
        action="Pulse",
        index=0,
        data={"Face": {"BoundingBox": list(bbox)}},
        jpeg=None,
    )


def _recognition_event(*, candidates=None) -> DahuaEvent:
    """Camera-native FaceRecognition event — its Candidates must be ignored."""
    return DahuaEvent(
        camera_id=1, code="FaceRecognition", action="Pulse", index=0,
        data={"Candidates": candidates or [],
              "Face": {"BoundingBox": [100, 100, 200, 200]}},
        jpeg=None,
    )


def _make_dispatcher(face_engine, alert_engine, *,
                     capability_router=None, threat_evaluator=None):
    deps = WorkerDeps(
        alert_engine=alert_engine,
        frame_bus=MagicMock(),
        recorders={},
        event_tap=None,
        capability_router=capability_router,
        face_engine=face_engine,
        threat_evaluator=threat_evaluator,
    )
    # Stub out network init — dispatcher is constructed without __init__ so it
    # never opens the RTSP event stream.
    d = DahuaEventDispatcher.__new__(DahuaEventDispatcher)
    d.camera_id = 1
    d.deps = deps
    return d


def _frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype="uint8")


def _match(person_id="p-42", name="Sam", sim=0.78) -> RecognitionResult:
    return RecognitionResult(
        matched=True, person_id=person_id, full_name=name,
        similarity=sim, embedding=None,
    )


def _no_match(sim=0.10) -> RecognitionResult:
    return RecognitionResult(
        matched=False, person_id=None, full_name=None,
        similarity=sim, embedding=None,
    )


# ─── FaceDetection routing ───────────────────────────────────────────


def test_face_detection_match_promotes_to_personnel_seen():
    alert = MagicMock()
    engine = MagicMock()
    engine.recognize.return_value = _match(person_id="p-42", name="Sam", sim=0.78)
    d = _make_dispatcher(engine, alert)

    d._handle_face_detection(_detection_event(), _frame())

    engine.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-42"
    assert kw["name"] == "Sam"
    assert kw["similarity"] == 78  # 0.78 * 100
    alert.handle_behaviour_alert_simple.assert_not_called()


def test_face_detection_no_match_promotes_to_face_intruder():
    alert = MagicMock()
    engine = MagicMock()
    engine.recognize.return_value = _no_match()
    d = _make_dispatcher(engine, alert)

    d._handle_face_detection(_detection_event(), _frame())

    alert.handle_face_intruder.assert_called_once()
    assert alert.handle_face_intruder.call_args.kwargs["name"] == "INTRUDER"
    alert.handle_personnel_seen.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_not_called()


def test_face_detection_uses_engine_even_when_router_says_native():
    """Capability router no longer gates face — engine runs regardless."""
    alert = MagicMock()
    router = CapabilityRouter()
    router.set_camera(CapabilitySnapshot(
        camera_id=1, native={Capability.FACE_RECOGNITION: True},
    ))
    engine = MagicMock()
    engine.recognize.return_value = _match()
    d = _make_dispatcher(engine, alert, capability_router=router)

    d._handle_face_detection(_detection_event(), _frame())

    engine.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()


def test_face_detection_engine_crash_degrades_to_bare_bbox():
    """A broken face engine must not block the rest of the lifecycle."""
    alert = MagicMock()
    engine = MagicMock()
    engine.recognize.side_effect = RuntimeError("model OOM")
    d = _make_dispatcher(engine, alert)

    d._handle_face_detection(_detection_event(), _frame())

    alert.handle_personnel_seen.assert_not_called()
    alert.handle_face_intruder.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()
    assert alert.handle_behaviour_alert_simple.call_args.kwargs["alert_type"] == "face_detected"


def test_face_detection_no_frame_logs_bbox():
    """Without a frame we can't run inference — bare bbox is right."""
    alert = MagicMock()
    engine = MagicMock()
    d = _make_dispatcher(engine, alert)

    d._handle_face_detection(_detection_event(), None)

    engine.recognize.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_called_once()


def test_face_detection_no_engine_keeps_baseline_behavior():
    """Vanilla deploy with no ML wired → bare behaviour alert."""
    alert = MagicMock()
    d = _make_dispatcher(face_engine=None, alert_engine=alert)

    d._handle_face_detection(_detection_event(), _frame())

    alert.handle_behaviour_alert_simple.assert_called_once()


# ─── FaceRecognition routing ─────────────────────────────────────────


def test_face_recognition_ignores_camera_candidates():
    """Camera says 'Alice'; the engine says 'Sam' → the engine wins."""
    alert = MagicMock()
    engine = MagicMock()
    engine.recognize.return_value = _match(person_id="p-99", name="Sam", sim=0.82)
    d = _make_dispatcher(engine, alert)

    d._handle_face_recognition(
        _recognition_event(candidates=[{
            "Person": {"UID": "cam-uid-7", "GroupID": "g1", "Name": "Alice"},
            "Similarity": 92,
        }]),
        _frame(),
    )

    engine.recognize.assert_called_once()
    alert.handle_personnel_seen.assert_called_once()
    kw = alert.handle_personnel_seen.call_args.kwargs
    assert kw["person_uid"] == "p-99"      # engine's identity, not the camera's
    assert kw["name"] == "Sam"
    assert kw["group_id"] == "jetson-fallback"


def test_face_recognition_no_engine_match_fires_intruder():
    """Camera 'matched' someone, but the engine disagrees → INTRUDER."""
    alert = MagicMock()
    engine = MagicMock()
    engine.recognize.return_value = _no_match(sim=0.18)
    d = _make_dispatcher(engine, alert)

    d._handle_face_recognition(
        _recognition_event(candidates=[{
            "Person": {"UID": "cam-thinks-known", "GroupID": "g1", "Name": "GhostMatch"},
            "Similarity": 70,
        }]),
        _frame(),
    )

    alert.handle_face_intruder.assert_called_once()
    alert.handle_personnel_seen.assert_not_called()


def test_face_recognition_no_engine_skips_recognition():
    """No engine and no evaluator → log only, never blindly alarm."""
    alert = MagicMock()
    d = _make_dispatcher(face_engine=None, alert_engine=alert)

    d._handle_face_recognition(
        _recognition_event(candidates=[{
            "Person": {"UID": "cam-uid", "GroupID": "g1", "Name": "Alice"},
            "Similarity": 92,
        }]),
        _frame(),
    )

    alert.handle_personnel_seen.assert_not_called()
    alert.handle_face_intruder.assert_not_called()
    alert.handle_behaviour_alert_simple.assert_not_called()


# ─── threat evaluator owns the decision when wired ───────────────────


def test_evaluator_takes_over_both_face_events():
    """With an evaluator wired, both face events route to it and the engine
    is never called directly on the single event frame."""
    alert = MagicMock()
    engine = MagicMock()
    evaluator = MagicMock()
    d = _make_dispatcher(engine, alert, threat_evaluator=evaluator)

    d._handle_face_detection(_detection_event(), _frame())
    d._handle_face_recognition(_recognition_event(), _frame())

    assert evaluator.trigger.call_count == 2
    engine.recognize.assert_not_called()
    alert.handle_personnel_seen.assert_not_called()
    alert.handle_face_intruder.assert_not_called()
