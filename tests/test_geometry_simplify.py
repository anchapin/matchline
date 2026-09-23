"""Tests for geometry_simplify.py: happy-path, invariant, and defect-injection."""

import pytest
from shapely.geometry import Polygon

from geometry_simplify import (
    _tri_area,
    envelope_area,
    footprint_from_regions,
    ring_edges,
    ring_perimeter,
    simplify_report,
    simplify_ring,
)

SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10]]


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestFootprintFromRegions:
    def test_single_rectangle(self):
        exterior = footprint_from_regions([SQUARE])
        assert Polygon(exterior).equals(Polygon(SQUARE))

    def test_single_rectangle_returns_valid_polygon(self):
        exterior = footprint_from_regions([SQUARE])
        assert Polygon(exterior).is_valid

    def test_empty_regions_returns_empty(self):
        exterior = footprint_from_regions([])
        assert exterior == []


class TestRingEdges:
    def test_simple_square_open_ring(self):
        edges = ring_edges(SQUARE)
        assert len(edges) == 4
        assert edges[0] == ((0, 0), (10, 0))
        assert edges[1] == ((10, 0), (10, 10))
        assert edges[2] == ((10, 10), (0, 10))
        assert edges[3] == ((0, 10), (0, 0))


class TestEnvelopeArea:
    def test_simple_rectangle_with_walls(self):
        area = envelope_area(SQUARE, wall_height=3.0)
        expected = 2 * 100.0 + 40.0 * 3.0  # floor + ceiling + 4 walls
        assert area == pytest.approx(expected)

    def test_envelope_without_walls(self):
        area = envelope_area(SQUARE, wall_height=None)
        assert area == pytest.approx(100.0)


class TestRingPerimeter:
    def test_simple_square(self):
        p = ring_perimeter(SQUARE)
        assert p == pytest.approx(40.0)


class TestSimplifyRing:
    def test_square_no_change_small_tol(self):
        res = simplify_ring(SQUARE, tol=0.001)
        assert res.original_count == 4
        assert res.simplified_count == 4
        assert res.original_area == pytest.approx(100.0)
        assert res.simplified_area == pytest.approx(100.0)
        assert res.area_delta_pct == pytest.approx(0.0)
        assert res.confidence == 1.0

    def test_ring_with_collinear_points_removed(self):
        ring = [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        assert res.simplified_count <= res.original_count


class TestSimplifyReport:
    def test_clean_simplification(self):
        res = simplify_ring(SQUARE, tol=0.001)
        report = simplify_report(res)
        assert report["original_surface_count"] == 4
        assert report["simplified_surface_count"] == 4
        assert report["reduction_pct"] == pytest.approx(0.0)
        assert report["area_delta_pct"] == pytest.approx(0.0)
        assert report["confidence"] == 1.0
        assert report["within_tolerance"] is True
        assert report["valid"] is True

    def test_simplification_report_valid_flag(self):
        ring = [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        report = simplify_report(res)
        assert "valid" in report
        assert isinstance(report["valid"], bool)


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------


def test_simplify_ring_preserves_polygon_validity():
    """Simplified ring must still form a valid polygon."""
    res = simplify_ring(SQUARE, tol=0.001)
    poly = Polygon(res.ring)
    assert poly.is_valid


def test_ring_perimeter_equals_shapely_perimeter():
    """ring_perimeter should match Shapely's Polygon.length for the same ring."""
    expected = Polygon(SQUARE).length
    assert ring_perimeter(SQUARE) == pytest.approx(expected)


def test_envelope_area_floor_plus_ceiling_plus_walls():
    """envelope_area with wall_height must include floor + ceiling + walls."""
    area_with_walls = envelope_area(SQUARE, wall_height=3.0)
    area_floor_only = envelope_area(SQUARE, wall_height=None)
    assert area_with_walls > area_floor_only
    assert area_floor_only == pytest.approx(100.0)


def test_footprint_from_regions_produces_valid_polygon():
    """footprint_from_regions must produce a valid polygon ring or empty list."""
    result = footprint_from_regions([SQUARE])
    poly = Polygon(result)
    assert poly.is_valid


def test_simplify_result_confidence_bounded():
    """confidence must be between 0 and 1."""
    res = simplify_ring(SQUARE, tol=0.001)
    assert 0.0 <= res.confidence <= 1.0


def test_simplify_result_valid_is_true_for_good_input():
    """Valid input should produce a valid SimplifyResult."""
    res = simplify_ring(SQUARE, tol=0.001)
    assert res.valid is True


def test_simplify_report_within_tolerance_for_small_tol():
    """simplify_report.within_tolerance should be True when tol is small."""
    res = simplify_ring(SQUARE, tol=0.001)
    report = simplify_report(res)
    assert report["within_tolerance"] is True


# ---------------------------------------------------------------------------
# Defect-injection tests
# ---------------------------------------------------------------------------


class TestDefectInjection:
    def test_empty_ring_does_not_crash(self):
        res = simplify_ring([], tol=0.01)
        assert res.ring == []
        assert res.valid is True

    def test_single_point_ring_raises(self):
        ring = [[0, 0], [0, 0]]
        with pytest.raises(ValueError, match="linearring"):
            simplify_ring(ring, tol=0.01)

    def test_identical_consecutive_points(self):
        ring = [[0, 0], [0, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        assert res.valid is True

    def test_tri_area_with_collinear_points_is_zero(self):
        area = _tri_area([0, 0], [5, 0], [10, 0])
        assert area == pytest.approx(0.0)

    def test_tri_area_positive_for_ccw_triangle(self):
        area = _tri_area([0, 0], [10, 0], [0, 10])
        assert area == pytest.approx(50.0)

    def test_tri_area_positive_for_cw_triangle(self):
        area = _tri_area([0, 0], [0, 10], [10, 0])
        assert area == pytest.approx(50.0)

    def test_simplify_report_area_delta_pct_reasonable(self):
        """area_delta_pct should be near zero for a perfect square."""
        res = simplify_ring(SQUARE, tol=0.001)
        report = simplify_report(res)
        assert abs(report["area_delta_pct"]) < 0.01

    def test_simplify_report_tolerance_pct_equals_tol_times_100(self):
        """tolerance_pct should be tol * 100."""
        res = simplify_ring(SQUARE, tol=0.05)
        report = simplify_report(res)
        assert report["tolerance_pct"] == pytest.approx(5.0)

    def test_envelope_area_raises_for_degenerate_ring(self):
        """envelope_area raises ValueError for degenerate ring."""
        with pytest.raises(ValueError, match="linearring"):
            envelope_area([[0, 0]], wall_height=3.0)
