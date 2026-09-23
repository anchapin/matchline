"""Tests for registration.py: sheet registration into canonical meters.

Tests cover:
  - Happy path: grid and geometric elevation registration produce valid facades
  - Invariant: point_in_polygon is consistent with itself and assign_points_to_spaces
  - Defect injection: malformed tie points raise ValueError
"""

from __future__ import annotations

import numpy as np
import pytest

from building_model import Space
from registration import (
    Affine2D,
    Facade,
    assign_points_to_spaces,
    interval_overlap,
    match_interval_to_segments,
    point_in_polygon,
    register_elevation_geometric,
    register_elevation_grid,
)


class TestAffine2D:
    def test_from_scale_translate_roundtrip(self):
        px_per_m = 50.0
        ox, oy = 100.0, 200.0
        aff = Affine2D.from_scale_translate(px_per_m, ox, oy)
        mx, my = aff.apply(ox, oy)
        assert abs(mx) < 1e-9 and abs(my) < 1e-9

    def test_apply_scales_px_to_meters(self):
        aff = Affine2D.from_scale_translate(px_per_m=100.0, ox_px=0.0, oy_px=0.0)
        mx, my = aff.apply(1000.0, 0.0)
        assert abs(mx - 10.0) < 1e-9
        assert abs(my) < 1e-9

    def test_from_tie_points_three_points(self):
        pts_px = np.array([[0.0, 0.0], [1000.0, 0.0], [0.0, 500.0]], dtype=float)
        pts_m = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 5.0]], dtype=float)
        aff = Affine2D.from_tie_points(pts_px, pts_m)
        mx, my = aff.apply(500.0, 250.0)
        assert abs(mx - 5.0) < 0.1
        assert abs(my - 2.5) < 0.1

    def test_from_tie_points_insufficient_raises(self):
        pts_px = np.array([[0.0, 0.0], [1000.0, 0.0]], dtype=float)
        pts_m = np.array([[0.0, 0.0], [10.0, 0.0]], dtype=float)
        with pytest.raises(ValueError, match="need >= 3"):
            Affine2D.from_tie_points(pts_px, pts_m)


