"""IFC export envelope ring: chain EnvelopeWall segments by shared endpoints.

EnvelopeWall from_m/to_m have no common winding. Chaining on to_m alone
skipped a corner of the clean test building and closed the ring with a
diagonal, so the IFC got three walls (one cutting across the plan) instead
of four.
"""

from __future__ import annotations

import math

import pytest

from building_model import EnvelopeWall
from ifc_export import _bem_from_model, _build_ring
from tests.model_factory import make_clean_model


def _area(ring):
    return (
        abs(
            sum(
                ring[i][0] * ring[(i + 1) % len(ring)][1]
                - ring[(i + 1) % len(ring)][0] * ring[i][1]
                for i in range(len(ring))
            )
        )
        / 2.0
    )


def _w(i, facade, a, b):
    return EnvelopeWall(id=f"W{i}", facade=facade, from_m=list(a), to_m=list(b))


def test_clean_model_ring_is_the_full_rectangle():
    ring = _bem_from_model(make_clean_model()).ring_m
    assert sorted(ring) == sorted([(0, -6), (10, -6), (10, 0), (0, 0)])
    assert math.isclose(_area(ring), 60.0)


def test_mixed_segment_directions_chain_into_one_loop():
    walls = [
        _w(1, "south", (0, 6), (10, 6)),  # west -> east
        _w(2, "north", (10, 0), (0, 0)),  # east -> west
        _w(3, "east", (10, 0), (10, 6)),  # north -> south (reversed vs. chain)
        _w(4, "west", (0, 6), (0, 0)),
    ]
    ring = _build_ring(walls)
    assert len(ring) == 4
    assert math.isclose(_area(ring), 60.0)
    for i in range(4):  # every ring edge is axis-aligned: no diagonal
        a, b = ring[i], ring[(i + 1) % 4]
        assert a[0] == b[0] or a[1] == b[1]


def test_consistently_wound_walls_unchanged():
    walls = [
        _w(1, "south", (0, 6), (10, 6)),
        _w(2, "east", (10, 6), (10, 0)),
        _w(3, "north", (10, 0), (0, 0)),
        _w(4, "west", (0, 0), (0, 6)),
    ]
    assert _build_ring(walls) == [(0, -6), (10, -6), (10, 0), (0, 0)]


def test_gap_falls_back_to_nearer_endpoint():
    walls = [
        _w(1, "south", (0, 6), (10, 6)),
        _w(2, "east", (10.5, 0), (10.5, 6)),  # 0.5 m gap at the SE corner
    ]
    ring = _build_ring(walls)
    assert ring == [(0, -6), (10, -6), (10.5, -6), (10.5, 0)]


def test_ifc_has_four_walls_for_the_clean_model(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from ifc_export import _export_ifc

    path = _export_ifc(make_clean_model(), tmp_path / "ring.ifc")
    walls = ifcopenshell.open(str(path)).by_type("IfcWall")
    assert len(walls) == 4


def test_inflated_space_area_blocked_by_its_own_check():
    # #478 intent: the per-space check names the space, independent of the ring.
    from validate import validate_bem_conservation

    m = make_clean_model()
    sid = next(iter(m.spaces))
    m.spaces[sid].area_m2 = (m.spaces[sid].area_m2 or 0.0) * 1.15
    res = {r.check_id: r for r in validate_bem_conservation(_bem_from_model(m))}
    chk = res["bem_space_area_matches_polygon"]
    assert chk.severity == "error" and sid in chk.entities


def test_clean_model_space_areas_pass():
    from validate import validate_bem_conservation

    res = {r.check_id: r for r in validate_bem_conservation(_bem_from_model(make_clean_model()))}
    assert res["bem_space_area_matches_polygon"].severity == "pass"
