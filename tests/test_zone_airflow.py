"""Scheduled zone airflow as FlowPerArea on gbXML thermal zones (#746).

Alex's 2026-10-10 decision (option 1): the summed scheduled max airflow of
the HVAC zones serving a thermal zone over its floor area, only where every
space in it is served by HVAC zones serving nothing outside it; otherwise no
value and a review item naming why.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from bem.bem_export import write_gbxml
from bem.bem_geometry import BEMModel, BEMSpace
from building_model import BuildingModel, Zone
from zone_airflow import UNIT, apply_zone_airflow, flow_per_area, gbxml_zones

NS = {"g": "http://www.gbxml.org/schema"}
RING = [(0.0, 0.0), (30.0, 0.0), (30.0, 12.0), (0.0, 12.0)]


def _rect(x0, x1, y0=0.0, y1=12.0):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _bem(spaces=None, thermal_zones=()):
    if spaces is None:
        spaces = [
            BEMSpace(
                f"sp-{k + 1}",
                f"ROOM {k + 1}",
                str(100 + k),
                _rect(10 * k, 10 * (k + 1)),
                120.0,
                360.0,
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


def _z(zid, sids, mx=None, mn=None):
    return Zone(
        id=zid,
        level_id="L1",
        space_ids=list(sids),
        design_airflow_max_m3s=mx,
        design_airflow_min_m3s=mn,
    )


def _model(zones):
    m = BuildingModel(name="t", auto_triage=False)
    for z in zones:
        m.zones[z.id] = z
    return m


TZ = [("A", ["sp-1", "sp-2"]), ("B", ["sp-3"])]


def test_default_zone_is_every_space():
    assert gbxml_zones(_bem()) == [("zone-1", ["sp-1", "sp-2", "sp-3"])]
    assert gbxml_zones(_bem(thermal_zones=TZ)) == [
        ("zone-A", ["sp-1", "sp-2"]),
        ("zone-B", ["sp-3"]),
    ]


def test_clean_mapping_sums_airflow_over_floor_area():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ),
        [_z("VAV-1", ["sp-1"], 0.6), _z("VAV-2", ["sp-2"], 0.3), _z("VAV-3", ["sp-3"], 0.24)],
    )
    assert splits == []
    assert vals["zone-A"] == pytest.approx(0.9 / 240.0)
    assert vals["zone-B"] == pytest.approx(0.24 / 120.0)


def test_one_hvac_zone_serving_two_rooms_in_one_thermal_zone_is_clean():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ), [_z("AHU", ["sp-1", "sp-2"], 1.2), _z("V3", ["sp-3"], 0.1)]
    )
    assert splits == [] and vals["zone-A"] == pytest.approx(1.2 / 240.0)


def test_hvac_zone_spanning_two_thermal_zones_writes_nothing_on_either():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ), [_z("V1", ["sp-1"], 0.5), _z("V23", ["sp-2", "sp-3"], 0.7)]
    )
    assert vals == {}
    got = dict(splits)
    assert any("outside it: V23" in r for r in got["zone-A"])
    assert any("outside it: V23" in r for r in got["zone-B"])


def test_unserved_space_blocks_the_zone_and_is_named():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ), [_z("V1", ["sp-1"], 0.5), _z("V3", ["sp-3"], 0.2)]
    )
    assert "zone-A" not in vals and vals["zone-B"] == pytest.approx(0.2 / 120.0)
    assert dict(splits)["zone-A"] == ["spaces served by no HVAC zone: sp-2"]


def test_serving_zone_without_airflow_blocks_it():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ), [_z("V1", ["sp-1", "sp-2"], None), _z("V3", ["sp-3"], 0.2)]
    )
    assert "zone-A" not in vals
    assert dict(splits)["zone-A"] == ["HVAC zones with no scheduled max airflow: V1"]


def test_thermal_zone_no_hvac_zone_serves():
    vals, splits = flow_per_area(_bem(thermal_zones=TZ), [_z("V1", ["sp-1", "sp-2"], 0.5)])
    assert dict(splits)["zone-B"] == ["no HVAC zone serves it"]


def test_no_scheduled_airflow_anywhere_writes_and_flags_nothing():
    assert flow_per_area(_bem(thermal_zones=TZ), [_z("V1", ["sp-1"]), _z("V3", ["sp-3"])]) == (
        {},
        [],
    )
    assert flow_per_area(_bem(), []) == ({}, [])


def test_hvac_zone_serving_a_space_missing_from_the_bem_is_outside():
    vals, splits = flow_per_area(
        _bem(thermal_zones=TZ), [_z("V1", ["sp-1", "sp-2", "gone"], 0.5), _z("V3", ["sp-3"], 0.1)]
    )
    assert "zone-A" not in vals
    assert any("outside it: V1" in r for r in dict(splits)["zone-A"])


def test_split_room_pieces_follow_their_parent():
    spaces = [
        BEMSpace("sp-1-p1", "R1", "101", _rect(0, 5), 60.0, 180.0, split_from="sp-1"),
        BEMSpace("sp-1-p2", "R1", "101", _rect(5, 10), 60.0, 180.0, split_from="sp-1"),
        BEMSpace("sp-2", "R2", "102", _rect(10, 20), 120.0, 360.0),
    ]
    zones = [_z("V1", ["sp-1"], 0.6), _z("V2", ["sp-2"], 0.3)]
    # both pieces in one thermal zone: clean
    vals, splits = flow_per_area(
        _bem(spaces, [("P", ["sp-1-p1", "sp-1-p2"]), ("C", ["sp-2"])]), zones
    )
    assert splits == [] and vals["zone-P"] == pytest.approx(0.6 / 120.0)
    # pieces in different thermal zones: V1 spans both
    vals, splits = flow_per_area(
        _bem(spaces, [("P", ["sp-1-p1"]), ("C", ["sp-1-p2", "sp-2"])]), zones
    )
    assert vals == {} and {g for g, _ in splits} == {"zone-P", "zone-C"}


def test_default_single_zone_needs_every_space_served():
    zones = [_z("V1", ["sp-1"], 0.5), _z("V2", ["sp-2"], 0.5), _z("V3", ["sp-3"], 0.2)]
    vals, splits = flow_per_area(_bem(), zones)
    assert splits == [] and vals["zone-1"] == pytest.approx(1.2 / 360.0)
    vals, splits = flow_per_area(_bem(), zones[:2])
    assert vals == {} and dict(splits)["zone-1"] == ["spaces served by no HVAC zone: sp-3"]


def test_apply_sets_bem_and_flags_review_once():
    m = _model([_z("V1", ["sp-1"], 0.5), _z("V3", ["sp-3"], 0.2)])
    bem = _bem(thermal_zones=TZ)
    assert apply_zone_airflow(m, bem) == ["zone-A"]
    assert bem.zone_flow_per_area == {"zone-B": pytest.approx(0.2 / 120.0)}
    items = [i for i in m.review_queue if i.kind == "zone_airflow"]
    assert len(items) == 1
    assert items[0].target == {"kind": "zone", "id": "zone-A"}
    assert "sp-2" in items[0].description and items[0].confidence < 0.8
    apply_zone_airflow(m, bem)
    assert len([i for i in m.review_queue if i.kind == "zone_airflow"]) == 1


def test_min_airflow_is_not_used():
    a, _ = flow_per_area(_bem(), [_z("V", ["sp-1", "sp-2", "sp-3"], 0.9, 0.1)])
    b, _ = flow_per_area(_bem(), [_z("V", ["sp-1", "sp-2", "sp-3"], 0.9, 0.5)])
    assert a == b


def _zones_xml(tmp_path, bem):
    root = ET.parse(write_gbxml(bem, tmp_path / "m.xml")).getroot()
    return {z.get("id"): z for z in root.findall(".//g:Zone", NS)}


def test_gbxml_writes_flow_per_area_only_on_settled_zones(tmp_path):
    bem = _bem(thermal_zones=TZ)
    apply_zone_airflow(_model([_z("V1", ["sp-1"], 0.5), _z("V3", ["sp-3"], 0.2)]), bem)
    zs = _zones_xml(tmp_path, bem)
    assert zs["zone-A"].find("g:FlowPerArea", NS) is None
    fpa = zs["zone-B"].find("g:FlowPerArea", NS)
    assert fpa.get("unit") == UNIT == "CubicMPerSecPerSquareM"
    assert float(fpa.text) == pytest.approx(0.2 / 120.0, rel=1e-5)


def test_gbxml_default_zone_and_nothing_when_unset(tmp_path):
    bem = _bem()
    assert _zones_xml(tmp_path, bem)["zone-1"].find("g:FlowPerArea", NS) is None
    bem.zone_flow_per_area = {"zone-1": 0.004}
    assert float(
        _zones_xml(tmp_path, bem)["zone-1"].find("g:FlowPerArea", NS).text
    ) == pytest.approx(0.004)


def test_gbxml_with_flow_per_area_validates(tmp_path):
    from bem.bem_export import validate_gbxml

    bem = _bem(thermal_zones=TZ)
    apply_zone_airflow(_model([_z("V12", ["sp-1", "sp-2"], 0.5), _z("V3", ["sp-3"], 0.2)]), bem)
    path = write_gbxml(bem, tmp_path / "m.xml")
    ok = validate_gbxml(path)
    assert (ok[0] if isinstance(ok, tuple) else ok) is not False
