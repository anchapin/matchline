"""Tests for bem_export.py — BEM model assembly and gbXML export."""


import pytest

from bem_export import (
    BEMModel,
    BEMOpeningUnit,
    BEMSpace,
    model_from_takeoff,
    validate_gbxml,
    write_gbxml,
)


def _make_takeoff():
    from datasets_adapter import DrawingScale, TakeoffLine, TakeoffResult

    return TakeoffResult(
        lines=[
            TakeoffLine(tag="A", category="window", count=4, width_m=1.2, height_m=1.5),
            TakeoffLine(tag="B", category="door", count=2, width_m=0.9, height_m=2.1),
        ],
        scale=DrawingScale(m_per_px=0.001),
        total_window_m2=0.0,
        total_door_m2=0.0,
        unmatched=[],
        notes=[],
    )


def _make_labeled():
    from room_labels import LabeledSpace, LabeledTakeoff

    return LabeledTakeoff(
        spaces=[
            LabeledSpace(polygon_px=[(100, 100), (300, 100), (300, 300), (100, 300)], name="Office", number="101"),
        ],
        labels=[],
        unmatched_labels=[],
        n_labeled=1,
        n_total=1,
    )


def _make_sres():
    from geometry_simplify import SimplifyResult

    return SimplifyResult(
        ring=[(100, 100), (300, 100), (300, 300), (100, 300)],
        original_count=4,
        area_delta_pct=0.1,
        tol=0.02,
    )


class TestBEMSpace:
    def test_bem_space_creation(self):
        sp = BEMSpace(
            sid="sp-001",
            name="Open Office",
            number="101",
            polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
            area_m2=100.0,
            volume_m3=300.0,
        )
        assert sp.sid == "sp-001"
        assert sp.area_m2 == 100.0


class TestBEMOpeningUnit:
    def test_bem_opening_unit(self):
        u = BEMOpeningUnit(category="window", tag="A", width_m=1.2, height_m=1.5)
        assert u.category == "window"
        assert u.tag == "A"


class TestModelFromTakeoff:
    def test_model_from_takeoff_requires_scale(self):
        from datasets_adapter import DrawingScale, TakeoffResult

        takeoff = TakeoffResult(lines=[], scale=DrawingScale(m_per_px=None), total_window_m2=0, total_door_m2=0, unmatched=[], notes=[])
        with pytest.raises(ValueError, match="scale"):
            model_from_takeoff(takeoff, _make_labeled(), _make_sres())

    def test_model_from_takeoff_requires_spaces(self):
        from room_labels import LabeledTakeoff

        labeled = LabeledTakeoff(spaces=[], labels=[], unmatched_labels=[], n_labeled=0, n_total=0)
        with pytest.raises(ValueError, match="no labeled spaces"):
            model_from_takeoff(_make_takeoff(), labeled, _make_sres())


class TestWriteGbxml:
    def test_write_gbxml_returns_path(self, tmp_path):
        model = BEMModel(
            building_name="Test Bldg",
            spaces=[
                BEMSpace(
                    sid="sp-001",
                    name="Office",
                    number="101",
                    polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
                    area_m2=100.0,
                    volume_m3=300.0,
                )
            ],
            openings=[],
            ring_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
            wall_height_m=3.0,
            area_delta_pct=0.1,
            simplify_tol_pct=2.0,
        )
        out = tmp_path / "out.xml"
        result = write_gbxml(model, out)
        assert result == out
        assert out.exists()


class TestValidateGbxml:
    def test_validate_smoke_check_on_valid(self, tmp_path):
        """A minimal well-formed gbXML passes validate_gbxml."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<gbXML temperatureUnit="C" lengthUnit="Meters" areaUnit="SquareMeters"
  volumeUnit="CubicMeters" version="6.01" useSIUnitsForResults="true"
  xmlns="http://www.gbxml.org/schema">
  <Campus id="campus-1">
    <Building id="bldg-1" buildingType="Office"/>
  </Campus>
</gbXML>"""
        p = tmp_path / "valid.xml"
        p.write_text(xml_content)
        ok, errors = validate_gbxml(p)
        assert ok is True
        assert len(errors) == 0

    def test_validate_smoke_check_on_malformed(self, tmp_path):
        """A malformed XML file is rejected."""
        p = tmp_path / "malformed.xml"
        p.write_text("<root><unclosed>")
        ok, errors = validate_gbxml(p)
        assert ok is False
        assert len(errors) > 0
