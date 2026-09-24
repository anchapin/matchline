"""Defect-injection tests for _check_space_area_matches_polygon conservation law.

Conservation law: space floor area (area_m2) must equal the shoelace-computed
area of its polygon_m, within tolerance_pct (0.1% = 1e-3 relative error).

Tests verify the check fires (warn) when area_m2 deviates > tolerance from the
polygon area, and passes (pass) when the building is clean.
"""

from validate import _build_ctx, _check_space_area_matches_polygon


class TestSpaceAreaConservesWithPolygon:
    """Verify space area conservation law (space.area_m2 == shoelace(polygon))."""

    def test_space_area_conservation_fails_on_mismatch(self, bldg_3room_wall_area_mismatch):
        """Check fires warn when a space area exceeds polygon area by 15%."""
        _bldg, model, _report = bldg_3room_wall_area_mismatch
        ctx = _build_ctx(model)
        result = _check_space_area_matches_polygon(ctx)
        assert result.severity == "warn", f"Expected warn on mismatched area, got {result.severity}"
        assert "L1-101" in result.entities, "Error should reference L1-101"

    def test_space_area_conservation_passes_on_exact(self, bldg_3room):
        """Check passes (ok) when all space areas match their polygon areas."""
        _bldg, model, _report = bldg_3room
        ctx = _build_ctx(model)
        result = _check_space_area_matches_polygon(ctx)
        assert result.severity == "pass", f"Expected pass on clean building, got {result.severity}"
