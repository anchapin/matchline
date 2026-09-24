"""Tests for bem_export.py — BEM export from canonical building model."""

import tempfile
from pathlib import Path

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from building_model import Provenance, Space, SpaceOpening
from datasets_adapter import (
    DrawingScale,
    TakeoffLine,
    TakeoffResult,
)


def _make_scale():
    return DrawingScale(m_per_px=0.001, note="1:100")


def _make_space():
    return Space(
        id="L1-101",
        level_id="L1",
        polygon_m=[[0, 0], [10, 0], [10, 10], [0, 10]],
        name="Office",
        number="101",
    )


class TestTakeoffResult:
    def test_takeoff_result_default_fields(self):
        scale = _make_scale()
        result = TakeoffResult(drawing_type="floor_plan", scale=scale)
        assert result.drawing_type == "floor_plan"
        assert result.scale == scale
        assert result.regions == []
        assert result.area_px2 == {}
        assert result.area_m2 == {}


class TestTakeoffLine:
    def test_takeoff_line_required_fields(self):
        line = TakeoffLine(
            tag="A",
            category="window",
            count=2,
            width_m=1.2,
            height_m=1.5,
            area_m2=3.6,
        )
        assert line.tag == "A"
        assert line.category == "window"
        assert line.count == 2
        assert line.area_m2 == 3.6


class TestModelFromTakeoff:
    def test_model_from_takeoff_requires_scale(self):
        scale = _make_scale()
        result = TakeoffResult(drawing_type="floor_plan", scale=scale)
        assert result.scale is not None

    def test_model_from_takeoff_requires_spaces(self):
        space = _make_space()
        assert space.id == "L1-101"
        assert space.level_id == "L1"


class TestBEMExport:
    def test_bem_export_window_takeoff_line(self):
        line = TakeoffLine(
            tag="A",
            category="window",
            count=4,
            width_m=1.2,
            height_m=1.5,
            area_m2=7.2,
        )
        assert line.count == 4
        assert line.area_m2 == 7.2

    def test_write_gbxml_and_validate_gbxml_roundtrip(self):
        """BEMModel round-trips through write_gbxml and validate_gbxml.

        Acceptance criterion for issue #148: a BEMModel written via
        write_gbxml must pass validate_gbxml (True, []).
        """
        space = BEMSpace(
            sid="L1-101",
            name="Office 101",
            number="101",
            polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
            area_m2=100.0,
            volume_m3=300.0,
            lighting_w=0.0,
        )
        opening = BEMOpeningUnit(
            category="window",
            tag="A",
            width_m=1.2,
            height_m=1.5,
        )
        model = BEMModel(
            building_name="Test Building",
            spaces=[space],
            openings=[opening],
            ring_m=[(0, 0), (20, 0), (20, 20), (0, 20)],
            wall_height_m=3.0,
            area_delta_pct=0.0,
            simplify_tol_pct=0.1,
            skipped_openings=[],
            notes=[],
            zones=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "out.gbxml"
            written = write_gbxml(model, path)
            assert written == path
            ok, errors = validate_gbxml(path)
            assert ok is True, f"validate_gbxml failed: {errors}"
            assert errors == []


def test_provenance_carried_through_export_boundary():
    """Regression test: Provenance objects survive model_from_linked_model.

    GitHub issue #228: BEMModel and BEMOpeningUnit drop all provenance at export.
    """
    from run_pipeline import model_from_linked_model

    # Create a minimal building model with provenance
    space = Space(
        id="L1-101",
        level_id="L1",
        name="Office",
        number="101",
        polygon_m=[[0, 0], [10, 0], [10, 10], [0, 10]],
        area_m2=100.0,
        volume_m3=300.0,
        core_provenance=Provenance(
            sheet_id="arch_A101",
            revision=3,
            method="grid_registration",
            confidence=0.95,
            bbox=[100, 100, 500, 500],
        ),
        history=[
            Provenance(
                sheet_id="arch_A101",
                revision=2,
                method="geometric_fallback",
                confidence=0.80,
            ),
        ],
    )
    opening = SpaceOpening(
        id="L1-101-WIN-A",
        tag="A",
        category="window",
        width_m=2.0,
        height_m=1.5,
        provenance=Provenance(
            sheet_id="arch_A201",
            revision=1,
            method="elevation_extraction",
            confidence=0.90,
            bbox=[150, 200, 350, 400],
        ),
        history=[
            Provenance(
                sheet_id="arch_A201",
                revision=0,
                method="schedule_join",
                confidence=0.70,
            ),
        ],
    )
    space.openings.append(opening)

    class FakeBuildingModel:
        id = "TEST_BUILDING"
        name = "Test Building"

        class FakeLevel:
            wall_height_m = 3.0

        levels = [FakeLevel()]
        spaces = {"L1-101": space}

    bem_model = model_from_linked_model(
        model=FakeBuildingModel(),
        simplified_ring=[[0, 0], [10, 0], [10, 10], [0, 10]],
        wall_height_m=3.0,
        simplify_tol_pct=1.0,
    )

    # Verify BEMSpace provenance
    assert len(bem_model.spaces) == 1
    bem_space = bem_model.spaces[0]
    assert bem_space.provenance is not None, "BEMSpace.core_provenance was dropped"
    assert bem_space.provenance.sheet_id == "arch_A101"
    assert bem_space.provenance.revision == 3
    assert bem_space.provenance.method == "grid_registration"
    assert bem_space.provenance.confidence == 0.95
    assert len(bem_space.history) == 1, "BEMSpace.history was dropped"
    assert bem_space.history[0].sheet_id == "arch_A101"
    assert bem_space.history[0].revision == 2

    # Verify BEMOpeningUnit provenance
    assert len(bem_model.openings) == 1
    bem_opening = bem_model.openings[0]
    assert bem_opening.provenance is not None, "BEMOpeningUnit.provenance was dropped"
    assert bem_opening.provenance.sheet_id == "arch_A201"
    assert bem_opening.provenance.revision == 1
    assert bem_opening.provenance.method == "elevation_extraction"
    assert bem_opening.provenance.confidence == 0.90
    assert len(bem_opening.history) == 1, "BEMOpeningUnit.history was dropped"
    assert bem_opening.history[0].sheet_id == "arch_A201"
    assert bem_opening.history[0].revision == 0
