"""Shared pytest fixtures.

The repo root is added to sys.path so `from itips...` and `from config...`
both resolve when pytest is invoked from anywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add the v2/ root to sys.path so both `itips` and `config` import cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_intake_db(tmp_path) -> Path:
    return tmp_path / "intake.sqlite"


@pytest.fixture
def tmp_evidence_root(tmp_path) -> Path:
    return tmp_path / "evidence"


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path):
    """Default-safe env so tests never accidentally hit real services."""
    monkeypatch.setenv("ITIPS_MODE", "test")
    monkeypatch.setenv("ITIPS_SIMULATE", "true")
    monkeypatch.setenv("ITIPS_SITE_ID", "site-test")
    monkeypatch.setenv("ITIPS_OPERATOR_ID", "op-test")
    monkeypatch.setenv("ITIPS_DEVICE_ID", "jtx-test")
    monkeypatch.setenv("ITIPS_DEVICE_HMAC_KEY",
                       "a" * 64)  # 32 bytes of 0xaa
    monkeypatch.setenv("ITIPS_EVIDENCE_STORE_PATH", str(tmp_path / "ev"))
    monkeypatch.setenv("ITIPS_INTAKE_DB_PATH", str(tmp_path / "intake.sqlite"))
    monkeypatch.setenv("ITIPS_INBOUND_TOKEN", "test-token")
    yield
