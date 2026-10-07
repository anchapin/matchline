"""Tests for bem_loads.py (#691): space-use loads and schedules in gbXML/IFC."""

import xml.etree.ElementTree as ET

import pytest

import space_use_defaults_data as data
from bem_export import validate_gbxml, write_gbxml
from bem_helpers import GBXML_NS
from bem_loads import ifc_load_psets, schedule_id
from building_model import Provenance, SpaceLighting, SpaceUse
from ifc_export import _bem_from_model, _export_ifc
from space_use_defaults import apply_space_use_defaults
from tests.test_multistorey_export import _model, _two_storey

NS = {"g": GBXML_NS}


def _simple_model():
    """One storey, three rooms (80, 80, 120 m2), 100 W of counted fixtures each."""
    m = _model(
        [("L1", 0.0, None)],
        [
            ("L1-100", "L1", (0, 0, 10, 8)),
            ("L1-101", "L1", (10, 0, 20, 8)),
            ("L1-102", "L1", (0, 8, 20, 14)),
        ],
    )
    for sp in m.spaces.values():
        sp.lighting = SpaceLighting(total_w=100.0)
    return m


def _office_model():
    m = _simple_model()
    for sp in m.spaces.values():
        sp.lighting = SpaceLighting()  # no fixtures counted: LPD comes from the defaults
    m.spaces["L1-100"].name = "OPEN OFFICE"
    m.spaces["L1-101"].name = "CONFERENCE"
    m.spaces["L1-102"].name = "CORRIDOR"
    apply_space_use_defaults(m)
    return m


def _write(m, tmp_path):
    p = write_gbxml(_bem_from_model(m), tmp_path / "m.xml")
    ok, errs = validate_gbxml(p)
    assert ok, errs[:5]
    return ET.parse(p).getroot()


def _spaces(root):
    return {s.get("id"): s for s in root.iter(f"{{{GBXML_NS}}}Space")}


def test_gbxml_space_carries_densities_from_defaults(tmp_path):
    root = _write(_office_model(), tmp_path)
    sp = _spaces(root)["L1-100"]
    row = data.SPACE_USE_DEFAULTS["open_office"]
    assert float(sp.find("g:LightPowerPerArea", NS).text) == pytest.approx(
        row["lpd_w_m2"], abs=1e-3
    )
    assert sp.find("g:LightPowerPerArea", NS).get("unit") == "WattPerSquareMeter"
    assert float(sp.find("g:EquipPowerPerArea", NS).text) == pytest.approx(
        row["equipment_w_m2"], abs=1e-3
    )
    people = sp.find("g:PeopleNumber", NS)
    assert people.get("unit") == "NumberOfPeople"
    assert float(people.text) == pytest.approx(row["people_per_m2"] * 80.0, abs=1e-3)
    desc = sp.find("g:Description", NS).text
    assert "ASHRAE 90.1-2019" in desc and "Office / OpenOffice" in desc


def test_gbxml_schedules_written_once_and_referenced(tmp_path):
    root = _write(_office_model(), tmp_path)
    sid = schedule_id("OfficeLarge BLDG_OCC_SCH")
    scheds = [s for s in root.findall("g:Schedule", NS) if s.get("id") == sid]
    assert len(scheds) == 1 and scheds[0].get("type") == "Fraction"
    week_ref = scheds[0].find("g:YearSchedule/g:WeekScheduleId", NS).get("weekScheduleIdRef")
    week = [w for w in root.findall("g:WeekSchedule", NS) if w.get("id") == week_ref][0]
    day_types = sorted(d.get("dayType") for d in week.findall("g:Day", NS))
    assert day_types == ["Holiday", "Sat", "Sun", "Weekday"]
    wkdy = [d for d in week.findall("g:Day", NS) if d.get("dayType") == "Weekday"][0]
    ds = [
        d for d in root.findall("g:DaySchedule", NS) if d.get("id") == wkdy.get("dayScheduleIdRef")
    ][0]
    vals = [float(v.text) for v in ds.findall("g:ScheduleValue", NS)]
    assert vals == pytest.approx(data.SCHEDULES["OfficeLarge BLDG_OCC_SCH"]["weekday"])
    for sp in _spaces(root).values():
        assert sp.get("peopleScheduleIdRef") == sid
        assert sp.get("lightScheduleIdRef") == schedule_id("OfficeLarge BLDG_LIGHT_SCH_2013")
        assert sp.get("equipmentScheduleIdRef") == schedule_id("OfficeLarge BLDG_EQUIP_SCH")


def test_gbxml_drawing_values_kept_and_unknown_schedule_not_referenced(tmp_path):
    m = _simple_model()  # 100 W of counted fixtures per room
    sp = m.spaces["L1-100"]
    sp.use = SpaceUse(
        people_per_m2=0.1,
        occupancy_schedule="CUSTOM_OCC",
        provenance=Provenance("arch_A101", 2, "room_schedule", 0.9),
    )
    apply_space_use_defaults(m)
    s = _spaces(_write(m, tmp_path))["L1-100"]
    assert float(s.find("g:LightPowerPerArea", NS).text) == pytest.approx(100.0 / 80.0)
    assert float(s.find("g:PeopleNumber", NS).text) == pytest.approx(8.0)
    assert s.get("peopleScheduleIdRef") is None  # no profile for a drawing-only name
    assert "drawing (room_schedule, arch_A101)" in s.find("g:Description", NS).text