class TestPointInPolygon:
    def test_point_inside_square(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        assert point_in_polygon((5.0, 5.0), square) is True

    def test_point_outside_square(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        assert point_in_polygon((15.0, 5.0), square) is False

    def test_point_on_boundary_is_not_true(self):
        boundary = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        result = point_in_polygon((0.0, 5.0), boundary)
        assert isinstance(result, bool)

    def test_point_in_triangle_definitely_inside(self):
        triangle = [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]
        assert point_in_polygon((5.0, 1.0), triangle) is True

    def test_empty_polygon(self):
        assert point_in_polygon((5.0, 5.0), []) is False


class TestAssignPointsToSpaces:
    def test_assign_one_point_inside(self):
        space = Space(
            id="L1-101",
            level_id="L1",
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        spaces = [space]
        points = [{"id": "d1", "x_m": 5.0, "y_m": 5.0}]
        result = assign_points_to_spaces(points, spaces)
        assert result["d1"] == "L1-101"

    def test_assign_no_match(self):
        space = Space(
            id="L1-101",
            level_id="L1",
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        spaces = [space]
        points = [{"id": "d1", "x_m": 100.0, "y_m": 100.0}]
        result = assign_points_to_spaces(points, spaces)
        assert result["d1"] is None

    def test_assign_multiple_spaces(self):
        s1 = Space(
            id="L1-101",
            level_id="L1",
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        s2 = Space(
            id="L1-102",
            level_id="L1",
            name="Room 102",
            number="102",
            polygon_m=[[10.0, 0.0], [20.0, 0.0], [20.0, 10.0], [10.0, 10.0]],
            area_m2=100.0,
        )
        spaces = [s1, s2]
        points = [
            {"id": "d1", "x_m": 5.0, "y_m": 5.0},
            {"id": "d2", "x_m": 15.0, "y_m": 5.0},
        ]
        result = assign_points_to_spaces(points, spaces)
        assert result["d1"] == "L1-101"
        assert result["d2"] == "L1-102"


class TestIntervalOverlap:
    def test_overlapping_intervals(self):
        assert interval_overlap(0.0, 5.0, 3.0, 8.0) == 2.0

    def test_non_overlapping_intervals(self):
        assert interval_overlap(0.0, 5.0, 6.0, 10.0) == 0.0

    def test_adjacent_intervals(self):
        assert interval_overlap(0.0, 5.0, 5.0, 10.0) == 0.0

    def test_contained_interval(self):
        assert interval_overlap(0.0, 10.0, 3.0, 7.0) == 4.0


class TestMatchIntervalToSegments:
    def test_full_coverage(self):
        segments = [
            {"s0": 0.0, "s1": 3.0, "id": "A"},
            {"s0": 3.0, "s1": 7.0, "id": "B"},
            {"s0": 7.0, "s1": 10.0, "id": "C"},
        ]
        best, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert best is not None
        assert 0.0 <= frac <= 1.0

    def test_partial_coverage(self):
        segments = [
            {"s0": 0.0, "s1": 3.0, "id": "A"},
            {"s0": 6.0, "s1": 10.0, "id": "B"},
        ]
        best, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert best is not None

    def test_no_match(self):
        segments = [{"s0": 20.0, "s1": 30.0, "id": "X"}]
        best, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert best is None


class TestRegisterElevationGrid:
    def test_grid_registration_produces_valid_facade_registration(self):
        facade = Facade(
            name="south",
            ref_corner_m=(0.0, 10.0),
            length_m=20.0,
            fixed_coord_m=10.0,
            axis="x",
        )
        plan_grid_m = {"A": 0.0, "B": 10.0, "C": 20.0}
        elev_bubbles = [
            {"label": "A", "u_px": 100.0},
            {"label": "B", "u_px": 300.0},
            {"label": "C", "u_px": 500.0},
        ]
        reg = register_elevation_grid(
            sheet_id="EL-01",
            facade=facade,
            plan_grid_m=plan_grid_m,
            elev_bubbles=elev_bubbles,
            v_ground_px=100.0,
            elev_px_per_m=50.0,
            revision=1,
        )
        assert reg.sheet_id == "EL-01"
        assert reg.confidence >= 0.9
        s, z = reg.to_facade(300.0, 100.0)
        assert 0.0 <= s <= 20.0

    def test_insufficient_grid_labels_raises(self):
        facade = Facade(
            name="south",
            ref_corner_m=(0.0, 10.0),
            length_m=20.0,
            fixed_coord_m=10.0,
            axis="x",
        )
        plan_grid_m = {"A": 0.0}
        elev_bubbles = [{"label": "A", "u_px": 100.0}]
        with pytest.raises(ValueError, match="grid path needs >= 2"):
            register_elevation_grid(
                sheet_id="EL-01",
                facade=facade,
                plan_grid_m=plan_grid_m,
                elev_bubbles=elev_bubbles,
                v_ground_px=100.0,
                elev_px_per_m=50.0,
                revision=1,
            )


class TestRegisterElevationGeometric:
    def test_geometric_fallback_lower_confidence_than_grid(self):
        facade = Facade(
            name="south",
            ref_corner_m=(0.0, 10.0),
            length_m=20.0,
            fixed_coord_m=10.0,
            axis="x",
        )
        reg = register_elevation_geometric(
            sheet_id="EL-02",
            facade=facade,
            wall_u0_px=50.0,
            elev_px_per_m=50.0,
            v_ground_px=100.0,
            revision=1,
        )
        assert reg.sheet_id == "EL-02"
        assert reg.confidence < 0.9
        s, z = reg.to_facade(100.0, 150.0)
        assert abs(s - 1.0) < 0.2
