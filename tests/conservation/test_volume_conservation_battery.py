"""Integration tests for volume_conservation through the full BATTERY pipeline.

Dedicated integration test for the volume_conservation conservation law:
  https://github.com/asymptotic/isssueboards/issues/325

Uses the full BATTERY pipeline: generate synthetic building via synth.*, run through
run_checks(), verify the conservation check passes, inject defect, verify check fails.
"""

from __future__ import annotations

import pytest

from tests.model_factory import break_volume_conservation, make_clean_model
from validate import run_checks


class TestVolumeConservationBattery:
    """volume_conservation through full BATTERY.

    The check lives in validate._check_volume_conservation and is
    registered in the BATTERY list so it is reached via run_checks().
    """

    @pytest.fixture
    def clean_model(self):
        """Build a clean (undefective) office-building model."""
        return make_clean_model()

    # ------------------------------------------------------------------
    # Happy path: clean model passes volume_conservation
    # ------------------------------------------------------------------

    def test_clean_model_passes(self, clean_model):
        """Clean model: volume_conservation check passes with no errors."""
        m = clean_model
        report = run_checks(m)

        # Filter to volume_conservation errors only
        errors = [r for r in report.results if r.check_id == "volume_conservation" and r.severity == "error"]
        assert len(errors) == 0, f"volume_conservation error on clean model: {errors}"

    # ------------------------------------------------------------------
    # Defect injection: break_volume_conservation triggers an error
    # ------------------------------------------------------------------

    def test_broken_volume_triggers_error(self, clean_model):
        """break_volume_conservation injection triggers volume_conservation error."""
        m = clean_model
        break_volume_conservation(m)

        report = run_checks(m)

        bad = [r for r in report.results if r.check_id == "volume_conservation" and r.severity == "error"]
        assert len(bad) == 1, (
            f"Expected exactly one volume_conservation error after break_volume_conservation, got "
            f"{[r.severity for r in report.results if r.check_id == 'volume_conservation']}"
        )

    # ------------------------------------------------------------------
    # Error message quality
    # ------------------------------------------------------------------

    def test_error_message_includes_details(self, clean_model):
        """volume_conservation error message includes expected vs actual volume details."""
        m = clean_model
        break_volume_conservation(m)

        report = run_checks(m)

        err = next(
            (r for r in report.results if r.check_id == "volume_conservation" and r.severity == "error"),
            None,
        )
        assert err is not None, "Expected a volume_conservation error"
        assert err.message, "volume_conservation error should have a message"
        # Sanitised check – the message should mention volume or level (case-insensitive)
        assert any(keyword in err.message.lower() for keyword in ["volume", "level"]), (
            f"volume_conservation error message should mention 'volume' or 'level': {err.message}"
        )

    # ------------------------------------------------------------------
    # Severity must be error, not warn
    # ------------------------------------------------------------------

    def test_error_not_warn_severity(self, clean_model):
        """volume_conservation emits 'error' severity, not 'warn'."""
        m = clean_model
        break_volume_conservation(m)

        report = run_checks(m)

        warns = [r for r in report.results if r.check_id == "volume_conservation" and r.severity == "warn"]
        assert len(warns) == 0, f"volume_conservation should emit 'error', not 'warn': {warns}"
