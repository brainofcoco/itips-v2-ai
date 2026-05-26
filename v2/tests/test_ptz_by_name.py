"""DahuaPTZ.goto_preset_by_name — name→index resolution + dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

from itips.camera.dahua_ptz import DahuaPTZ, PresetInfo


def _ptz_with_presets(presets):
    """Build a DahuaPTZ bypassing __init__'s network probe, with
    list_presets() returning the supplied fixture."""
    ptz = DahuaPTZ.__new__(DahuaPTZ)
    ptz._endpoint = MagicMock()
    ptz.camera_id = 4
    ptz._channel = 1
    ptz._timeout = 5.0
    ptz._connected = True
    ptz.list_presets = MagicMock(return_value=presets)
    ptz._call = MagicMock()
    return ptz


def test_goto_preset_by_name_matches_case_insensitively():
    ptz = _ptz_with_presets([
        PresetInfo(index=1, name="Home"),
        PresetInfo(index=7, name="Gate View"),
        PresetInfo(index=12, name="Generator"),
    ])
    assert ptz.goto_preset_by_name("gate view") is True
    # Index 7 should have been dispatched via _call as a GotoPreset.
    call = ptz._call.call_args.args[0]
    assert call["code"] == "GotoPreset"
    assert call["arg2"] == 7


def test_goto_preset_by_name_trims_whitespace():
    ptz = _ptz_with_presets([PresetInfo(index=3, name="Gate View")])
    assert ptz.goto_preset_by_name("  Gate View  ") is True
    assert ptz._call.call_args.args[0]["arg2"] == 3


def test_goto_preset_by_name_returns_false_when_unknown():
    ptz = _ptz_with_presets([
        PresetInfo(index=1, name="Home"),
        PresetInfo(index=7, name="Gate View"),
    ])
    assert ptz.goto_preset_by_name("Compound Centre") is False
    ptz._call.assert_not_called()


def test_goto_preset_by_name_rejects_empty():
    ptz = _ptz_with_presets([PresetInfo(index=1, name="Anything")])
    assert ptz.goto_preset_by_name("") is False
    assert ptz.goto_preset_by_name(None) is False  # defensive
