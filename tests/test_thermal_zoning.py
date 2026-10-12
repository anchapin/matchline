"""ASHRAE 90.1 Appendix G perimeter/core thermal blocks (#630)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from thermal_zoning import (
    PERIMETER_DEPTH_M,
    ZoningError,
    appendix_g_zoning,
    assign_spaces,
    orientation,
    outward_azimuth,
    perimeter_core,
)

D = PERIMETER_DEPTH_M


def _areas(blocks):
    return {b.id: b.area_m2 for b in blocks}


def _rot(ring, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for x, y in ring]


def _tiles(ring, blocks):
    plate = Polygon(ring)
    geoms = [b.geom for b in blocks]
    assert sum(g.area for g in geoms) == pytest.approx(plate.area, rel=1e-9)
    assert unary_union(geoms).symmetric_difference(plate).area < 1e-6
    for i, a in enumerate(geoms):
        for b in geoms[i + 1 :]:
            assert a.intersection(b).area < 1e-6


def test_depth_is_15_ft():
    assert D == pytest.approx(15 * 0.3048)


@pytest.mark.parametrize(
    "p0,p1,want",
    [
        ((0, 0), (10, 0), "south"),
        ((10, 0), (10, 5), "east"),
        ((10, 5), (0, 5), "north"),
        ((0, 5), (0, 0), "west"),
    ],
)
def test_ccw_edge_orientation(p0, p1, want):
    assert orientation(outward_azimuth(p0, p1)) == want


def test_orientation_buckets_are_plus_minus_45():
    assert orientation(0) == orientation(44.9) == orientation(315) == "north"
    assert orientation(45) == "east"
    assert orientation(225) == "west"


def test_rectangle_matches_the_trapezoids():
    ring = [(0, 0), (30, 0), (30, 20), (0, 20)]
    a = _areas(perimeter_core(ring))
    assert a["perimeter-north"] == pytest.approx((30 + 30 - 2 * D) / 2 * D)
    assert a["perimeter-south"] == pytest.approx(a["perimeter-north"])
    assert a["perimeter-east"] == pytest.approx((20 + 20 - 2 * D) / 2 * D)
    assert a["perimeter-west"] == pytest.approx(a["perimeter-east"])
    assert a["core"] == pytest.approx((30 - 2 * D) * (20 - 2 * D))


def test_cw_ring_gives_the_same_blocks():
    ring = [(0, 0), (30, 0), (30, 20), (0, 20)]
    assert _areas(perimeter_core(ring[::-1])) == pytest.approx(_areas(perimeter_core(ring)))


def test_small_plate_has_no_core_and_splits_on_the_ridge():
    a = _areas(perimeter_core([(0, 0), (8, 0), (8, 6), (0, 6)]))
    assert "core" not in a
    assert a["perimeter-east"] == pytest.approx(9.0)  # triangle 6 x 3
    assert a["perimeter-north"] == pytest.approx(15.0)  # trapezoid (8 + 2) / 2 x 3


def test_l_shape_is_mirror_symmetric_and_tiles():
    ring = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]
    blocks = perimeter_core(ring)
    a = _areas(blocks)
    # mirror about y = x swaps south<->west and east<->north
    assert a["perimeter-south"] == pytest.approx(a["perimeter-west"])
    assert a["perimeter-east"] == pytest.approx(a["perimeter-north"])
    _tiles(ring, blocks)


def test_u_shape_tiles_with_matching_arms():
    ring = [(0, 0), (40, 0), (40, 30), (25, 30), (25, 12), (15, 12), (15, 30), (0, 30)]
    blocks = perimeter_core(ring)
    a = _areas(blocks)
    assert a["perimeter-east"] == pytest.approx(a["perimeter-west"])
    _tiles(ring, blocks)


def test_rotation_moves_walls_between_buckets_but_keeps_areas():
    ring = [(0, 0), (30, 0), (30, 20), (0, 20)]
    a = _areas(perimeter_core(_rot(ring, 30)))
    assert sorted(a.values()) == pytest.approx(sorted(_areas(perimeter_core(ring)).values()))


def test_two_walls_in_one_bucket_merge():
    # 40 deg rotation: the long south wall faces 220 (south), the long north
    # wall 40 (north); the 30 deg-off short walls stay east/west
    blocks = perimeter_core(_rot([(0, 0), (30, 0), (30, 20), (0, 20)], 40))
    assert {b.orientation for b in blocks if b.kind == "perimeter"} == {
        "north",
        "east",
        "south",
        "west",
    }


def test_triangle_has_no_north_block():
    ring = [(0, 0), (30, 0), (15, 20)]
    blocks = perimeter_core(ring)
    assert "perimeter-north" not in _areas(blocks)
    _tiles(ring, blocks)


def test_prefix_names_blocks_per_level():
    ids = [b.id for b in perimeter_core([(0, 0), (30, 0), (30, 20), (0, 20)], prefix="L2-")]
    assert ids == [
        "L2-perimeter-north",
        "L2-perimeter-east",
        "L2-perimeter-south",
        "L2-perimeter-west",
        "L2-core",
    ]


def test_empty_plate_raises():
    with pytest.raises(ZoningError):
        perimeter_core([(0, 0), (1, 0), (2, 0)])


def test_space_shares_sum_to_one_and_pick_the_majority():
    blocks = perimeter_core([(0, 0), (30, 0), (30, 20), (0, 20)])
    # a 10 x 6 room on the south wall in the middle: 4.572 m in the south
    # block, the rest in the core
    room = SimpleNamespace(sid="SP1", polygon_m=[(10, 0), (20, 0), (20, 6), (10, 6)])
    corner = SimpleNamespace(sid="SP2", polygon_m=[(0, 0), (4, 0), (4, 4), (0, 4)])
    z1, z2 = assign_spaces(blocks, [room, corner])
    assert sum(z1.fractions.values()) == pytest.approx(1.0)
    assert z1.fractions["perimeter-south"] == pytest.approx(D / 6)
    assert z1.fractions["core"] == pytest.approx(1 - D / 6)
    assert z1.majority == "perimeter-south"
    # a square corner room splits evenly on the 45 deg bisector
    assert z2.fractions == pytest.approx({"perimeter-south": 0.5, "perimeter-west": 0.5})
    assert z2.majority == "perimeter-south"  # tie: first listed block wins


def test_appendix_g_zoning_reads_a_bem_model():
    m = SimpleNamespace(
        ring_m=[(0, 0), (20, 0), (20, 12), (0, 12)],
        spaces=[SimpleNamespace(sid="SP1", polygon_m=[(0, 0), (20, 0), (20, 12), (0, 12)])],
    )
    blocks, shares = appendix_g_zoning(m)
    assert "core" in _areas(blocks)
    assert sum(shares[0].fractions.values()) == pytest.approx(1.0)
