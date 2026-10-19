"""Scheduled air handlers as gbXML air loops (#746, option 3).

Alex's 2026-10-11 decision: each AHU/RTU on the schedules is a gbXML
AirLoop (supply fan with its design airflow, coils with their capacities);
each thermal zone references the air handler serving it, from the box-to-unit
links; a zone with no link or more than one air handler gets no reference and
a review item. Box airflow summing above the fan airflow is diversity, never a
review item.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from air_loops import apply_air_loops, build_air_loops, loop_id, zone_air_loops
from bem.bem_export import validate_gbxml, write_gbxml
from bem.bem_geometry import BEMModel, BEMSpace
from building_model import AirLoop, BuildingModel, ComponentRef, Zone

NS = {"g": "http://www.gbxml.org/schema"}
RING = [(0.0, 0.0), (30.0, 0.0), (30.0, 12.0), (0.0, 12.0)]
TZ = [("A", ["sp-1", "sp-2"]), ("B", ["sp-3"])]


def _rect(x0, x1, y0=0.0, y1=12.0):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _bem(thermal_zones=()):
    spaces = [
        BEMSpace(
            f"sp-{k + 1}", f"ROOM {k + 1}", str(100 + k), _rect(10 * k, 10 * (k + 1)), 120.0, 360.0
        )
        for k in range(3)
    ]
    return BEMModel(
        building_name="t",
        spaces=spaces,
        openings=[],
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
        thermal_zones=list(thermal_zones),
    )


def _z(zid, sids, tag="", loop=""):
    tu = ComponentRef(id=tag, type="vav", x_m=0.0, y_m=0.0, tag=tag) if tag else None
    return Zone(id=zid, level_id="L1", space_ids=list(sids), terminal_unit=tu, air_loop=loop)


def _model(zones):
    m = BuildingModel(name="t", auto_triage=False)
    for z in zones:
        m.zones[z.id] = z
    return m


def _cap(v, unit="MBH", col="COOLING"):
    return {"value": v, "unit": unit, "column": col}


def _ahu(tag="AHU-1", cfm=2000, caps=None):
    return {
        "tag": tag,
        "kind": "ahu",
        "sheet": "M601",
        "cfm": cfm,
        "airflow_unit": "cfm",
        "capacities": caps
        if caps is not None
        else {"cooling_total": _cap(60), "heating": _cap(40, col="HEATING")},
        "values": {},
    }


def _vav(tag, cfm, ahu="AHU-1"):
    return {
        "tag": tag,
        "kind": "vav",
        "sheet": "M601",
        "cfm_max": cfm,
        "airflow_unit": "cfm",
        "values": {"AHU": ahu} if ahu else {},
    }


# ---- Stage 4: from the schedules -------------------------------------------


def test_build_links_zones_and_records_fan_and_coils():
    m = _model([_z("z1", ["sp-1"], "VAV-1"), _z("z2", ["sp-2"], "VAV-2")])
    notes = build_air_loops(m, [_ahu(), _vav("VAV-1", 800), _vav("VAV-2", 900)])
    (lp,) = m.air_loops
    assert isinstance(lp, AirLoop) and lp.tag == "AHU-1" and lp.sheet == "M601"
    assert lp.supply_airflow_m3s == round(2000 * 0.3048**3 / 60.0, 6)
    assert lp.cooling_total_btuh == 60000.0 and lp.heating_btuh == 40000.0
    assert lp.cooling_sensible_btuh is None
    assert lp.box_tags == ["VAV-1", "VAV-2"] and lp.zone_ids == ["z1", "z2"]
    assert m.zones["z1"].air_loop == "AHU-1" and m.zones["z2"].air_loop == "AHU-1"
    assert any("AHU-1 supply airflow 2000 cfm" in s for s in lp.sources)
    assert any("AHU-1 serves 2 HVAC zone(s), 2 box(es)" in n for n in notes)


def test_box_sum_above_fan_is_diversity_not_a_problem():
    m = _model([_z("z1", ["sp-1"], "VAV-1"), _z("z2", ["sp-2"], "VAV-2")])
    notes = build_air_loops(m, [_ahu(cfm=1500), _vav("VAV-1", 1000), _vav("VAV-2", 1000)])
    (lp,) = m.air_loops
    assert lp.box_airflow_sum_m3s > lp.supply_airflow_m3s
    line = next(n for n in notes if "boxes sum" in n)
    assert "ratio 1.33" in line and "diversity, expected" in line
    assert m.zones["z1"].air_loop == "AHU-1"
    assert m.review_queue == []


def test_box_sum_unknown_when_a_box_has_no_airflow():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_air_loops(m, [_ahu(), _vav("VAV-1", 800), _vav("VAV-2", None)])
    assert m.air_loops[0].box_airflow_sum_m3s is None


def test_unlinked_box_zone_gets_no_air_loop_and_is_noted():
    m = _model([_z("z1", ["sp-1"], "VAV-1"), _z("z2", ["sp-2"], "VAV-9")])
    notes = build_air_loops(m, [_ahu(), _vav("VAV-1", 800), _vav("VAV-9", 500, ahu="")])
    assert m.zones["z2"].air_loop == ""
    assert any("no known air handler: z2 (VAV-9)" in n for n in notes)


def test_zone_whose_terminal_is_the_air_handler_itself():
    m = _model([_z("z1", ["sp-1"], "RTU-1")])
    build_air_loops(m, [_ahu("RTU-1", caps={})])
    assert m.zones["z1"].air_loop == "RTU-1"
    lp = m.air_loops[0]
    assert lp.box_tags == [] and lp.cooling_total_btuh is None


def test_no_air_handlers_on_the_schedules_does_nothing():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    assert build_air_loops(m, [_vav("VAV-1", 800, ahu="")]) == []
    assert m.air_loops == [] and m.zones["z1"].air_loop == ""


def test_capacity_without_units_is_not_used_and_noted():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    notes = build_air_loops(
        m, [_ahu(caps={"cooling_total": _cap(60, unit=None)}), _vav("VAV-1", 800)]
    )
    assert m.air_loops[0].cooling_total_btuh is None
    assert any("states no unit" in n for n in notes)


def test_air_loops_round_trip_through_json():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_air_loops(m, [_ahu(), _vav("VAV-1", 800)])
    back = BuildingModel.from_json(m.to_json())
    assert isinstance(back.air_loops[0], AirLoop)
    assert back.air_loops[0].zone_ids == ["z1"] and back.zones["z1"].air_loop == "AHU-1"


# ---- Stage 6: onto the thermal zones ---------------------------------------


def test_loop_id_is_an_xml_name():
    assert loop_id("AHU-1") == "airloop-AHU-1"
    assert loop_id("RTU 2/A") == "airloop-RTU-2-A"


def test_thermal_zone_takes_its_single_air_handler():
    hv = [
        _z("z1", ["sp-1"], loop="AHU-1"),
        _z("z2", ["sp-2"], loop="AHU-1"),
        _z("z3", ["sp-3"], loop="AHU-2"),
    ]
    vals, unsettled = zone_air_loops(_bem(TZ), hv)
    assert vals == {"zone-A": "AHU-1", "zone-B": "AHU-2"} and unsettled == []


def test_two_air_handlers_in_one_thermal_zone_go_to_review():
    hv = [
        _z("z1", ["sp-1"], loop="AHU-1"),
        _z("z2", ["sp-2"], loop="AHU-2"),
        _z("z3", ["sp-3"], loop="AHU-2"),
    ]
    vals, unsettled = zone_air_loops(_bem(TZ), hv)
    assert vals == {"zone-B": "AHU-2"}
    assert unsettled == [("zone-A", ["served by more than one air handler: AHU-1, AHU-2"])]


def test_unlinked_hvac_zone_blocks_the_thermal_zone():
    hv = [_z("z1", ["sp-1"], loop="AHU-1"), _z("z2", ["sp-2"]), _z("z3", ["sp-3"], loop="AHU-1")]
    vals, unsettled = zone_air_loops(_bem(TZ), hv)
    assert vals == {"zone-B": "AHU-1"}
    assert unsettled == [("zone-A", ["HVAC zones with no known air handler: z2"])]


def test_unserved_space_does_not_block_and_unserved_zone_is_flagged():
    hv = [_z("z1", ["sp-1"], loop="AHU-1")]
    vals, unsettled = zone_air_loops(_bem(TZ), hv)
    assert vals == {"zone-A": "AHU-1"}
    assert unsettled == [("zone-B", ["no HVAC zone serves it"])]


def _settled_model():
    m = _model([_z("z1", ["sp-1", "sp-2"], "VAV-1"), _z("z2", ["sp-3"], "VAV-2")])
    build_air_loops(
        m, [_ahu(), _ahu("AHU-2", cfm=1000), _vav("VAV-1", 800), _vav("VAV-2", 600, ahu="AHU-2")]
    )
    return m


def test_apply_sets_bem_and_flags_review_once():
    m = _model([_z("z1", ["sp-1"], "VAV-1"), _z("z2", ["sp-2"], "VAV-2")])
    build_air_loops(m, [_ahu(), _ahu("AHU-2"), _vav("VAV-1", 800), _vav("VAV-2", 800, ahu="AHU-2")])
    bem = _bem(TZ)
    assert apply_air_loops(m, bem) == ["zone-A", "zone-B"]
    assert apply_air_loops(m, bem) == ["zone-A", "zone-B"]
    items = [i for i in m.review_queue if i.kind == "air_loop"]
    assert [i.target["id"] for i in items] == ["zone-A", "zone-B"]
    assert "more than one air handler" in items[0].description
    assert "no HVAC zone serves it" in items[1].description
    assert {lp["name"] for lp in bem.air_loops} == {"AHU-1", "AHU-2"}
    assert bem.zone_air_loop == {}


def test_apply_without_air_handlers_writes_and_flags_nothing():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    bem = _bem(TZ)
    assert apply_air_loops(m, bem) == []
    assert bem.air_loops == [] and bem.zone_air_loop == {} and m.review_queue == []


def test_gbxml_writes_air_loops_and_zone_refs(tmp_path):
    m = _settled_model()
    bem = _bem(TZ)
    apply_air_loops(m, bem)
    assert bem.zone_air_loop == {"zone-A": "airloop-AHU-1", "zone-B": "airloop-AHU-2"}
    p = write_gbxml(bem, tmp_path / "a.xml")
    root = ET.parse(p).getroot()
    refs = {
        z.get("id"): z.find("g:AirLoopId", NS).get("airLoopIdRef")
        for z in root.findall("g:Zone", NS)
    }
    assert refs == {"zone-A": "airloop-AHU-1", "zone-B": "airloop-AHU-2"}
    loops = {lp.get("id"): lp for lp in root.findall("g:AirLoop", NS)}
    a = loops["airloop-AHU-1"]
    assert a.get("systemType") == "VariableAirVolume"
    assert a.find("g:Name", NS).text == "AHU-1"
    fan = a.find("g:AirLoopEquipment[@equipmentType='Fan']", NS)
    mf = fan.find("g:MaximumFlow", NS)
    assert mf.get("unit") == "LPerSec" and abs(float(mf.text) - 943.895) < 0.01
    caps = {c.get("capacityType"): float(c.text) for c in a.iterfind(".//g:Capacity", NS)}
    assert caps == {"CoolingTotal": 60000.0, "Heating": 40000.0}
    assert "M601" not in a.find("g:Description", NS).text  # sources name the row, not the sheet
    assert "AHU-1 supply airflow 2000 cfm" in a.find("g:Description", NS).text


def test_gbxml_with_air_loops_validates(tmp_path):
    m = _settled_model()
    bem = _bem(TZ)
    apply_air_loops(m, bem)
    ok, errors = validate_gbxml(write_gbxml(bem, tmp_path / "v.xml"))
    assert ok, errors


def test_gbxml_without_air_loops_is_unchanged(tmp_path):
    root = ET.parse(write_gbxml(_bem(TZ), tmp_path / "n.xml")).getroot()
    assert root.findall("g:AirLoop", NS) == []
    assert all(z.find("g:AirLoopId", NS) is None for z in root.findall("g:Zone", NS))