def test_gbxml_without_loads_writes_no_load_elements(tmp_path):
    m = _simple_model()
    for sp in m.spaces.values():
        sp.lighting = SpaceLighting()
    root = _write(m, tmp_path)
    assert root.findall("g:Schedule", NS) == []
    for s in _spaces(root).values():
        for tag in ("PeopleNumber", "LightPowerPerArea", "EquipPowerPerArea", "Description"):
            assert s.find(f"g:{tag}", NS) is None


def test_multistorey_gbxml_carries_loads_too(tmp_path):
    m = _two_storey()
    for sp in m.spaces.values():
        sp.name = "OPEN OFFICE"
    apply_space_use_defaults(m)
    root = _write(m, tmp_path)
    for s in _spaces(root).values():
        assert s.find("g:EquipPowerPerArea", NS) is not None
        assert s.get("peopleScheduleIdRef") == schedule_id("OfficeLarge BLDG_OCC_SCH")
    assert len(root.findall("g:Schedule", NS)) == 3


def test_ifc_psets_for_occupancy_and_thermal_load(tmp_path):
    import ifcopenshell
    import ifcopenshell.util.element as ue

    m = _office_model()
    path = _export_ifc(m, tmp_path / "m.ifc")
    f = ifcopenshell.open(str(path))
    space = [s for s in f.by_type("IfcSpace") if s.Name == "OPEN OFFICE"][0]
    psets = ue.get_psets(space)
    row = data.SPACE_USE_DEFAULTS["open_office"]
    occ = psets["Pset_SpaceOccupancyRequirements"]
    assert occ["OccupancyNumber"] == pytest.approx(row["people_per_m2"] * 80.0, abs=1e-2)
    assert occ["AreaPerOccupant"] == pytest.approx(1 / row["people_per_m2"], abs=1e-2)
    th = psets["Pset_SpaceThermalLoad"]
    assert th["EquipmentSensible"] == pytest.approx(row["equipment_w_m2"] * 80.0, abs=1e-2)
    assert th["Lighting"] == pytest.approx(row["lpd_w_m2"] * 80.0, abs=1e-2)


def test_ifc_load_psets_empty_without_loads():
    class S:
        area_m2 = 10.0
        lighting_w = 0.0
        people_per_m2 = None
        equipment_w_m2 = None

    assert ifc_load_psets(S()) == {}


# --- people heat gain from the prototype activity level (#693) ---


def test_activity_level_traces_to_prototype_activity_schedule():
    for key, row in data.SPACE_USE_DEFAULTS.items():
        if row["activity_schedule"]:
            assert row["activity_w_per_person"] > 0, key
        else:
            assert row["activity_w_per_person"] is None, key
    assert (
        data.SPACE_USE_DEFAULTS["open_office"]["activity_schedule"] == "OfficeMedium ACTIVITY_SCH"
    )
    assert data.SPACE_USE_DEFAULTS["open_office"]["activity_w_per_person"] == 120.0
    # the data-center row has no occupant activity in the prototype: none invented
    assert data.SPACE_USE_DEFAULTS["data_center"]["activity_w_per_person"] is None


def test_apply_fills_activity_but_keeps_a_drawing_value():
    m = _simple_model()
    m.spaces["L1-100"].name = "OPEN OFFICE"
    m.spaces["L1-101"].name = "OPEN OFFICE"
    m.spaces["L1-101"].use = SpaceUse(activity_w_per_person=95.0)
    apply_space_use_defaults(m)
    assert m.spaces["L1-100"].use.activity_w_per_person == 120.0
    assert "activity_w_per_person" in m.spaces["L1-100"].use.provenance.note or (
        m.spaces["L1-100"].use.provenance.method == "doe_prototype_default"
    )
    assert m.spaces["L1-101"].use.activity_w_per_person == 95.0


def test_gbxml_people_heat_gain_written_and_valid(tmp_path):
    root = _write(_office_model(), tmp_path)
    hg = _spaces(root)["L1-100"].find("g:PeopleHeatGain", NS)
    assert hg is not None
    assert hg.get("unit") == "WattPerPerson"
    assert hg.get("heatGainType") == "Total"
    assert float(hg.text) == pytest.approx(120.0)


def test_ifc_thermal_load_people_watts():
    m = _office_model()
    bem = _bem_from_model(m)
    sp = next(s for s in bem.spaces if s.sid == "L1-100")
    psets = ifc_load_psets(sp)
    row = data.SPACE_USE_DEFAULTS["open_office"]
    assert psets["Pset_SpaceThermalLoad"]["People"] == pytest.approx(
        row["people_per_m2"] * sp.area_m2 * 120.0, rel=1e-3
    )
