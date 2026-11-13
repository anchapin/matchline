"""measure_takeoff: per-category polygon union before summation (#1382).

Overlapping same-category polygons used to be summed one by one, so a
wall drawn twice (two annotation polygons covering the same wall) was
measured twice. The reported area per category is now the area of the
UNION of that category's polygons. Counts stay one per region, and
categories never interfere with each other.
"""

from __future__ import annotations

import pytest

from datasets_adapter import DrawingScale, Region, measure_takeoff, polygon_area_px2

SCALE = DrawingScale(0.01, "test")


def _sq(x0: float, y0: float, w: float = 10.0, h: float = 10.0) -> list:
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]


def _region(category: str, poly, label: str = "") -> Region:
    return Region(category, poly, "test", "floor_plan", label)


# --- partial overlap -------------------------------------------------------


def test_partial_overlap_same_category_is_unioned():
    """Two 10x10 squares offset by 5 px cover 150 px^2, not 200 px^2."""
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(5, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(150.0)
    assert res.area_px2["wall"] != pytest.approx(200.0)


def test_partial_overlap_keeps_counts_one_per_region():
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(5, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.counts["wall"] == 2
    assert len(res.regions) == 2


def test_area_m2_follows_the_unioned_area():
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(5, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_m2["wall"] == pytest.approx(res.area_px2["wall"] * SCALE.m_per_px**2)
    assert res.area_m2["wall"] == pytest.approx(150.0 * 0.01**2)


def test_triple_overlap_is_unioned_once():
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(5, 0)), _region("wall", _sq(10, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(200.0)
    assert res.counts["wall"] == 3


# --- full containment ------------------------------------------------------


def test_fully_contained_polygon_is_not_double_counted():
    regions = [_region("wall", _sq(0, 0, 20, 20)), _region("wall", _sq(5, 5, 10, 10))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(400.0)
    assert res.counts["wall"] == 2


def test_identical_polygons_count_once():
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(0, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(100.0)
    assert res.counts["wall"] == 2


# --- disjoint / single polygon: values unchanged ----------------------------


def test_disjoint_same_category_sums_unchanged():
    regions = [_region("wall", _sq(0, 0)), _region("wall", _sq(100, 100))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(200.0)
    assert res.counts["wall"] == 2


def test_single_polygon_category_unchanged():
    poly = [(0, 0), (20, 0), (20, 30), (0, 30)]
    res = measure_takeoff([_region("floor_area", poly)], "floor_plan", SCALE)
    assert res.area_px2["floor_area"] == pytest.approx(polygon_area_px2(poly))
    assert res.area_px2["floor_area"] == pytest.approx(600.0)
    assert res.counts["floor_area"] == 1


def test_disjoint_polygons_keep_the_exact_previous_value():
    """Disjoint categories report the same float the old per-region sum gave."""
    a = [(0, 0), (3, 0), (3, 7), (0, 7)]
    b = [(10, 10), (17, 10), (17, 13), (10, 13)]
    regions = [_region("wall", a), _region("wall", b)]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == polygon_area_px2(a) + polygon_area_px2(b)


# --- cross-category ---------------------------------------------------------


def test_cross_category_overlap_does_not_affect_each_other():
    """A wall over a floor area: each category measures its own union."""
    regions = [_region("floor_area", _sq(0, 0, 20, 20)), _region("wall", _sq(0, 0, 20, 20))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["floor_area"] == pytest.approx(400.0)
    assert res.area_px2["wall"] == pytest.approx(400.0)


def test_overlap_in_one_category_leaves_other_categories_alone():
    regions = [
        _region("wall", _sq(0, 0)),
        _region("wall", _sq(5, 0)),
        _region("floor_area", _sq(0, 0, 50, 50)),
    ]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(150.0)
    assert res.area_px2["floor_area"] == pytest.approx(2500.0)
    assert res.counts == {"wall": 2, "floor_area": 1}


# --- degenerate / invalid input --------------------------------------------


def test_degenerate_polygon_does_not_raise():
    regions = [_region("wall", [(0, 0), (10, 0)]), _region("wall", _sq(0, 0))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(100.0)
    assert res.counts["wall"] == 2


def test_degenerate_polygon_alone_reports_zero():
    res = measure_takeoff([_region("wall", [(0, 0), (10, 0)])], "floor_plan", SCALE)
    assert res.area_px2["wall"] == pytest.approx(0.0)
    assert res.counts["wall"] == 1


def test_self_intersecting_polygon_does_not_raise():
    """A bow-tie ring is invalid for shapely: it keeps its best-effort area."""
    bowtie = [(0, 0), (10, 0), (0, 10), (10, 10)]
    regions = [_region("wall", bowtie)]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.counts["wall"] == 1
    assert res.area_px2["wall"] == pytest.approx(polygon_area_px2(bowtie))


def test_self_intersecting_polygon_beside_a_valid_one():
    bowtie = [(0, 0), (10, 0), (0, 10), (10, 10)]
    regions = [_region("wall", bowtie), _region("wall", _sq(100, 100))]
    res = measure_takeoff(regions, "floor_plan", SCALE)
    assert res.counts["wall"] == 2
    assert res.area_px2["wall"] == pytest.approx(polygon_area_px2(bowtie) + 100.0)


def test_empty_region_list_is_empty_result():
    res = measure_takeoff([], "floor_plan", SCALE)
    assert res.area_px2 == {}
    assert res.counts == {}
    assert res.area_m2 == {}
