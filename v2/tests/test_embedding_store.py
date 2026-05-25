"""EmbeddingStore — SQLite + numpy roundtrip."""

from __future__ import annotations

import numpy as np

from itips.ml.embedding_store import EmbeddingStore


def _vec(n: int = 512, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(size=n).astype("float32")


def test_upsert_roundtrip(tmp_path):
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    v = _vec()
    store.upsert(person_id="p1", full_name="Worker One", embedding=v)
    rec = store.get("p1")
    assert rec is not None
    assert rec.full_name == "Worker One"
    assert rec.dim == 512
    # Stored vector should be L2-normalised.
    assert abs(float(np.linalg.norm(rec.embedding)) - 1.0) < 1e-5


def test_upsert_replaces_in_place(tmp_path):
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    store.upsert(person_id="p1", full_name="Old", embedding=_vec(seed=1))
    store.upsert(person_id="p1", full_name="New", embedding=_vec(seed=2))
    assert store.count() == 1
    assert store.get("p1").full_name == "New"


def test_delete_returns_true_on_hit_false_on_miss(tmp_path):
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    store.upsert(person_id="p1", full_name="X", embedding=_vec())
    assert store.delete("p1") is True
    assert store.delete("p1") is False
    assert store.count() == 0


def test_list_all_returns_independent_buffers(tmp_path):
    """A copy guard — sqlite buffers must not leak into caller arrays."""
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    store.upsert(person_id="p1", full_name="A", embedding=_vec(seed=1))
    store.upsert(person_id="p2", full_name="B", embedding=_vec(seed=2))
    records = store.list_all()
    assert len(records) == 2
    # Mutate one — must not propagate to the store.
    records[0].embedding[0] = 999.0
    fresh = store.get(records[0].person_id)
    assert abs(float(fresh.embedding[0]) - 999.0) > 1.0


def test_rejects_multi_dimensional_embedding(tmp_path):
    store = EmbeddingStore(db_path=tmp_path / "emb.sqlite")
    try:
        store.upsert(person_id="p1", full_name="X",
                     embedding=np.zeros((2, 512), dtype="float32"))
    except ValueError as e:
        assert "1-D" in str(e)
    else:
        raise AssertionError("expected ValueError on 2-D embedding")
