"""FaceEngine — recognition logic only, with a fake InsightFace app.

We never import or run real InsightFace here. The engine's `_ensure_model`
hook is overridden so we can drop a stub `face_app` into place without
the dep. This validates the matching, threshold, and routing logic —
not the underlying neural network.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import io

import cv2
import pytest
from PIL import Image

from itips.ml.embedding_store import EmbeddingStore
from itips.ml.face_engine import (
    FaceEngine,
    FaceEngineUnavailable,
    RecognitionResult,
    _decode_image,
)


@dataclass
class _FakeFace:
    bbox: tuple[float, float, float, float]
    normed_embedding: np.ndarray


class _FakeApp:
    """Stand-in for `insightface.app.FaceAnalysis`."""

    def __init__(self, faces_for_next_call: list[_FakeFace]) -> None:
        self.faces = faces_for_next_call
        self.calls: list[tuple[int, int]] = []  # (h, w)

    def get(self, frame):
        self.calls.append((frame.shape[0], frame.shape[1]))
        return list(self.faces)


def _make_engine(tmp_path, fake_app: _FakeApp, threshold: float = 0.35) -> FaceEngine:
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    engine = FaceEngine(embedding_store=store, similarity_threshold=threshold)
    # Skip the real loader.
    engine._app = fake_app
    engine._ready.set()
    return engine


def _unit_vec(seed: int, dim: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype("float32")
    return v / float(np.linalg.norm(v))


def _make_frame(h: int = 480, w: int = 640) -> np.ndarray:
    return np.zeros((h, w, 3), dtype="uint8")


# ─── enrolment ───────────────────────────────────────────────────────


def test_enrol_stores_embedding_for_largest_face(tmp_path, monkeypatch):
    # A small face + a large face → engine picks the large one.
    small = _FakeFace(bbox=(0, 0, 30, 30), normed_embedding=_unit_vec(1))
    big = _FakeFace(bbox=(0, 0, 200, 200), normed_embedding=_unit_vec(2))
    app = _FakeApp([small, big])
    engine = _make_engine(tmp_path, app)
    # Stub the JPEG decode so we don't have to build a real image.
    import itips.ml.face_engine as fe
    monkeypatch.setattr(fe, "_decode_image", lambda _b: _make_frame())

    rec = engine.enroll(person_id="p1", full_name="Worker", image_bytes=b"x")
    assert rec.person_id == "p1"
    assert np.allclose(rec.embedding, big.normed_embedding, atol=1e-5)


def test_enrol_raises_when_no_face(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, _FakeApp([]))
    import itips.ml.face_engine as fe
    monkeypatch.setattr(fe, "_decode_image", lambda _b: _make_frame())
    try:
        engine.enroll(person_id="p1", full_name="X", image_bytes=b"x")
    except ValueError as e:
        assert "no face" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")


# ─── recognition ─────────────────────────────────────────────────────


def test_recognise_returns_match_above_threshold(tmp_path):
    seed_embedding = _unit_vec(42)
    app = _FakeApp([_FakeFace(bbox=(10, 10, 110, 110),
                              normed_embedding=seed_embedding)])
    engine = _make_engine(tmp_path, app)
    # Enrol the SAME embedding the next recognise() will see — should
    # be a perfect match (cosine 1.0).
    engine._store.upsert(person_id="p1", full_name="Worker", embedding=seed_embedding)

    result = engine.recognize(_make_frame(), bbox=(10, 10, 110, 110))
    assert result.matched is True
    assert result.person_id == "p1"
    assert result.full_name == "Worker"
    assert result.similarity > 0.99


def test_recognise_returns_unmatched_below_threshold(tmp_path):
    app = _FakeApp([_FakeFace(bbox=(10, 10, 110, 110),
                              normed_embedding=_unit_vec(1))])
    engine = _make_engine(tmp_path, app, threshold=0.9)
    # Different seed → near-orthogonal → very low cosine.
    engine._store.upsert(person_id="p1", full_name="W", embedding=_unit_vec(2))

    result = engine.recognize(_make_frame(), bbox=(10, 10, 110, 110))
    assert result.matched is False
    assert result.person_id is None
    assert result.display_name == "INTRUDER"


def test_recognise_empty_store_returns_unmatched(tmp_path):
    app = _FakeApp([_FakeFace(bbox=(0, 0, 100, 100),
                              normed_embedding=_unit_vec(1))])
    engine = _make_engine(tmp_path, app)
    # No-one enrolled.
    result = engine.recognize(_make_frame())
    assert result.matched is False
    assert result.embedding is not None


def test_recognise_no_face_in_frame_returns_unmatched(tmp_path):
    engine = _make_engine(tmp_path, _FakeApp([]))
    result = engine.recognize(_make_frame())
    assert result.matched is False
    assert result.embedding is None


def test_recognise_crops_to_bbox(tmp_path):
    # When a bbox is supplied, the engine should hand a CROP (smaller
    # than the full frame) to the underlying app. We verify by reading
    # the shape of the array `get()` saw.
    app = _FakeApp([_FakeFace(bbox=(0, 0, 50, 50), normed_embedding=_unit_vec(1))])
    engine = _make_engine(tmp_path, app)
    engine.recognize(_make_frame(h=480, w=640), bbox=(100, 100, 200, 200))
    seen_h, seen_w = app.calls[-1]
    assert seen_h < 480 and seen_w < 640  # crop, not full frame


# ─── error surfaces ──────────────────────────────────────────────────


def test_face_engine_unavailable_when_insightface_missing(tmp_path, monkeypatch):
    """If `import insightface` fails the engine raises a typed error."""
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    engine = FaceEngine(embedding_store=store)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("insightface"):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(__builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__,
                        "__import__", fake_import)
    try:
        engine._ensure_model()
    except FaceEngineUnavailable as e:
        assert "insightface" in str(e).lower()
    else:
        raise AssertionError("expected FaceEngineUnavailable")


# ─── image decode ─────────────────────────────────────────────────────


def test_decode_image_reads_jpeg():
    ok, buf = cv2.imencode(".jpg", _make_frame(h=64, w=48))
    assert ok
    frame = _decode_image(buf.tobytes())
    assert frame.shape[:2] == (64, 48)


def test_decode_image_reads_png():
    ok, buf = cv2.imencode(".png", _make_frame(h=32, w=32))
    assert ok
    frame = _decode_image(buf.tobytes())
    assert frame.shape[:2] == (32, 32)


def test_decode_image_falls_back_to_pillow_for_bmp():
    # cv2 reads BMP too, but this proves a Pillow-encoded stream decodes.
    pil_bytes = io.BytesIO()
    Image.new("RGB", (40, 24), (10, 20, 30)).save(pil_bytes, format="BMP")
    frame = _decode_image(pil_bytes.getvalue())
    assert frame.shape[:2] == (24, 40)


def test_decode_image_rejects_garbage_with_clear_error():
    with pytest.raises(ValueError) as exc:
        _decode_image(b"this is definitely not an image")
    assert "could not decode" in str(exc.value)


def test_decode_image_names_heic_in_error():
    # Minimal HEIC-looking header: [size]ftypheic — enough for the sniff.
    fake_heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
    with pytest.raises(ValueError) as exc:
        _decode_image(fake_heic)
    assert "HEIC" in str(exc.value)
