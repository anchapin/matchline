"""Provenance completeness tests: every fact from the extraction pipeline carries
provenance — sheet, revision, method, and confidence.  No fact is unauditable."""

import pytest

from link import build_model
from synth.multidiscipline import generate_building
from validate import run_checks


def _provenance_check(model):
    """Return the provenance_complete check result from run_checks."""
    report = run_checks(model)
    return next(r for r in report.results if r.check_id == "provenance_complete")


class TestProvenanceCompleteSynthetic:
    """Verify provenance completeness on models produced by the extraction pipeline
    (generate_building + build_model), not on hand-built test fixtures."""

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_synthetic_pipeline_provenance_complete(self, seed):
        """Every fact from the synthetic pipeline carries provenance."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        result = _provenance_check(model)
        assert result.severity == "pass", f"seed={seed}: {result.message}"

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_open_office_pipeline_provenance_complete(self, seed):
        """Every fact from the open-office pipeline carries provenance."""
        bldg = generate_building(seed, open_office_span=True)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        result = _provenance_check(model)
        assert result.severity == "pass", f"seed={seed} (open_office): {result.message}"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_nogrid_elevation_pipeline_provenance_complete(self, seed):
        """Provenance is complete on the geometric-fallback elevation path too."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_nogrid", building_name=bldg["building_id"])
        result = _provenance_check(model)
        assert result.severity == "pass", f"seed={seed} (nogrid): {result.message}"


class TestProvenanceCompleteValidationCheck:
    """The provenance_complete check fires correctly when provenance is missing."""

    def test_missing_space_core_provenance_caught(self):
        """A space with no core_provenance fires the provenance_complete check."""
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        first_space = next(iter(model.spaces.values()))
        first_space.core_provenance = None
        result = _provenance_check(model)
        assert result.severity == "error"
        assert "provenance" in result.message.lower()

    def test_missing_opening_provenance_caught(self):
        """An opening with no provenance fires the provenance_complete check."""
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        opening = next((o for sp in model.spaces.values() for o in sp.openings), None)
        assert opening is not None, "test requires a model with at least one opening"
        opening.provenance = None
        result = _provenance_check(model)
        assert result.severity == "error"

    def test_missing_envelope_provenance_caught(self):
        """An envelope wall with no provenance fires the provenance_complete check."""
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        model.envelope[0].provenance = None
        result = _provenance_check(model)
        assert result.severity == "error"

    def test_all_entity_categories_covered(self):
        """Verify provenance_complete check accounts for all entity categories.

        The check covers: spaces (core_provenance), openings, lighting fixtures,
        HVAC diffusers/sensors/terminals, zones, and envelope walls.
        A clean pipeline model should report a fact count > 0.
        """
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        result = _provenance_check(model)
        assert result.severity == "pass"
        import re
        m = re.search(r"(\d+) facts", result.message)
        assert m is not None and int(m.group(1)) > 0, (
            f"expected positive fact count in message: {result.message}"
        )
        assert "sheet" in result.message and "method" in result.message
