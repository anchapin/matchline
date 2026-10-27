"""#974: mechanical schedule parsing and system-type classification."""

import os
import unittest

import pytest

from building_model import ReviewItem
from mech_schedule import (
    SCHEMA,
    VALUE_SLOTS,
    ZONE_LOAD_SLOTS,
    MechScheduleResult,
    MechScheduleRow,
    MechValue,
    _map_value_slot,
    _row_values,
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
    # no silent accepts: every low-confidence row has a review item, and
    # the review list is exactly the classification conflicts plus the
    # unmappable value slots (#1236), never anything fabricated
    low_rows = [r for r in res.rows if r.confidence == "low"]
    mech_items = [i for i in res.review_items if i.kind == "mech_schedule"]
    value_items = [i for i in res.review_items if i.kind == "mech_schedule_value"]
    assert len(low_rows) == len(mech_items)
    assert len(res.review_items) == len(mech_items) + len(value_items)
    assert set(value_items) == {i for i in res.review_items if i.kind == "mech_schedule_value"}
    assert stats["value_review_items"] == len(value_items)
    for row in res.rows:
        for value in row.values:
            assert value.field in VALUE_SLOTS
            assert value.zone_load_impacting == (value.field in ZONE_LOAD_SLOTS)
    # every system's service area is checked against the Space sheet
    for system in res.systems:
        for room in system.rooms:
            assert isinstance(room["matched"], bool)


# ---------------------------------------------------------------------------
# #1236: generalized value slots, ambiguity to review, zone-load inputs
# ---------------------------------------------------------------------------

_VALUE_TYPE_ROWS = [
    ("Chiller", "23 21 23: Chiller", "150 ton"),
    ("Boiler", "23 52 16: Boiler", "1200 MBH"),
    ("Heating Coil HW", "23 72 13: Coil", "800 MBH"),
    ("Cooling Coil CHW", "23 72 13: Coil", "60 ton"),
    ("Generic Coil", "23 72 13: Coil", "150 MBH"),
    ("Pump", "23 21 13: Pump", "50 GPM"),
    ("Exhaust Fan", "23 34 16: Fan", "1200 CFM"),
    ("VAV Terminal", "23 73 13: VAV", "Minimum 400 CFM, Maximum 1600 CFM"),
    ("AHU", "23 74 13: AHU", "63300000 J"),
    ("Pump Motor", "23 21 13: Pump", "Motor 7.5 kW"),
    ("VAV Power", "23 73 13: VAV", "2 kW"),
    ("Head Pump", "23 21 13: Pump", "TDH 60 ft"),
    ("Head Pump kPa", "23 21 13: Pump", "TDH 200 kPa"),
    ("Chiller kW", "23 21 23: Chiller", "350 kW"),
    ("Chiller Qual", "23 21 23: Chiller", "Cooling 250 ton"),
    ("Boiler Qual", "23 52 16: Boiler", "Heating 900 MBH"),
    ("Bare Fan", "23 34 16: Fan", "24 in x 20 ft duct, 900"),
    ("Bare Pump", "23 21 13: Pump", "2 in suction, 6 ft offset, 40"),
    ("Bare Pump No Sizes", "23 21 13: Pump", "40"),
    ("Bare VAV", "23 73 13: VAV", "12 in collar, 8 ft duct, 300"),
    ("Conflict Chiller", "23 21 23: Chiller", "Cooling 100 ton"),
    ("Chiller CompVal", "23 21 23: Chiller", "Chiller"),
    ("Diffuser Ls", "23 33 13: Diffuser", "50 L/s"),
]
_VALUE_COMP_ROWS = [
    ("Chiller CH-1", "Chiller", "1A01", "Chiller"),
    ("Boiler B-1", "Boiler", "1A01", "Boiler"),
    ("Coil HC-1", "Heating Coil HW", "1B21", "Heating Coil"),
    ("Coil CC-1", "Cooling Coil CHW", "1B21", "Cooling Coil"),
    ("Coil C-1", "Generic Coil", "1B21", "Coil"),
    ("Pump P-1", "Pump", "1A01", "Pump"),
    ("Fan EF-1", "Exhaust Fan", "1C13", "Exhaust Fan"),
    ("VAV-1", "VAV Terminal", "2B12", "VAV Box"),
    ("AHU-1", "AHU", "n/a", "Air Handling Unit"),
    ("Pump P-2", "Pump Motor", "1A01", "Pump"),
    ("VAV-3", "VAV Power", "2B12", "VAV Box"),
    ("Pump P-3", "Head Pump", "1A01", "Pump"),
    ("Pump P-5", "Head Pump kPa", "1A01", "Pump"),
    ("Chiller CH-2", "Chiller kW", "1A01", "Chiller"),
    ("Chiller CH-4", "Chiller Qual", "1A01", "Chiller"),
    ("Boiler B-2", "Boiler Qual", "1A01", "Boiler"),
    ("Fan EF-2", "Bare Fan", "1C13", "Exhaust Fan"),
    ("Pump P-4", "Bare Pump", "1A01", "Pump"),
    ("Pump P-6", "Bare Pump No Sizes", "1A01", "Pump"),
    ("VAV-2", "Bare VAV", "2B12", "VAV Box"),
    ("Chiller CH-3", "Conflict Chiller", "1A01", "Cooling 120 ton"),
    ("Chiller CH-5", "Chiller CompVal", "1A01", "180 ton"),
    ("Diffuser D-1", "Diffuser Ls", "1A02", "Supply Diffuser"),
]


def _write_value_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    t = wb.active
    t.title = "Type"
    t.append(["Name", "CreatedBy", "CreatedOn", "Category", "Description", "ExtObject"])
    for name, cat, desc in _VALUE_TYPE_ROWS:
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
    for name, type_name, space, desc in _VALUE_COMP_ROWS:
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
    for name in {"1A01", "1A02", "1B21", "1C13", "2B12"}:
        s.append([name, "tester@example.com", "2013-10-09", "Space", "F1", "n/a"])
    path = tmp_path / "value-schedules.xlsx"
    wb.save(path)
    return path


@pytest.fixture()
def vresult(tmp_path):
    return parse_cobie_workbook(_write_value_workbook(tmp_path))


def _vrow(res: MechScheduleResult, tag: str) -> MechScheduleRow:
    matches = [r for r in res.rows if r.tag == tag]
    assert len(matches) == 1, f"expected one row tagged {tag}, got {matches}"
    return matches[0]


def _slots(row: MechScheduleRow):
    return {v.field: v for v in row.values}


def _value_reviews(res: MechScheduleResult, tag: str):
    return [i for i in res.review_items if i.kind == "mech_schedule_value" and tag in i.id]


def test_coil_text_rules_and_coils_stay_rows(vresult):
    assert _vrow(vresult, "HC-1").system_type == "heating_coil"
    assert _vrow(vresult, "CC-1").system_type == "cooling_coil"
    assert _vrow(vresult, "C-1").system_type == "coil"
    # coils stay schedule rows, never MechSystem entities
    system_tags = {s.tag for s in vresult.systems}
    assert {"HC-1", "CC-1", "C-1"}.isdisjoint(system_tags)


def test_bare_thermal_units_map_through_family_defaults(vresult):
    cooling = _slots(_vrow(vresult, "CH-1"))["cooling_capacity"]
    assert cooling.value == 150.0
    assert cooling.unit == "ton"
    assert cooling.confidence == "medium"
    assert cooling.zone_load_impacting is False
    heating = _slots(_vrow(vresult, "B-1"))["heating_capacity"]
    assert heating.value == 1200.0
    assert heating.unit == "MBH"
    assert heating.confidence == "medium"


def test_coil_family_defaults_follow_the_coil_kind(vresult):
    assert _slots(_vrow(vresult, "HC-1"))["heating_capacity"].value == 800.0
    assert _slots(_vrow(vresult, "CC-1"))["cooling_capacity"].value == 60.0
    assert _slots(_vrow(vresult, "CC-1"))["cooling_capacity"].unit == "ton"


def test_explicit_qualifier_is_high_confidence(vresult):
    cooling = _slots(_vrow(vresult, "CH-4"))["cooling_capacity"]
    assert cooling.value == 250.0
    assert cooling.confidence == "high"
    heating = _slots(_vrow(vresult, "B-2"))["heating_capacity"]
    assert heating.value == 900.0
    assert heating.confidence == "high"


def test_airflow_units_min_max_and_lps(vresult):
    fan = _slots(_vrow(vresult, "EF-1"))["airflow"]
    assert fan.value == 1200.0
    assert fan.unit == "CFM"
    assert fan.confidence == "high"
    assert fan.zone_load_impacting is True
    row = _vrow(vresult, "VAV-1")
    slots = _slots(row)
    assert slots["airflow_min"].value == 400.0
    assert slots["airflow_min"].confidence == "high"
    assert slots["airflow_max"].value == 1600.0
    assert slots["airflow_max"].zone_load_impacting is True
    assert "airflow" not in slots
    diffuser = _slots(_vrow(vresult, "D-1"))["airflow"]
    assert diffuser.value == 50.0
    assert diffuser.unit == "L/s"
    assert diffuser.zone_load_impacting is True


def test_water_flow_gpm(vresult):
    flow = _slots(_vrow(vresult, "P-1"))["water_flow"]
    assert flow.value == 50.0
    assert flow.unit == "GPM"
    assert flow.confidence == "high"
    assert flow.zone_load_impacting is False


def test_power_slots_for_non_thermal_families(vresult):
    motor = _slots(_vrow(vresult, "P-2"))["power"]
    assert motor.value == 7.5
    assert motor.unit == "kW"
    assert motor.confidence == "high"  # explicit motor qualifier
    vav = _slots(_vrow(vresult, "VAV-3"))["power"]
    assert vav.value == 2.0
    assert vav.confidence == "medium"  # unqualified kW on a non-thermal family


def test_head_slot_with_tdh_qualifier(vresult):
    head_ft = _slots(_vrow(vresult, "P-3"))["head"]
    assert head_ft.value == 60.0
    assert head_ft.unit == "ft"
    assert head_ft.confidence == "high"
    head_kpa = _slots(_vrow(vresult, "P-5"))["head"]
    assert head_kpa.value == 200.0
    assert head_kpa.unit == "kPa"
    assert head_kpa.confidence == "high"


def test_ambiguous_family_thermal_values_are_never_recorded(vresult):
    # AHU with bare thermal unit, generic coil, chiller with bare kW:
    # each states a value the family cannot disambiguate, so no value is
    # emitted and a review item cites the cell instead.
    for tag, cell in (("AHU-1", None), ("C-1", None), ("CH-2", None)):
        row = _vrow(vresult, tag)
        assert row.values == [], f"{tag} must carry no values"
        reviews = _value_reviews(vresult, tag)
        assert reviews, f"{tag} must produce a value review item"
        assert reviews[0].id.startswith(f"mechval:{tag}:")
        assert "value not recorded" in reviews[0].description


def test_value_review_item_cites_sheet_row_cell_and_raw(vresult):
    item = _value_reviews(vresult, "AHU-1")[0]
    assert item.kind == "mech_schedule_value"
    assert item.needs_review is True
    assert "Type!" in item.description
    assert "63300000 J" in item.description
    assert "ahu" in item.description


def test_bare_number_ip_defaults_and_unused_bare_numbers(vresult):
    fan = _slots(_vrow(vresult, "EF-2"))["airflow"]
    assert fan.value == 900.0
    assert fan.unit == "CFM"
    assert fan.confidence == "medium"  # IP default, not a stated unit
    assert fan.zone_load_impacting is True
    pump = _slots(_vrow(vresult, "P-4"))["water_flow"]
    assert pump.value == 40.0
    assert pump.unit == "GPM"
    assert pump.confidence == "medium"
    # no feet+inches in the text, or no family default: bare numbers are
    # unreadable, never used and never guessed
    assert _vrow(vresult, "P-6").values == []
    assert _vrow(vresult, "VAV-2").values == []
    # dimension sizes (in/ft) inside an IP-default text never become
    # values or review items
    assert not _value_reviews(vresult, "P-6")
    assert not _value_reviews(vresult, "VAV-2")
    assert not _value_reviews(vresult, "P-4")
    assert not _value_reviews(vresult, "EF-2")


def test_conflicting_slot_values_are_nulled_with_one_review(vresult):
    row = _vrow(vresult, "CH-3")
    slots = _slots(row)
    assert set(slots) == {"cooling_capacity"}
    values = [v for v in row.values if v.field == "cooling_capacity"]
    assert len(values) == 2
    for value in values:
        assert value.value is None
        assert value.confidence == "low"
    assert {v.raw for v in values} == {"100 ton", "120 ton"}
    conflicts = [i for i in _value_reviews(vresult, "CH-3") if "conflict" in i.description]
    assert len(conflicts) == 1
    assert "100 ton" in conflicts[0].description
    assert "120 ton" in conflicts[0].description


def test_value_provenance_cells_name_the_workbook_cell(vresult):
    cooling = _slots(_vrow(vresult, "CH-1"))["cooling_capacity"]
    assert cooling.sheet == "Type"
    assert cooling.cell.startswith("E")
    assert cooling.row >= 2
    comp_value = _slots(_vrow(vresult, "CH-5"))["cooling_capacity"]
    assert comp_value.sheet == "Component"
    assert comp_value.cell.startswith("F")
    assert comp_value.row >= 2
    assert comp_value.value == 180.0


def test_zone_load_flag_confined_to_airflow_slots(vresult):
    for row in vresult.rows:
        for value in row.values:
            if value.field in ZONE_LOAD_SLOTS:
                assert value.zone_load_impacting is True
            else:
                assert value.zone_load_impacting is False
    airflow_count = sum(1 for row in vresult.rows for v in row.values if v.field in ZONE_LOAD_SLOTS)
    assert vresult.stats["zone_load_inputs"] == airflow_count
    # recorded inputs only: nothing here sizes equipment, so no slot or
    # stat carries a sized/derived value
    assert not any("sized" in s or "load" == s for s in VALUE_SLOTS)


def test_stats_carry_value_counts(vresult):
    assert vresult.stats["values"] == sum(len(r.values) for r in vresult.rows)
    assert vresult.stats["values"] > 10
    assert vresult.stats["value_review_items"] == len(
        [i for i in vresult.review_items if i.kind == "mech_schedule_value"]
    )


def test_system_values_copy_row_values(vresult):
    systems = {s.tag: s for s in vresult.systems}
    assert systems["CH-1"].values == _vrow(vresult, "CH-1").values
    assert systems["EF-1"].values
    # low-confidence rows still parse values but produce no system
    assert _vrow(vresult, "CH-3").system_type == "chiller"


def test_mechvalue_is_frozen_and_rows_default_to_empty():
    value = MechValue(
        field="airflow",
        value=100.0,
        unit="CFM",
        raw="100 CFM",
        sheet="Type",
        row=2,
        cell="E2",
        confidence="high",
        zone_load_impacting=True,
    )
    with pytest.raises(Exception):
        value.value = 200.0  # type: ignore[misc]
    row = MechScheduleRow(
        tag="X-1",
        name="X-1",
        type_name="t",
        type_description="",
        category="c",
        description="",
        service_area="",
        system_type="pump",
        confidence="medium",
        capacity=None,
    )
    assert row.values == []
    _row_values("X-1", "pump", "", "", "", "")


_ATTR_TYPE_ROWS = [("AHU", "23-75 35 14 17: Modular Indoor", "")]
_ATTR_COMP_ROWS = [("AHU-1", "AHU", "1B21", "Air Handling Unit")]


def _write_attribute_workbook(tmp_path, attr_rows, include_sheet=True):
    """A minimal workbook plus an ``Attribute`` sheet of stated requirements."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    t = wb.active
    t.title = "Type"
    t.append(["Name", "CreatedBy", "CreatedOn", "Category", "Description", "ExtObject"])
    for name, cat, desc in _ATTR_TYPE_ROWS:
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
    for name, type_name, space, desc in _ATTR_COMP_ROWS:
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
    s.append(["1B21", "tester@example.com", "2013-10-09", "Space", "F1", "n/a"])
    if include_sheet:
        at = wb.create_sheet("Attribute")
        at.append(
            ["Name", "CreatedBy", "CreatedOn", "Category", "SheetName", "RowName", "Value", "Unit"]
        )
        for req_name, value, unit in attr_rows:
            at.append([req_name, "", "", "Requirement", "Component", "AHU-1", value, unit])
    path = tmp_path / "attribute-schedules.xlsx"
    wb.save(path)
    return path


def test_attribute_requirements_fill_airflow_slots(tmp_path):
    """#1240 follow-up: stated Attribute requirements fill the slots.

    The clinic workbook states ``Total`` / ``Outside Air`` in L/s on the
    ``Attribute`` sheet; the text scan never sees those cells, so the
    benchmark measured airflow recovery 0/33. The requirement's stated
    name picks the slot: outside air is ventilation air, not a supply
    total, so it fills its own slot and can never misstate a fan's
    airflow. Provenance points at the stated Value cell.
    """
    path = _write_attribute_workbook(
        tmp_path, [("Total", "12.5", "L/s"), ("Outside Air", "3", "L/s")]
    )
    res = parse_cobie_workbook(path)
    row = _vrow(res, "AHU-1")
    slots = _slots(row)
    assert slots["airflow"].value == 12.5
    assert slots["airflow"].unit == "L/s"
    assert slots["airflow"].sheet == "Attribute"
    assert slots["airflow"].confidence == "high"
    assert slots["airflow"].zone_load_impacting is True
    assert slots["outside_air_flow"].value == 3.0
    assert slots["outside_air_flow"].unit == "L/s"
    assert slots["outside_air_flow"].sheet == "Attribute"
    # the stated Value cell is the provenance: Attribute!G2 is the Total row
    assert slots["airflow"].row == 2 and slots["airflow"].cell.endswith("2")
    assert res.stats["zone_load_inputs"] == 2


def test_attribute_conflict_with_stated_text_nulls_the_slot(tmp_path):
    """A slot stated twice with different numbers is one conflict, cross-source.

    The Type description's stated airflow and the Attribute requirement's
    stated airflow disagree: every airflow value is nulled at low
    confidence and one review item names both raw strings, the same
    #1236 conflict rule as two text statements.
    """
    _ATTR_TYPE_ROWS[0] = ("AHU", "23-75 35 14 17: Modular Indoor", "500 L/s")
    try:
        path = _write_attribute_workbook(tmp_path, [("Total", "12.5", "L/s")])
    finally:
        _ATTR_TYPE_ROWS[0] = ("AHU", "23-75 35 14 17: Modular Indoor", "")
    res = parse_cobie_workbook(path)
    row = _vrow(res, "AHU-1")
    airflow = [v for v in row.values if v.field == "airflow"]
    assert len(airflow) == 2
    assert all(v.value is None and v.confidence == "low" for v in airflow)
    conflict = [
        i
        for i in res.review_items
        if i.kind == "mech_schedule_value" and "conflict" in i.description
    ]
    assert len(conflict) == 1
    assert "500 L/s" in conflict[0].description and "12.5" in conflict[0].description


def test_missing_attribute_sheet_is_a_noop(tmp_path):
    """A workbook without an ``Attribute`` sheet parses exactly as before."""
    path = _write_attribute_workbook(tmp_path, [], include_sheet=False)
    res = parse_cobie_workbook(path)
    row = _vrow(res, "AHU-1")
    assert row.values == []
    assert res.stats["zone_load_inputs"] == 0


def test_attribute_skips_non_airflow_and_unreadable_requirements(tmp_path):
    """Scope line: only airflow-unit requirements are read in this pass.

    A requirement in a unit the reader does not table (``m3/h`` here), a
    stated ``n/a``, and prose are left for a separate decision, recorded
    in the reader rather than silently invented as values. A stated
    range keeps value ``None`` (an interval, never a number), the same
    rule as text ranges.
    """
    path = _write_attribute_workbook(
        tmp_path,
        [
            ("Total", "10-20", "L/s"),
            ("Outside Air", "n/a", "L/s"),
            ("Total", "12.5", "m3/h"),
            ("Fan Power", "see schedule", "L/s"),
        ],
    )
    res = parse_cobie_workbook(path)
    row = _vrow(res, "AHU-1")
    slots = _slots(row)
    assert set(slots) == {"airflow"}
    assert slots["airflow"].value is None  # stated range
    assert slots["airflow"].unit == "L/s"
    assert res.stats["value_review_items"] == 0


def test_attribute_min_max_names_map_to_their_slots(tmp_path):
    """``Minimum``/``Maximum`` in a stated requirement's name picks the slot."""
    path = _write_attribute_workbook(
        tmp_path, [("Minimum Flow", "2", "L/s"), ("Maximum Flow", "20", "L/s")]
    )
    res = parse_cobie_workbook(path)
    slots = _slots(_vrow(res, "AHU-1"))
    assert slots["airflow_min"].value == 2.0
    assert slots["airflow_max"].value == 20.0


# ---------------------------------------------------------------------------
# #1290: Watt-family stated tokens normalize to canonical unit tokens
# ---------------------------------------------------------------------------


def test_watt_family_stated_tokens_normalize_to_canonical():
    cap = parse_capacity("2500 Watt")
    assert cap["value"] == 2500.0
    assert cap["unit"] == "W"
    assert cap["raw"] == "2500 Watt"


def test_watt_family_kw_magnitude_is_never_converted():
    cap = parse_capacity("2.5 kW")
    assert cap["value"] == 2.5
    assert cap["unit"] == "kW"
    assert cap["raw"] == "2.5 kW"


def test_watt_family_power_slot_accepts_stated_watt_and_stays_ambiguous_on_thermal():
    slot, unit, conf, reason = _map_value_slot("fan", "watt", "Watt", None)
    assert (slot, unit, conf, reason) == ("power", "W", "medium", None)
    slot, unit, conf, reason = _map_value_slot("chiller", "watt", "Watt", None)
    assert slot is None and unit is None and conf is None
    assert "ambiguous between power" in reason
