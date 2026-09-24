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


class TestProvenanceCompleteAllCategories:
    """Assert every entity category's provenance field is non-None after a clean pipeline run."""

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_space_provenance_non_none(self, seed):
        """Space.core_provenance and Space.history are non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.core_provenance is not None, (
                f"seed={seed} space={sid}: core_provenance is None"
            )
            assert sp.history is not None, f"seed={seed} space={sid}: history is None"
            for h in sp.history:
                assert h.sheet_id, f"seed={seed} space={sid}: history entry missing sheet_id"
                assert h.method, f"seed={seed} space={sid}: history entry missing method"

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_opening_provenance_non_none(self, seed):
        """SpaceOpening.provenance is non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for o in sp.openings:
                assert o.provenance is not None, (
                    f"seed={seed} opening={o.id} space={sid}: provenance is None"
                )

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_lighting_provenance_non_none(self, seed):
        """SpaceLighting.provenance and fixture provenance are non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.lighting.provenance is not None, (
                f"seed={seed} space={sid}: lighting.provenance is None"
            )
            for f in sp.lighting.fixtures:
                assert f.provenance is not None, (
                    f"seed={seed} fixture={f.id} space={sid}: provenance is None"
                )

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_hvac_provenance_non_none(self, seed):
        """SpaceHVAC.provenance and ComponentRef provenance are non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.hvac.provenance is not None, (
                f"seed={seed} space={sid}: hvac.provenance is None"
            )
            for d in sp.hvac.diffusers:
                assert d.provenance is not None, (
                    f"seed={seed} diffuser={d.id} space={sid}: provenance is None"
                )
            for s in sp.hvac.sensors:
                assert s.provenance is not None, (
                    f"seed={seed} sensor={s.id} space={sid}: provenance is None"
                )
            for t in sp.hvac.terminal_units:
                assert t.provenance is not None, (
                    f"seed={seed} terminal={t.id} space={sid}: provenance is None"
                )

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_daylight_provenance_non_none(self, seed):
        """SpaceDaylight.provenance and DaylitZone provenance are non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.daylight.provenance is not None, (
                f"seed={seed} space={sid}: daylight.provenance is None"
            )
            for dz in sp.daylight.primary:
                assert dz.provenance is not None, (
                    f"seed={seed} primary_daylight_zone={dz.id} space={sid}: provenance is None"
                )
            for dz in sp.daylight.secondary:
                assert dz.provenance is not None, (
                    f"seed={seed} secondary_daylight_zone={dz.id} space={sid}: provenance is None"
                )

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_zone_provenance_non_none(self, seed):
        """Zone.provenance is non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for zid, z in model.zones.items():
            assert z.provenance is not None, f"seed={seed} zone={zid}: provenance is None"
            assert z.history is not None, f"seed={seed} zone={zid}: history is None"

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_all_envelope_provenance_non_none(self, seed):
        """EnvelopeWall.provenance is non-None after pipeline."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for w in model.envelope:
            assert w.provenance is not None, f"seed={seed} envelope_wall={w.id}: provenance is None"


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

    def test_low_confidence_fact_needs_review(self):
        """A fact with provenance but missing method/revision and confidence < 0.7 needs review."""
        from building_model import Provenance

        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        opening = next((o for sp in model.spaces.values() for o in sp.openings), None)
        assert opening is not None, "test requires a model with at least one opening"
        # Set provenance with missing method/revision and low confidence
        opening.provenance = Provenance(
            sheet_id="test_sheet",
            revision=0,  # missing proper revision
            method="",  # missing method
            confidence=0.5,  # below threshold
        )
        result = _provenance_check(model)
        # Since sheet_id is present, severity is pass, but needs_review should be True
        assert result.severity == "pass"
        assert opening.id in result.needs_review, (
            f"opening {opening.id} with low confidence should need review"
        )
        assert result.needs_review[opening.id] is True

    def test_high_confidence_insufficient_provenance_no_review(self):
        """A fact with provenance but missing method/revision and confidence >= 0.7 does not need review."""
        from building_model import Provenance

        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        opening = next((o for sp in model.spaces.values() for o in sp.openings), None)
        assert opening is not None, "test requires a model with at least one opening"
        # Set provenance with missing method/revision but high confidence
        opening.provenance = Provenance(
            sheet_id="test_sheet",
            revision=0,  # missing proper revision
            method="",  # missing method
            confidence=0.85,  # above threshold
        )
        result = _provenance_check(model)
        assert result.severity == "pass"
        assert opening.id not in result.needs_review or not result.needs_review.get(opening.id), (
            f"opening {opening.id} with high confidence should not need review"
        )

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
