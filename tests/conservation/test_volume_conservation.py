"""Integration tests for volume_conservation through the full BATTERY pipeline.

Dedicated integration test for the volume_conservation conservation law:
SUM(space volumes per level) ~= footprint x floor-to-floor height.

This test exercises the full validate.py run_checks() pipeline, ensuring
the conservation law integrates correctly with the rest of the BATTERY.

See issue #325.
"""

from __future__ import annotations

from tests.model_factory import break_volume_conservation, make_clean_model
from validate import run_checks


class TestVolumeConservationIntegration:
    """Integration tests for volume_conservation through full BATTERY."""

    def test_clean_model_passes(self):
        """Happy-path: clean model has SUM(space volumes) ~= footprint x height."""
        m = make_clean_model()
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "error"
        ]
        assert len(errors) == 0, f"volume_conservation error on clean model: {errors}"

    def test_broken_volume_triggers_error(self):
        """break_volume_conservation injection triggers volume_conservation error."""
        m = make_clean_model()
        break_volume_conservation(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected exactly one volume_conservation error after break_volume_conservation, got "
            f"{[r.severity for r in report.results if r.check_id == 'volume_conservation']}"
        )

    def test_error_message_includes_details(self):
        """volume_conservation error message includes expected vs actual volume details."""
        m = make_clean_model()
        break_volume_conservation(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1
        err = errors[0]
        assert err.message, "volume_conservation error should have a message"
        assert "volume" in err.message.lower() or "level" in err.message.lower(), (
            f"volume_conservation error message should mention 'volume' or 'level': {err.message}"
        )

    def test_error_not_warn_severity(self):
        """volume_conservation emits 'error' severity, not 'warn'."""
        m = make_clean_model()
        break_volume_conservation(m)
        report = run_checks(m)
        warns = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "warn"
        ]
        assert len(warns) == 0, f"volume_conservation should emit 'error', not 'warn': {warns}"
