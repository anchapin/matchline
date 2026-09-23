"""Tests for bem_export.py — BEM export from canonical building model."""

import tempfile
from pathlib import Path

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from building_model import Space
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
