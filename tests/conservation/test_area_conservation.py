"""Integration tests for area_conservation through the full BATTERY pipeline.

Dedicated integration test for the area_conservation conservation law:
SUM(space areas per level) ~= footprint area.

This test exercises the full validate.py run_checks() pipeline, ensuring
the conservation law integrates correctly with the rest of the BATTERY.

See issue #325.
"""

from __future__ import annotations

from tests.model_factory import break_area, make_clean_model
from validate import run_checks


class TestAreaConservationIntegration:
    """Integration tests for area_conservation through full BATTERY."""

    def test_clean_model_passes(self):
        """Happy-path: clean model has SUM(space areas per level) ~= footprint."""
        m = make_clean_model()
        report = run_checks(m)
        errors = [
            r for r in report.results if r.check_id == "area_conservation" and r.severity == "error"
        ]
        assert len(errors) == 0, f"area_conservation error on clean model: {errors}"

    def test_broken_area_triggers_error(self):
        """break_area injection: space area mismatch triggers area_conservation error."""
        m = make_clean_model()
        break_area(m)
        report = run_checks(m)
        errors = [
            r for r in report.results if r.check_id == "area_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected exactly one area_conservation error after break_area, got "
            f"{[r.severity for r in report.results if r.check_id == 'area_conservation']}"
        )

    def test_error_message_includes_details(self):
        """area_conservation error message includes expected vs actual area details."""
        m = make_clean_model()
        break_area(m)
        report = run_checks(m)
        errors = [
            r for r in report.results if r.check_id == "area_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1
        err = errors[0]
        assert err.message, "area_conservation error should have a message"
        assert "area" in err.message.lower() or "level" in err.message.lower(), (
            f"area_conservation error message should mention 'area' or 'level': {err.message}"
        )

    def test_error_not_warn_severity(self):
        """area_conservation emits 'error' severity, not 'warn'."""
        m = make_clean_model()
        break_area(m)
        report = run_checks(m)
        warns = [
            r for r in report.results if r.check_id == "area_conservation" and r.severity == "warn"
        ]
        assert len(warns) == 0, f"area_conservation should emit 'error', not 'warn': {warns}"
