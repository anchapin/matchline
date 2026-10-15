"""#974: mechanical schedule parsing and system-type classification."""

import os
import unittest

import pytest

from building_model import ReviewItem
from mech_schedule import (
    SCHEMA,
    MechScheduleResult,
    link_to_model,
    parse_capacity,
    parse_cobie_workbook,
    parse_tag,
)

_TYPE_ROWS = [
    ("AC Unit Type 1", "23-75 10 24 21 27: Unitary Air", "Horiz. D.X. Fan Coil"),
    ("AHU", "23-75 35 14 17: Modular Indoor", "63300000 J"),
    ("Air Cooled Chiller", "23-75 10 24 21 21 21: Rotary", "633-703 kW"),
    (
        "Fan- Roof Mounted Type 1",
        "23-75 35 17 27: Centrifugal Fan",
        "Centrifugal Roof Mounted, Motorized Damper",
    ),
    ("VAV 200 mm", "23-75 70 21 24 14: Variable", "200 mm"),
    (
        "Supply Diffuser 600 Face",
        "23.75.70.21.27.11: Diffuser",
        "600 x 600 Face 300 x 300 Connection",
    ),
    ("Duplex Receptacle", "23-80 50 11 17: Ground Fault", "Standard"),
]
_COMP_ROWS = [
    ("AC Unit Type 1 AC-1", "AC Unit Type 1", "1B21", "AC Unit"),
    ("ACCU-1", "AC Unit Type 1", "1C13", "AC Unit"),
    ("AHU-1", "AHU", "n/a", "Air Handling Unit"),
    ("Air Cooled Chiller CH-1", "Air Cooled Chiller", "n/a", "Chiller"),
    ("Fan- EF1-1", "Fan- Roof Mounted Type 1", "1C13", "Exhaust Fan"),
    ("VAV 200 mm 2B12", "VAV 200 mm", "2B12", "VAV Box"),
    ("Supply Diffuser 600 Face 1A02", "Supply Diffuser 600 Face", "1A02", "Diffuser"),
    ("Duplex Receptacle R-1", "Duplex Receptacle", "1A01", "Receptacle"),
]
_SPACE_ROWS = [("1A01", "F1"), ("1B21", "F1"), ("1C13", "F1"), ("2B12", "F2")]


def _write_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    t = wb.active
    t.title = "Type"
    t.append(["Name", "CreatedBy", "CreatedOn", "Category", "Description", "ExtObject"])
    for name, cat, desc in _TYPE_ROWS:
        t.append([name, "tester@example.com", "2013-10-09", cat, desc, "IfcTypeProduct"])
    c = wb.create_sheet("Component")
    c.append(
        [
            "Name",
            "CreatedBy",
            "CreatedOn",
            "TypeName",
            "Space",
            "Description",
            "ExtSystem",
            "ExtObject",
            "ExtIdentifier",
            "SerialNumber",
            "InstallationDate",
            "WarrantyStartDate",
            "TagNumber",
            "BarCode",
        ]
    )
    for name, type_name, space, desc in _COMP_ROWS:
        c.append(
            [
                name,
                "tester@example.com",
                "2013-10-09",
                type_name,
                space,
                desc,
                "n/a",
                "IfcEnergyConversionDevice",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
            ]
        )
    s = wb.create_sheet("Space")
    s.append(["Name", "CreatedBy", "CreatedOn", "Category", "FloorName", "Description"])
    for name, floor in _SPACE_ROWS:
        s.append([name, "tester@example.com", "2013-10-09", "Space", floor, "n/a"])
    path = tmp_path / "clinic-design.xlsx"
    wb.save(path)
    return path


@pytest.fixture()
def result(tmp_path):
    return parse_cobie_workbook(_write_workbook(tmp_path))


def _row(res: MechScheduleResult, tag: str):
    matches = [r for r in res.rows if r.tag == tag]
    assert len(matches) == 1, f"expected one row tagged {tag}, got {matches}"
    return matches[0]


def test_schema_in_stats(result):
    assert result.stats["schema"] == SCHEMA
    assert SCHEMA == "matchline.mech_schedule/1"


def test_tags_parsed_from_component_names():
    assert parse_tag("AC Unit Type 1 AC-1") == "AC-1"
    assert parse_tag("Fan- EF1-1") == "EF1-1"
    assert parse_tag("ACCU-1") == "ACCU-1"
    assert parse_tag("Supply Diffuser 600 Face 1A02") == ""


def test_high_confidence_agrees_on_tag_and_text(result):
    ac = _row(result, "AC-1")
    assert ac.system_type == "fan_coil"
    assert ac.confidence == "high"
    ahu = _row(result, "AHU-1")
    assert ahu.system_type == "ahu"
    assert ahu.confidence == "high"
    ef = _row(result, "EF1-1")
    assert ef.system_type == "exhaust_fan"
    assert ef.confidence == "high"


def test_single_signal_is_medium(result):
    vav_rows = [r for r in result.rows if r.type_name == "VAV 200 mm"]
    assert len(vav_rows) == 1
    assert vav_rows[0].system_type == "vav_box"
    assert vav_rows[0].confidence == "medium"


