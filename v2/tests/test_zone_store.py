"""ZoneStore + polygon geometry — pure-Python, no ML deps."""

from __future__ import annotations

import pytest

from itips.ml.zone_store import (
    ZONE_TYPE_LINE,
    ZONE_TYPE_REGION,
    Zone,
    ZoneStore,
    point_in_polygon,
    segments_intersect,
)


# ─── Zone construction validation ────────────────────────────────────


def test_region_zone_needs_at_least_three_points():
    with pytest.raises(ValueError):
        Zone(zone_id="z1", zone_type=ZONE_TYPE_REGION, points=[(0.0, 0.0), (1.0, 0.0)])


def test_line_zone_needs_at_least_two_points():
    with pytest.raises(ValueError):
        Zone(zone_id="z1", zone_type=ZONE_TYPE_LINE, points=[(0.0, 0.0)])


def test_invalid_zone_type_rejected():
    with pytest.raises(ValueError):
        Zone(zone_id="z1", zone_type="rectangle", points=[(0.0, 0.0), (1.0, 1.0)])


# ─── ZoneStore CRUD ─────────────────────────────────────────────────


def test_upsert_and_read_back(tmp_path):
    store = ZoneStore(path=tmp_path / "zones.json")
    z = Zone(zone_id="perimeter", zone_type=ZONE_TYPE_REGION,
             points=[(0.1, 0.5), (0.9, 0.5), (0.9, 0.95), (0.1, 0.95)],
             name="Compound perimeter")
    store.upsert_zone(camera_id=1, zone=z)
    zones = store.for_camera(1)
    assert len(zones) == 1
    assert zones[0].zone_id == "perimeter"
    assert zones[0].name == "Compound perimeter"


def test_upsert_replaces_in_place(tmp_path):
    store = ZoneStore(path=tmp_path / "zones.json")
    z1 = Zone(zone_id="z", zone_type=ZONE_TYPE_REGION,
              points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], name="v1")
    z2 = Zone(zone_id="z", zone_type=ZONE_TYPE_REGION,
              points=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9)], name="v2")
    store.upsert_zone(1, z1)
    store.upsert_zone(1, z2)
    zones = store.for_camera(1)
    assert len(zones) == 1
    assert zones[0].name == "v2"


def test_remove_zone(tmp_path):
    store = ZoneStore(path=tmp_path / "zones.json")
    store.upsert_zone(1, Zone(zone_id="z1", zone_type=ZONE_TYPE_REGION,
                              points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]))
    assert store.remove_zone(1, "z1") is True
    assert store.remove_zone(1, "z1") is False
    assert store.for_camera(1) == []


def test_persistence_across_instances(tmp_path):
    """A fresh ZoneStore reads back what the previous one wrote."""
    path = tmp_path / "zones.json"
    s1 = ZoneStore(path=path)
    s1.upsert_zone(2, Zone(zone_id="gate", zone_type=ZONE_TYPE_LINE,
                            points=[(0.0, 0.5), (1.0, 0.5)],
                            direction="LeftToRight"))
    s2 = ZoneStore(path=path)
    zones = s2.for_camera(2)
    assert len(zones) == 1
    assert zones[0].zone_id == "gate"
    assert zones[0].direction == "LeftToRight"


def test_replace_for_camera_overwrites_all(tmp_path):
    store = ZoneStore(path=tmp_path / "zones.json")
    store.upsert_zone(1, Zone(zone_id="a", zone_type=ZONE_TYPE_REGION,
                              points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]))
    store.upsert_zone(1, Zone(zone_id="b", zone_type=ZONE_TYPE_REGION,
                              points=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9)]))
    store.replace_for_camera(1, [Zone(zone_id="c", zone_type=ZONE_TYPE_REGION,
                                       points=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8)])])
    zones = store.for_camera(1)
    assert [z.zone_id for z in zones] == ["c"]


def test_corrupt_file_is_handled_gracefully(tmp_path):
    """A bad JSON file shouldn't take the whole runtime down."""
    path = tmp_path / "zones.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ZoneStore(path=path)  # must not raise
    assert store.for_camera(1) == []


# ─── geometry primitives ─────────────────────────────────────────────


def test_point_in_polygon_inside():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(0.5, 0.5, square) is True


def test_point_in_polygon_outside():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(1.5, 0.5, square) is False
    assert point_in_polygon(0.5, -0.5, square) is False


def test_point_in_polygon_concave_shape():
    """L-shape — verifies the ray-casting handles concavities."""
    # Inverted L
    poly = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.5),
            (0.5, 0.5), (0.5, 1.0), (0.0, 1.0)]
    assert point_in_polygon(0.25, 0.75, poly) is True   # in vertical arm
    assert point_in_polygon(0.75, 0.75, poly) is False  # outside arm


def test_segments_intersect_basic_cross():
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0)) is True


def test_segments_intersect_parallel_no_cross():
    assert segments_intersect((0, 0), (1, 0), (0, 1), (1, 1)) is False


def test_segments_intersect_offset_no_cross():
    """Two non-touching, non-parallel segments that miss each other."""
    # Segment A goes top-left to top-right; segment B is well below.
    assert segments_intersect((0, 0.9), (1, 0.9), (0.4, 0.1), (0.6, 0.2)) is False
