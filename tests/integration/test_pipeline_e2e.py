"""Integration tests: full pipeline end-to-end for bldg_3room (issue #421).

Exercises the complete synthetic pipeline from building generation through
model linking, verifying that the output model has the expected structural
elements: levels, zones, spaces, and envelope walls.
"""

from __future__ import annotations

import pytest

from link import build_model
from synth.multidiscipline import generate_building


class TestPipelineE2E:
    """Full end-to-end pipeline tests using bldg_3room synthetic building."""

    @pytest.fixture
    def linked_model(self):
        """Generate and link a bldg_3room building model once per test."""
        bldg = generate_building(seed=42, open_office_span=False)
        model, report = build_model(
            bldg,
            elevation_key="elev_grid",
            building_name=bldg["building_id"],
        )
        return model, report, bldg

    def test_model_has_levels(self, linked_model):
        """Linked model contains at least one level."""
        model, _, _ = linked_model
        assert len(model.levels) >= 1, "Model should have at least one level"

    def test_model_has_spaces(self, linked_model):
        """Linked model contains spaces after pipeline processing."""
        model, _, _ = linked_model
        assert len(model.spaces) >= 1, "Model should have at least one space"

    def test_model_has_zones(self, linked_model):
        """Linked model contains zones after pipeline processing."""
        model, _, _ = linked_model
        assert len(model.zones) >= 1, "Model should have at least one zone"

    def test_model_has_envelope(self, linked_model):
        """Linked model contains envelope wall elements after pipeline processing."""
        model, _, _ = linked_model
        assert len(model.envelope) >= 1, "Model should have at least one envelope wall"

    def test_spaces_have_valid_polygons(self, linked_model):
        """All spaces in the linked model have valid polygon geometry."""
        model, _, _ = linked_model
        for space_id, space in model.spaces.items():
            assert space.polygon_m is not None, f"Space {space_id} should have polygon_m"
            assert len(space.polygon_m) >= 3, (
                f"Space {space_id} polygon should have at least 3 points"
            )

    def test_envelope_walls_have_area(self, linked_model):
        """All envelope walls in the linked model have valid area values."""
        model, _, _ = linked_model
        for wall in model.envelope:
            assert wall.area_m2 is not None, f"Wall {wall.id} should have area_m2"
            assert wall.area_m2 > 0, f"Wall {wall.id} area should be positive"

    def test_zones_reference_spaces(self, linked_model):
        """All zones in the linked model reference at least one space."""
        model, _, _ = linked_model
        for zone_id, zone in model.zones.items():
            assert len(zone.space_ids) >= 1, f"Zone {zone_id} should reference at least one space"

    def test_spaces_reference_level(self, linked_model):
        """All spaces in the linked model reference a valid level."""
        model, _, _ = linked_model
        level_ids = {level.id for level in model.levels}
        for space_id, space in model.spaces.items():
            assert space.level_id in level_ids, (
                f"Space {space_id} references level_id {space.level_id} "
                f"which is not in model levels"
            )