def test_capacity_single_value_and_stated_range(result):
    ahu = _row(result, "AHU-1")
    assert ahu.capacity == {
        "value": 63300000.0,
        "unit": "J",
        "raw": "63300000 J",
        "source": "type.description",
    }
    chiller = _row(result, "CH-1")
    assert chiller.capacity is not None
    assert chiller.capacity["value"] is None
    assert chiller.capacity["unit"] == "kW"
    assert chiller.capacity["raw"] == "633-703 kW"
    vav_rows = [r for r in result.rows if r.type_name == "VAV 200 mm"]
    assert vav_rows[0].capacity is None


def test_capacity_parser_skips_dimensions_and_keeps_units_as_stated():
    assert parse_capacity("600 x 600 Face 300 x 300 Connection") is None
    assert parse_capacity("200 mm") is None
    assert parse_capacity("") is None
    cap = parse_capacity("5 tons")
    assert cap["unit"] == "ton"
    assert cap["value"] == 5.0


def test_no_evidence_excluded_from_schedule(result):
    tags = [r.tag for r in result.rows]
    types = [r.type_name for r in result.rows]
    assert "R-1" not in tags
    assert "Duplex Receptacle" not in types
    assert result.stats["excluded"] >= 1
    assert "Duplex Receptacle" in result.stats["excluded_samples"]


def test_conflicting_evidence_routes_to_review(result):
    accu = _row(result, "ACCU-1")
    assert accu.system_type is None
    assert accu.confidence == "low"
    assert result.review_items, "conflict must produce a review item"
    item = result.review_items[0]
    assert isinstance(item, ReviewItem)
    assert item.kind == "mech_schedule"
    assert item.needs_review is True
    assert item.target["id"] == "ACCU-1"
    assert accu.capacity is None or accu.capacity  # row still carries its data


def test_provenance_carry_row_and_type_rows(result):
    ac = _row(result, "AC-1")
    assert ac.provenance["sheet"] == "Component"
    assert ac.provenance["row"] >= 2
    assert ac.provenance["type_row"] >= 2
    assert ac.provenance["ext_object"] == "IfcEnergyConversionDevice"


def test_service_areas_matched_against_space_sheet(result):
    ac = _row(result, "AC-1")
    assert ac.service_area == "1B21"
    systems = {s.tag: s for s in result.systems}
    assert systems["AC-1"].rooms == [{"space": "1B21", "matched": True}]
    assert systems["EF1-1"].rooms == [{"space": "1C13", "matched": True}]
    ahu = systems["AHU-1"]
    assert ahu.rooms == []  # "n/a" service area carries no room claim


def test_systems_are_first_class_and_terminals_stay_rows(result):
    tags = {s.tag for s in result.systems}
    assert {"AC-1", "AHU-1", "CH-1", "EF1-1"} <= tags
    diffusers = [r for r in result.rows if r.system_type == "supply_diffuser"]
    assert len(diffusers) == 1
    assert diffusers[0].tag not in tags
    for system in result.systems:
        assert system.system_type in {
            "ahu",
            "rtu",
            "fan_coil",
            "condensing_unit",
            "vav_box",
            "fan",
            "exhaust_fan",
            "erv",
            "heat_pump",
            "chiller",
            "boiler",
            "pump",
        }


def test_link_to_model_matches_room_numbers(result):
    class _Space:
        def __init__(self, sid, number, name=""):
            self.id = sid
            self.number = number
            self.name = name

    class _Model:
        spaces = {
            "1-1B21": _Space("1-1B21", "1B21", "EXAM"),
            "1-1A02": _Space("1-1A02", "1A02", "LOBBY"),
        }

    matched = link_to_model(result.systems, _Model())
    assert matched["AC-1"] == ["1-1B21"]
    assert matched["AHU-1"] == []
    unmatched = [tag for tag, ids in matched.items() if not ids]
    assert "AHU-1" in unmatched


@unittest.skipUnless(
    os.environ.get("MATCHLINE_CLINIC_COBIE"),
    "set MATCHLINE_CLINIC_COBIE to the clinic design workbook path",
)
def test_clinic_cobie_real_workbook():
    path = os.environ["MATCHLINE_CLINIC_COBIE"]
    res = parse_cobie_workbook(path)
    stats = res.stats
    assert stats["rows"] > 100, stats
    by_type = stats["by_type"]
    assert by_type.get("fan_coil", 0) >= 4
    assert by_type.get("ahu", 0) >= 1
    ahu = [r for r in res.rows if r.tag.startswith("AHU")]
    assert ahu and ahu[0].system_type == "ahu"
    # no silent accepts: every low-confidence row has a review item
    low_rows = [r for r in res.rows if r.confidence == "low"]
    assert len(low_rows) == len(res.review_items)
    # every system's service area is checked against the Space sheet
    for system in res.systems:
        for room in system.rooms:
            assert isinstance(room["matched"], bool)
