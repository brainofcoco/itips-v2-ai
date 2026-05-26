"""OpenAIValidator — gating, cooldown, quota, verdict parsing, escalation flags.

Real OpenAI never imported. We inject a stub client via the `_client`
attribute and a tiny in-memory prompts dict.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from itips.ml.openai_validator import (
    OpenAIValidator,
    ValidationResult,
    _parse_response,
)


_PROMPTS_YAML = """\
scenarios:
  behavior_intrusion:
    model: gpt-4o-mini
    confidence_band: [0.30, 0.80]
    cooldown_s: 5
    system: "sys"
    user_template: "alert at {camera_id}"
  sensor_unverified:
    model: gpt-4o
    confidence_band: [0.0, 1.0]
    cooldown_s: 5
    system: "sys"
    user_template: "sensor at {camera_id}"
"""


def _make_validator(tmp_path, *, enabled=True, api_key="sk-test",
                    max_tokens_per_hour=1000):
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(_PROMPTS_YAML, encoding="utf-8")
    v = OpenAIValidator(
        api_key=api_key, prompts_path=prompts,
        default_model="gpt-4o-mini", enabled=enabled,
        max_tokens_per_hour=max_tokens_per_hour,
    )
    return v


def _stub_response(verdict="real", category="human", confidence=0.8,
                    summary="text", tokens=42):
    rsp = MagicMock()
    rsp.choices = [MagicMock()]
    rsp.choices[0].message.content = (
        f'{{"verdict": "{verdict}", "category": "{category}", '
        f'"confidence": {confidence}, "summary": "{summary}"}}'
    )
    rsp.usage = MagicMock(total_tokens=tokens)
    return rsp


def _frame():
    return np.zeros((480, 640, 3), dtype="uint8")


# ─── gating ─────────────────────────────────────────────────────────


def test_disabled_validator_never_fires(tmp_path):
    v = _make_validator(tmp_path, enabled=False)
    assert v.is_enabled() is False
    assert v.should_validate("behavior_intrusion", local_confidence=0.5) is False


def test_missing_key_disables_even_when_enabled(tmp_path):
    v = _make_validator(tmp_path, api_key=None)
    assert v.is_enabled() is False


def test_outside_confidence_band_skipped(tmp_path):
    v = _make_validator(tmp_path)
    # band is [0.30, 0.80] for behavior_intrusion
    assert v.should_validate("behavior_intrusion", local_confidence=0.10) is False
    assert v.should_validate("behavior_intrusion", local_confidence=0.90) is False
    assert v.should_validate("behavior_intrusion", local_confidence=0.50) is True


def test_unknown_scenario_skipped(tmp_path):
    v = _make_validator(tmp_path)
    assert v.should_validate("not_a_scenario", local_confidence=0.5) is False


def test_quota_blocks_further_calls(tmp_path):
    v = _make_validator(tmp_path, max_tokens_per_hour=50)
    v._record_tokens(60)   # blow the cap
    assert v.should_validate("behavior_intrusion", local_confidence=0.5) is False


# ─── validate() happy paths ─────────────────────────────────────────


def test_validate_returns_structured_result(tmp_path):
    v = _make_validator(tmp_path)
    client = MagicMock()
    client.chat.completions.create.return_value = _stub_response(
        verdict="false_positive", category="animal",
        confidence=0.92, summary="Dog crossed perimeter",
    )
    v._client = client
    result = v.validate("behavior_intrusion", _frame(),
                        {"camera_id": 4, "confidence": 0.5})
    assert result is not None
    assert result.verdict == "false_positive"
    assert result.category == "animal"
    assert result.confidence == pytest.approx(0.92)
    assert result.summary == "Dog crossed perimeter"
    assert result.tokens_used == 42
    assert result.should_suppress is True


def test_validate_caches_within_cooldown(tmp_path):
    v = _make_validator(tmp_path)
    client = MagicMock()
    client.chat.completions.create.return_value = _stub_response()
    v._client = client
    ctx = {"camera_id": 4, "confidence": 0.5}
    v.validate("behavior_intrusion", _frame(), ctx, cooldown_key=("intrusion", 4))
    v.validate("behavior_intrusion", _frame(), ctx, cooldown_key=("intrusion", 4))
    # API hit only once; second call served from cache.
    assert client.chat.completions.create.call_count == 1


def test_validate_returns_none_when_api_explodes(tmp_path):
    v = _make_validator(tmp_path)
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("network")
    v._client = client
    assert v.validate("behavior_intrusion", _frame(),
                      {"camera_id": 4, "confidence": 0.5}) is None


def test_validate_returns_none_when_response_is_garbage(tmp_path):
    v = _make_validator(tmp_path)
    client = MagicMock()
    bad = MagicMock()
    bad.choices = [MagicMock()]
    bad.choices[0].message.content = "not json"
    bad.usage = MagicMock(total_tokens=0)
    client.chat.completions.create.return_value = bad
    v._client = client
    assert v.validate("behavior_intrusion", _frame(),
                      {"camera_id": 4, "confidence": 0.5}) is None


# ─── verdict + escalation flags ─────────────────────────────────────


def test_high_conf_false_positive_should_suppress():
    r = ValidationResult(scenario="x", verdict="false_positive",
                         category="animal", confidence=0.95,
                         summary="", model="m")
    assert r.should_suppress is True


def test_low_conf_false_positive_does_not_suppress():
    r = ValidationResult(scenario="x", verdict="false_positive",
                         category="animal", confidence=0.50,
                         summary="", model="m")
    assert r.should_suppress is False


def test_fire_smoke_weapon_categories_escalate():
    for cat in ["fire", "smoke", "weapon"]:
        r = ValidationResult(scenario="x", verdict="real",
                             category=cat, confidence=0.9,
                             summary="", model="m")
        assert r.should_escalate is True


def test_routine_categories_do_not_escalate():
    for cat in ["human", "worker", "animal", "vehicle", "environmental", "unclear"]:
        r = ValidationResult(scenario="x", verdict="real",
                             category=cat, confidence=0.9,
                             summary="", model="m")
        assert r.should_escalate is False


# ─── parse_response edge cases ──────────────────────────────────────


def test_parse_clamps_confidence_to_unit_range():
    rsp = MagicMock()
    rsp.choices = [MagicMock()]
    rsp.choices[0].message.content = (
        '{"verdict":"real","category":"human","confidence":2.5,"summary":"x"}'
    )
    rsp.usage = MagicMock(total_tokens=0)
    result = _parse_response(rsp, scenario="x", model="m")
    assert result.confidence == 1.0


def test_parse_unknown_verdict_becomes_inconclusive():
    rsp = MagicMock()
    rsp.choices = [MagicMock()]
    rsp.choices[0].message.content = (
        '{"verdict":"yep","category":"human","confidence":0.5,"summary":""}'
    )
    rsp.usage = MagicMock(total_tokens=0)
    result = _parse_response(rsp, scenario="x", model="m")
    assert result.verdict == "inconclusive"


def test_parse_unknown_category_becomes_unclear():
    rsp = MagicMock()
    rsp.choices = [MagicMock()]
    rsp.choices[0].message.content = (
        '{"verdict":"real","category":"alien","confidence":0.5,"summary":""}'
    )
    rsp.usage = MagicMock(total_tokens=0)
    result = _parse_response(rsp, scenario="x", model="m")
    assert result.category == "unclear"


# ─── audit + token accounting ───────────────────────────────────────


def test_recent_ring_captures_verdicts(tmp_path):
    v = _make_validator(tmp_path)
    client = MagicMock()
    client.chat.completions.create.return_value = _stub_response(summary="cat in zone")
    v._client = client
    v.validate("behavior_intrusion", _frame(),
               {"camera_id": 4, "confidence": 0.5})
    recent = v.recent()
    assert len(recent) == 1
    assert recent[0]["summary"] == "cat in zone"
    assert recent[0]["scenario"] == "behavior_intrusion"


def test_hourly_token_window_prunes_old_entries(tmp_path):
    v = _make_validator(tmp_path)
    # Inject a stale token entry > 1h ago.
    v._token_log.append((time.time() - 7200, 999))
    v._record_tokens(50)
    used, _ = v.hourly_token_usage()
    assert used == 50   # stale row pruned
