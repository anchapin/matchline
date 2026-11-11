"""Integration tests for facade_opening_closure through the full BATTERY pipeline.

Dedicated integration test for the facade_opening_closure conservation law:
facade openings are properly closed by other elements.

This test exercises the full validate.py run_checks() pipeline, ensuring
the conservation law integrates correctly with the rest of the BATTERY.

See issue #325.
"""

from __future__ import annotations

import pytest

from tests.model_factory import break_opening_oversize, make_clean_model
from validate import run_checks


class TestFacadeOpeningClosureIntegration:
    """Integration tests for facade_opening_closure through full BATTERY."""

    def test_clean_model_passes(self):
        """Happy-path: clean model has all facade openings properly closed."""
        m = make_clean_model()
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "error"
        ]
        assert len(errors) == 0, f"facade_opening_closure error on clean model: {errors}"

    def test_oversized_opening_triggers_error(self):
        """break_opening_oversize injection triggers facade_opening_closure error."""
        m = make_clean_model()
        break_opening_oversize(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected exactly one facade_opening_closure error after break_opening_oversize, got "
            f"{[r.severity for r in report.results if r.check_id == 'facade_opening_closure']}"
        )

    def test_error_message_includes_details(self):
        """facade_opening_closure error message includes opening details."""
        m = make_clean_model()
        break_opening_oversize(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "error"
        ]
        assert len(errors) == 1
        err = errors[0]
        assert err.message, "facade_opening_closure error should have a message"
        assert "opening" in err.message.lower() or "facade" in err.message.lower(), (
            "facade_opening_closure error message should mention 'opening' or 'facade': "
            f"{err.message}"
        )

    def test_error_not_warn_severity(self):
        """facade_opening_closure emits 'error' severity, not 'warn'."""
        m = make_clean_model()
        break_opening_oversize(m)
        report = run_checks(m)
        warns = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "warn"
        ]
        assert len(warns) == 0, f"facade_opening_closure should emit 'error', not 'warn': {warns}"


class TestFacadeOpeningClosureWithDoors:
    """#1324: doors linked from elevations count as facade openings."""

    def test_door_opening_is_counted_and_closure_passes(self):
        from link import build_model
        from tests.test_link import _door_building

        bldg = _door_building("south")
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        doors = [op for sp in model.spaces.values() for op in sp.openings if op.category == "door"]
        assert len(doors) == 1
        door_area = doors[0].area_m2 or 0.0
        assert door_area == pytest.approx(0.9 * 2.1, abs=1e-6)

        report = run_checks(model)
        res = {r.check_id: r for r in report.results}
        closure = res["facade_opening_closure"]
        assert closure.severity == "pass", closure.message
        # gross wall area minus the openings (window AND door) stays opaque
        gross = sum(w.area_m2 or 0.0 for w in model.envelope if w.facade == "south")
        window_area = sum(
            op.area_m2 or 0.0
            for sp in model.spaces.values()
            for op in sp.openings
            if op.category == "window" and op.host_facade == "south"
        )
        assert f"{(gross - door_area - window_area):.2f}" in closure.message
