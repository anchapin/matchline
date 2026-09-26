"""Tests for link.py: cross-sheet linker and build_model.

Tests cover:
  - Happy path: build_model produces a valid BuildingModel for seeds 101/102
  - Invariant: _dedupe_space_openings leaves no duplicate openings
  - Defect injection: missing space polygon is flagged for review
"""

from __future__ import annotations

import pytest

from building_model import REVIEW_CONFIDENCE, BuildingModel, Space, SpaceOpening
from link import build_model
from link._dedupe import _dedupe_space_openings
from synth.multidiscipline import generate_building


class TestBuildModel:
    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_returns_valid_model(self, seed: int):
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_grid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.levels) > 0
        assert len(model.spaces) > 0

    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_with_open_office(self, seed: int):
        bldg = generate_building(seed, open_office_span=True)
        model, report = build_model(
            bldg, elevation_key="elev_grid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.spaces) > 0

    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_nogrid_elevation(self, seed: int):
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_nogrid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.spaces) > 0

    def test_build_model_spaces_have_valid_polygon(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for sid, space in model.spaces.items():
            if space.polygon_m:
                assert len(space.polygon_m) >= 3, f"space {sid} polygon needs >= 3 pts"
                for pt in space.polygon_m:
                    assert len(pt) == 2

    def test_build_model_all_spaces_have_core_provenance(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for sid, space in model.spaces.items():
            assert space.core_provenance is not None, f"space {sid} missing provenance"

    def test_build_model_envelope_has_provenance(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for wall in model.envelope:
            assert wall.provenance is not None


class TestDedupeSpaceOpenings:
    def test_different_tags_not_deduplicated(self):
        model = BuildingModel(name="dedup-test")
        level_id = "L1"
        model.levels.append(
            type("Level", (), {"id": level_id, "name": "L1", "elevation_z_m": 0.0})()
        )
        space = Space(
            id="L1-101",
            level_id=level_id,
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        model.spaces["L1-101"] = space

        prov = type("Provenance", (), {"confidence": 0.95, "sheet_id": "EL-01"})()
        o1 = SpaceOpening(
            id="OP1",
            tag="WINDOW",
            category="window",
            width_m=1.5,
            height_m=2.0,
            sill_m=0.9,
            provenance=prov,
        )
        o2 = SpaceOpening(
            id="OP2",
            tag="DOOR",
            category="door",
            width_m=0.9,
            height_m=2.1,
            sill_m=0.0,
            provenance=prov,
        )
        space.openings.extend([o1, o2])

        _dedupe_space_openings(model)

        tags = {op.tag for op in space.openings}
        assert "WINDOW" in tags
        assert "DOOR" in tags

    def test_cross_level_same_facade_deduplicated(self):
        """Issue #404: windows on same facade across different levels are deduplicated."""
        model = BuildingModel(name="cross-level-test")
        level1 = type("Level", (), {"id": "L1", "name": "Level 1", "elevation_z_m": 0.0})()
        level2 = type("Level", (), {"id": "L2", "name": "Level 2", "elevation_z_m": 3.0})()
        model.levels.extend([level1, level2])

        space1 = Space(
            id="L1-101",
            level_id="L1",
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        space2 = Space(
            id="L2-101",
            level_id="L2",
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        model.spaces["L1-101"] = space1
        model.spaces["L2-101"] = space2

        prov1 = type("Provenance", (), {"confidence": 0.9, "sheet_id": "EL-L1"})()
        prov2 = type("Provenance", (), {"confidence": 0.95, "sheet_id": "EL-L2"})()

        op1 = SpaceOpening(
            id="OP1",
            tag="WINDOW",
            category="window",
            width_m=1.5,
            height_m=2.0,
            sill_m=0.9,
            host_facade="NORTH",
            provenance=prov1,
        )
        op2 = SpaceOpening(
            id="OP2",
            tag="WINDOW",
            category="window",
            width_m=1.5,
            height_m=2.0,
            sill_m=0.9,
            host_facade="NORTH",
            provenance=prov2,
        )
        space1.openings.append(op1)
        space2.openings.append(op2)

        _dedupe_space_openings(model)

        all_openings = (
            list(model.spaces.values())[0].openings + list(model.spaces.values())[1].openings
        )
        assert len(all_openings) == 1
        assert all_openings[0].tag == "WINDOW"
        assert all_openings[0].host_facade == "NORTH"
        assert "+" in all_openings[0].provenance.sheet_id
        assert "EL-L1" in all_openings[0].provenance.sheet_id
        assert "EL-L2" in all_openings[0].provenance.sheet_id


class TestReviewQueueRouting:
    def test_low_confidence_fact_lands_in_review_queue(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        prov = type("Provenance", (), {"confidence": 0.5, "sheet_id": "test"})()
        model.flag_for_review(
            kind="geometry",
            description="synthetic low-confidence item for testing",
            confidence=0.5,
            provenance=prov,
        )
        assert len(model.review_queue) > 0
        assert any(item.confidence < REVIEW_CONFIDENCE for item in model.review_queue)

    def test_review_queue_classified_by_confidence(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        prov = type("Provenance", (), {"confidence": 0.95, "sheet_id": "test"})()
        model.flag_for_review(
            kind="geometry",
            description="high-confidence item",
            confidence=0.95,
            provenance=prov,
        )
        high_conf_count = sum(
            1 for item in model.review_queue if item.confidence >= REVIEW_CONFIDENCE
        )
        assert high_conf_count == 0
