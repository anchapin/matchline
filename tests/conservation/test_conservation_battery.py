"""Conservation law integration tests — full BATTERY pipeline.

Tests that exercise conservation checks through run_checks() rather than
calling check functions directly, ensuring the full validate.py BATTERY
pipeline is exercised end-to-end.

See issue #305.
"""

from __future__ import annotations

from tests.model_factory import (
    break_area,
    break_opening_oversize,
    break_volume_conservation,
    make_clean_model,
)
from validate import run_checks


class TestSpaceAreaMatchesPolygonBattery:
    """space_area_matches_polygon conservation through full BATTERY."""

    def test_clean_model_no_warnings(self):
        """Happy-path: clean model produces no space_area_matches_polygon warnings."""
        m = make_clean_model()
        report = run_checks(m)
        bad = [
            r
            for r in report.results
            if r.check_id == "space_area_matches_polygon" and r.severity == "warn"
        ]
        assert len(bad) == 0, (
            f"space_area_matches_polygon warn on clean model: {bad}"
        )

    def test_area_mismatch_fires_warning(self):
        """Mismatched space area triggers space_area_matches_polygon warning."""
        m = make_clean_model()
        first_space = next(iter(m.spaces.values()))
        first_space.area_m2 = 999.0
        report = run_checks(m)
        matching = [
            r
            for r in report.results
            if r.check_id == "space_area_matches_polygon" and r.severity == "warn"
        ]
        assert len(matching) == 1, (
            f"Expected space_area_matches_polygon warn, got "
            f"{[r.severity for r in report.results if r.check_id == 'space_area_matches_polygon']}"
        )


class TestAreaConservationBattery:
    """area_conservation through full BATTERY.

    Conservation law: SUM(space areas per level) ~= footprint area.
    """

    def test_clean_model_passes(self):
        """Clean model: area_conservation check passes with no errors."""
        m = make_clean_model()
        report = run_checks(m)
        bad = [
            r
            for r in report.results
            if r.check_id == "area_conservation" and r.severity == "error"
        ]
        assert len(bad) == 0, f"area_conservation error on clean model: {bad}"

    def test_broken_area_fires_error(self):
        """Defect injection: breaking space area triggers area_conservation error."""
        m = make_clean_model()
        break_area(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "area_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected area_conservation error after break_area, got "
            f"{[r.severity for r in report.results if r.check_id == 'area_conservation']}"
        )


class TestVolumeConservationBattery:
    """volume_conservation through full BATTERY.

    Conservation law: SUM(space volumes per level) ~= footprint x floor height.
    """

    def test_clean_model_passes(self):
        """Clean model: volume_conservation check passes with no errors."""
        m = make_clean_model()
        report = run_checks(m)
        bad = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "error"
        ]
        assert len(bad) == 0, f"volume_conservation error on clean model: {bad}"

    def test_broken_volume_fires_error(self):
        """Defect injection: breaking volume conservation triggers error."""
        m = make_clean_model()
        break_volume_conservation(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "volume_conservation" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected volume_conservation error, got "
            f"{[r.severity for r in report.results if r.check_id == 'volume_conservation']}"
        )


class TestFacadeOpeningClosureBattery:
    """facade_opening_closure through full BATTERY.

    Conservation law: facade openings are properly closed by other elements.
    """

    def test_clean_model_passes(self):
        """Clean model: facade_opening_closure check passes with no errors."""
        m = make_clean_model()
        report = run_checks(m)
        bad = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "error"
        ]
        assert len(bad) == 0, f"facade_opening_closure error on clean model: {bad}"

    def test_oversized_opening_fires_error(self):
        """Defect injection: oversized opening triggers facade_opening_closure error."""
        m = make_clean_model()
        break_opening_oversize(m)
        report = run_checks(m)
        errors = [
            r
            for r in report.results
            if r.check_id == "facade_opening_closure" and r.severity == "error"
        ]
        assert len(errors) == 1, (
            f"Expected facade_opening_closure error, got "
            f"{[r.severity for r in report.results if r.check_id == 'facade_opening_closure']}"
        )
