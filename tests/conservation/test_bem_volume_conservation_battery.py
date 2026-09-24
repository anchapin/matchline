"""Integration tests for bem_volume_conservation through the full BATTERY pipeline.

Dedicated integration test for the bem_volume_conservation conservation law:
  https://github.com/asymptotic/isssueboards/issues/325

Uses the full BATTERY pipeline: generate synthetic building via synth.*, run through
run_checks(), verify the conservation check passes, inject defect, verify check fails.
"""

from __future__ import annotations

import pytest

from run_pipeline import build_model
from synth.multidiscipline import generate_building
from validate import run_checks


def _linked(seed=101, open_office_span=False, elevation_key="elev_grid"):
    """Build linked BuildingModel from synthetic building.

    Mirrors the approach used in test_bem_conservation.py.
    """
    bldg = generate_building(seed, open_office_span=open_office_span)
    model, report = build_model(
        bldg, elevation_key=elevation_key, building_name=bldg["building_id"]
    )
    return model


class TestBEMVolumeConservationBattery:
    """bem_volume_conservation through full BATTERY.

    The check lives in validate._check_bem_volume_conservation and is
    reached via run_checks() when it builds the BEM model internally.
    """

    @pytest.fixture
    def clean_model(self):
        """Build a clean (undefective) linked building model."""
        return _linked(seed=42)

    # ------------------------------------------------------------------
    # Happy path: clean model passes bem_volume_conservation
    # ------------------------------------------------------------------

    def test_clean_model_passes(self, clean_model):
        """Clean model: bem_volume_conservation check passes with no errors."""
        model = clean_model
        report = run_checks(model)

        # Filter to bem_volume_conservation errors only
        errors = [
            r
            for r in report.results
            if r.check_id == "bem_volume_conservation" and r.severity == "error"
        ]
        assert len(errors) == 0, f"bem_volume_conservation error on clean model: {errors}"

    # ------------------------------------------------------------------
    # Defect injection: break_bem_volume_conservation triggers an error
    # ------------------------------------------------------------------

    def test_broken_bem_volume_triggers_error(self, clean_model):
        """break_bem_volume_conservation injection triggers bem_volume_conservation error."""
        from geometry_simplify import footprint_from_regions, simplify_ring
        from run_pipeline import model_from_linked_model
        from tests.model_factory import break_bem_volume_conservation
        from validate import _check_bem_volume_conservation

        model = clean_model

        # Build BEM model from the building
        sres = simplify_ring(
            footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
            tol=0.02,
            wall_height=model.levels[0].wall_height_m,
        )
        bem = model_from_linked_model(
            model=model,
            simplified_ring=sres.ring,
            wall_height_m=model.levels[0].wall_height_m,
            simplify_tolerance=0.5,
        )

        # Inject defect: set space volumes to large values
        break_bem_volume_conservation(bem)

        # Run the check on the modified BEM model
        result = _check_bem_volume_conservation(bem, tol_volume=0.1)
        assert result.severity == "error", (
            f"Expected bem_volume_conservation error after break, got {result.severity}"
        )

    # ------------------------------------------------------------------
    # Error message quality
    # ------------------------------------------------------------------

    def test_error_message_includes_details(self, clean_model):
        """bem_volume_conservation error message includes volume details."""
        from geometry_simplify import footprint_from_regions, simplify_ring
        from run_pipeline import model_from_linked_model
        from validate import _check_bem_volume_conservation

        model = clean_model

        # Build BEM model
        sres = simplify_ring(
            footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
            tol=0.02,
            wall_height=model.levels[0].wall_height_m,
        )
        bem = model_from_linked_model(
            model=model,
            simplified_ring=sres.ring,
            wall_height_m=model.levels[0].wall_height_m,
            simplify_tolerance=0.5,
        )

        # Inject defect: set space volumes to large values
        for space in bem.spaces:
            space.volume_m3 = 999999.0

        result = _check_bem_volume_conservation(bem, tol_volume=0.1)
        assert result.severity == "error", f"Expected error, got {result.severity}"
        assert result.message, "bem_volume_conservation error should have a message"
        # Error should mention volume
        assert any(keyword in result.message.lower() for keyword in ["volume", "vol"]), (
            f"Error message should mention 'volume': {result.message}"
        )

    # ------------------------------------------------------------------
    # Severity must be error, not warn
    # ------------------------------------------------------------------

    def test_error_not_warn_severity(self, clean_model):
        """bem_volume_conservation emits 'error' severity, not 'warn'."""
        from geometry_simplify import footprint_from_regions, simplify_ring
        from run_pipeline import model_from_linked_model
        from validate import _check_bem_volume_conservation

        model = clean_model

        # Build BEM model
        sres = simplify_ring(
            footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
            tol=0.02,
            wall_height=model.levels[0].wall_height_m,
        )
        bem = model_from_linked_model(
            model=model,
            simplified_ring=sres.ring,
            wall_height_m=model.levels[0].wall_height_m,
            simplify_tolerance=0.5,
        )

        # Inject defect: set space volumes to large values
        for space in bem.spaces:
            space.volume_m3 = 999999.0

        result = _check_bem_volume_conservation(bem, tol_volume=0.1)
        assert result.severity == "error", (
            f"bem_volume_conservation should emit 'error', not '{result.severity}'"
        )
