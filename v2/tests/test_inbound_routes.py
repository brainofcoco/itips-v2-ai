from itips.api.inbound import _apply_command, _apply_maintenance, _apply_personnel_sync


class _FakeFaceEngine:
    def __init__(self):
        self.calls = []

    def apply_personnel_sync(self, payload):
        self.calls.append(payload)


class _FakeAuthorizer:
    def __init__(self):
        self.calls = []

    def apply_maintenance_window(self, payload):
        self.calls.append(payload)


class _FakePTZ:
    def __init__(self):
        self.calls = []

    def apply_override(self, params):
        self.calls.append(params)


def test_personnel_sync_routes_to_face_engine():
    engine = _FakeFaceEngine()
    result = _apply_personnel_sync({"action": "add", "person_id": "p1"}, engine)
    assert result == {"synced": True}
    assert engine.calls == [{"action": "add", "person_id": "p1"}]


def test_personnel_sync_rejects_missing_fields():
    engine = _FakeFaceEngine()
    result = _apply_personnel_sync({"action": "add"}, engine)
    assert result["synced"] is False


def test_maintenance_window_routes_to_authorizer():
    auth = _FakeAuthorizer()
    payload = {
        "action": "arm",
        "window_id": "w1",
        "person_id": "p1",
        "start_utc": "2026-05-25T10:00:00Z",
        "end_utc": "2026-05-25T14:00:00Z",
    }
    assert _apply_maintenance(payload, auth) == {"applied": True}
    assert auth.calls == [payload]


def test_command_ptz_override():
    ptz = _FakePTZ()
    result = _apply_command(
        {"command_type": "ptz_override", "parameters": {"camera_id": 1, "pan_degrees": 45}},
        {1: ptz},
    )
    assert result == {"command_accepted": True}
    assert ptz.calls == [{"camera_id": 1, "pan_degrees": 45}]


def test_command_request_stream_returns_url_hint():
    result = _apply_command(
        {"command_type": "request_stream", "parameters": {"camera_id": 2}},
        {},
    )
    assert result["command_accepted"] is True
    assert result["stream"]["url_hint"] == "/video_feed/2"


def test_command_unknown_rejected():
    result = _apply_command({"command_type": "what"}, {})
    assert result["command_accepted"] is False
