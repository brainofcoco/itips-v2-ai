"""PlateEngine — OCR filtering + bbox math, with a fake EasyOCR reader.

Real EasyOCR is never imported. The engine's `_ensure_model` hook is
overridden so we can drop a stub `reader` into place without the dep.
This validates the plate-pattern filter, confidence threshold, bbox
offset arithmetic, and crop handling — not the underlying CRNN.
"""

from __future__ import annotations

import numpy as np

from itips.ml.plate_engine import (
    PlateEngine,
    PlateEngineUnavailable,
    _clean_plate_text,
    _looks_like_plate,
    _points_to_bbox,
)


class _FakeReader:
    """Stand-in for `easyocr.Reader`. Returns a queued list per call."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls: list[tuple[int, int]] = []  # (h, w) of each crop

    def readtext(self, frame, **kwargs):
        self.calls.append((frame.shape[0], frame.shape[1]))
        return list(self.rows)


def _row(points, text, conf):
    """EasyOCR's `detail=1` row shape."""
    return [points, text, conf]


def _make_engine(reader: _FakeReader, threshold: float = 0.5) -> PlateEngine:
    engine = PlateEngine(min_confidence=threshold)
    engine._reader = reader
    engine._ready.set()
    return engine


def _frame(h: int = 480, w: int = 640) -> np.ndarray:
    return np.zeros((h, w, 3), dtype="uint8")


# ─── pattern filter ─────────────────────────────────────────────────


def test_looks_like_plate_accepts_nigerian_format():
    assert _looks_like_plate("LAG123XY") is True
    assert _looks_like_plate("ABC12DE") is True


def test_looks_like_plate_rejects_all_letters():
    assert _looks_like_plate("STOPSIGN") is False


def test_looks_like_plate_rejects_all_digits():
    assert _looks_like_plate("123456") is False


def test_looks_like_plate_rejects_too_short_or_too_long():
    assert _looks_like_plate("AB12") is False         # 4 chars
    assert _looks_like_plate("ABCDEF12345") is False  # 11 chars


def test_clean_plate_text_strips_separators_and_uppercases():
    assert _clean_plate_text("lag-123-xy") == "LAG123XY"
    assert _clean_plate_text("ABC 12 DE") == "ABC12DE"
    assert _clean_plate_text("plate: KJA-555-AB!") == "PLATEKJA555AB"


# ─── reading ─────────────────────────────────────────────────────────


def test_read_plate_picks_highest_confidence_plate_like_text():
    reader = _FakeReader([
        _row([(0, 0), (50, 0), (50, 20), (0, 20)], "STOPSIGN", 0.92),  # ignored
        _row([(0, 0), (100, 0), (100, 30), (0, 30)], "LAG-123-XY", 0.81),
        _row([(0, 0), (100, 0), (100, 30), (0, 30)], "ABC-99-DE", 0.65),
    ])
    engine = _make_engine(reader)
    result = engine.read_plate(_frame())
    assert result is not None
    assert result.plate_number == "LAG123XY"
    assert result.confidence == 0.81


def test_read_plate_returns_none_below_confidence_floor():
    reader = _FakeReader([
        _row([(0, 0), (50, 0), (50, 20), (0, 20)], "LAG-123-XY", 0.40),
    ])
    engine = _make_engine(reader, threshold=0.5)
    assert engine.read_plate(_frame()) is None


def test_read_plate_returns_none_when_no_plate_like_strings():
    reader = _FakeReader([
        _row([(0, 0), (50, 0), (50, 20), (0, 20)], "WELCOME", 0.99),
        _row([(0, 0), (50, 0), (50, 20), (0, 20)], "555-1234", 0.95),
    ])
    engine = _make_engine(reader)
    assert engine.read_plate(_frame()) is None


def test_vehicle_bbox_translates_plate_bbox_back_to_frame_coords():
    """If we cropped to a vehicle bbox, the returned plate bbox must
    be in the original frame's coordinate system."""
    reader = _FakeReader([
        _row([(10, 10), (110, 10), (110, 40), (10, 40)], "LAG-123-XY", 0.9),
    ])
    engine = _make_engine(reader)
    # Vehicle bbox starts at (200, 100) in the full frame.
    result = engine.read_plate(_frame(h=480, w=640),
                                vehicle_bbox=(200, 100, 500, 400))
    assert result is not None
    # OCR said plate is at (10, 10) inside the crop → (210, 110) in
    # the full frame after offsetting by the crop's top-left.
    x1, y1, _, _ = result.bbox
    assert x1 == 210.0
    assert y1 == 110.0


def test_vehicle_bbox_hands_smaller_array_to_reader():
    reader = _FakeReader([])
    engine = _make_engine(reader)
    engine.read_plate(_frame(h=480, w=640), vehicle_bbox=(100, 100, 300, 300))
    seen_h, seen_w = reader.calls[-1]
    assert seen_h == 200 and seen_w == 200  # crop dims


def test_invalid_vehicle_bbox_falls_back_to_full_frame():
    reader = _FakeReader([])
    engine = _make_engine(reader)
    # bbox outside the frame → engine should hand the full frame in.
    engine.read_plate(_frame(h=240, w=320), vehicle_bbox=(500, 500, 600, 600))
    seen_h, seen_w = reader.calls[-1]
    assert seen_h == 240 and seen_w == 320


# ─── error surface ──────────────────────────────────────────────────


def test_plate_engine_unavailable_when_easyocr_missing(monkeypatch):
    engine = PlateEngine()
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("easyocr"):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    try:
        engine._ensure_model()
    except PlateEngineUnavailable as e:
        assert "easyocr" in str(e).lower()
    else:
        raise AssertionError("expected PlateEngineUnavailable")


# ─── helpers ─────────────────────────────────────────────────────────


def test_points_to_bbox_with_offset():
    pts = [(5, 10), (45, 10), (45, 30), (5, 30)]
    assert _points_to_bbox(pts, off_x=100, off_y=200) == (105.0, 210.0, 145.0, 230.0)
