"""The unified equipment model (#1237): one object per scheduled equipment
piece, tag/type/capacities/airflow from the schedule row, and the HVAC zones
it serves, each relationship carrying its source. Zones with no known air
handler, or served by more than one, get no reference and a review item;
box airflow above the fan airflow stays diversity, never a review item.
"""

from __future__ import annotations

from equipment import build_equipment
from building_model import BuildingModel, ComponentRef, Equipment, ServingRef, Zone

NSOURCES = ("plan_terminal_tag", "plan_tag_placement", "schedule_box_link")


def _z(zid, sids, tag=""):
    tu = ComponentRef(id=tag, type="vav", x_m=0.0, y_m=0.0, tag=tag) if tag else None
    return Zone(id=zid, level_id="L1", space_ids=list(sids), terminal_unit=tu)


def _model(zones=()):
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


def _vav(tag, cfm, ahu="AHU-1", located=None):
    return {
        "tag": tag,
        "kind": "vav",
        "sheet": "M601",
        "cfm_max": cfm,
        "airflow_unit": "cfm",
        "values": {"AHU": ahu} if ahu else {},
        **({"located": located} if located else {}),
    }


def _loc(space_id, conf=0.8):
    return {"sheet": "M101", "space_id": space_id, "level_id": "L1",
            "at_m": [1.0, 2.0], "confidence": conf}


def _item(model, tag):
    return next(e for e in model.equipment if e.tag == tag)


# ---- one object per scheduled piece ----------------------------------------


def test_one_equipment_per_schedule_row():
    m = _model()
    build_equipment(m, [_ahu(), _vav("VAV-1", 800), _vav("VAV-2", 900, ahu="")])
    assert [e.tag for e in m.equipment] == ["AHU-1", "VAV-1", "VAV-2"]
    assert all(isinstance(e, Equipment) for e in m.equipment)
    ahu = _item(m, "AHU-1")
    assert ahu.id == "eq-AHU-1" and ahu.kind == "ahu" and ahu.sheet == "M601"
    assert ahu.airflow_m3s == 2000 * 0.3048**3 / 60.0
    assert ahu.cooling_total_btuh == 60000.0 and ahu.heating_btuh == 40000.0
    assert "AHU-1 supply airflow 2000 cfm" in ahu.sources
    box = _item(m, "VAV-2")
    assert box.served_by == "" and box.cooling_total_btuh is None


def test_row_without_units_keeps_nothing_and_notes_it():
    m = _model()
    notes = build_equipment(m, [_ahu(caps={"cooling_total": _cap(60, unit=None)})])
    ahu = _item(m, "AHU-1")
    assert ahu.cooling_total_btuh is None
    assert any("states no unit" in n for n in notes)


# ---- serving relationships resolved with a source --------------------------


def test_every_serving_relationship_resolved_with_a_source():
    m = _model([_z("z1", ["sp-1"], "VAV-1"), _z("z2", ["sp-2"], "VAV-2")])
    notes = build_equipment(m, [_ahu(), _vav("VAV-1", 800), _vav("VAV-2", 900)])
    assert m.review_queue == []
    ahu = _item(m, "AHU-1")
    assert [(r.zone_id, r.source) for r in ahu.serves] == [
        ("z1", "schedule_box_link"), ("z2", "schedule_box_link")]
    assert all(isinstance(r, ServingRef) and r.source in NSOURCES for r in ahu.serves)
    assert all("via box" in r.note for r in ahu.serves)
    box = _item(m, "VAV-1")
    assert [(r.zone_id, r.source) for r in box.serves] == [("z1", "plan_terminal_tag")]
    assert m.zones["z1"].air_loop == "AHU-1" and m.zones["z2"].air_loop == "AHU-1"
    assert notes == []  # nothing unresolved, nothing to explain


def test_zone_whose_terminal_is_the_central_unit_itself():
    m = _model([_z("z1", ["sp-1"], "RTU-1")])
    build_equipment(m, [_ahu("RTU-1", caps={})])
    ahu = _item(m, "RTU-1")
    assert [(r.zone_id, r.source) for r in ahu.serves] == [("z1", "plan_terminal_tag")]
    assert m.zones["z1"].air_loop == "RTU-1" and m.review_queue == []


def test_fan_coil_serves_its_own_zone_without_a_central_unit():
    m = _model([_z("z1", ["sp-1"], "FCU-1")])
    build_equipment(m, [{"tag": "FCU-1", "kind": "fcu", "sheet": "M601"}])
    fcu = _item(m, "FCU-1")
    assert [(r.zone_id, r.source) for r in fcu.serves] == [("z1", "plan_terminal_tag")]
    assert m.zones["z1"].air_loop == "" and m.review_queue == []


def test_placement_of_a_box_tag_feeds_the_zone_it_sits_in():
    # zone z1 has no traced terminal; the VAV-2 tag placed in sp-1 ties the
    # zone to VAV-2's air handler
    m = _model([_z("z1", ["sp-1"])])
    build_equipment(m, [_ahu(), _vav("VAV-2", 800, located=[_loc("sp-1")])])
    box = _item(m, "VAV-2")
    assert [(r.zone_id, r.source) for r in box.serves] == [
        ("z1", "plan_tag_placement")]
    ahu = _item(m, "AHU-1")
    assert [(r.zone_id, r.source) for r in ahu.serves] == [
        ("z1", "plan_tag_placement")]
    assert m.zones["z1"].air_loop == "AHU-1" and m.review_queue == []


def test_plan_locs_carry_the_placements():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_equipment(m, [_vav("VAV-1", 800, ahu="", located=[_loc("sp-1")])])
    (loc,) = _item(m, "VAV-1").plan_locs
    assert loc["space_id"] == "sp-1" and loc["at_m"] == [1.0, 2.0]


# ---- unresolved serving goes to review, never a guess ----------------------


def test_box_without_a_unit_link_gets_no_reference_and_a_review_item():
    m = _model([_z("z1", ["sp-1"], "VAV-9")])
    notes = build_equipment(m, [_ahu(), _vav("VAV-9", 500, ahu="")])
    assert m.zones["z1"].air_loop == ""
    (item,) = [i for i in m.review_queue if i.kind == "air_loop"]
    assert item.target == {"kind": "hvac_zone", "id": "z1"}
    assert "VAV-9 has no known air handler" in item.description
    assert any("no known air handler: z1 (VAV-9)" in n for n in notes)
    assert _item(m, "AHU-1").serves == []


def test_terminal_not_on_the_schedules_is_flagged():
    m = _model([_z("z1", ["sp-1"], "VAV-7")])
    notes = build_equipment(m, [_ahu(), _vav("VAV-1", 800)])
    assert m.zones["z1"].air_loop == ""
    (item,) = [i for i in m.review_queue if i.kind == "air_loop"]
    assert "VAV-7 is not on the schedules" in item.description
    assert any("not on the schedules: z1 (VAV-7)" in n for n in notes)


def test_zone_without_a_terminal_unit_is_flagged():
    m = _model([_z("z1", ["sp-1"])])
    build_equipment(m, [_ahu(), _vav("VAV-1", 800)])
    (item,) = [i for i in m.review_queue if i.kind == "air_loop"]
    assert "no terminal unit traced" in item.description
    assert m.zones["z1"].air_loop == ""


def test_zone_served_by_two_units_gets_no_reference_and_a_review_item():
    # the traced terminal ties z1 to AHU-1; VAV-2's tag placed in the same
    # room ties it to AHU-2 as well: no single air handler, review it
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_equipment(
        m,
        [
            _ahu(),
            _ahu("AHU-2", cfm=1000),
            _vav("VAV-1", 800),
            _vav("VAV-2", 700, ahu="AHU-2", located=[_loc("sp-1")]),
        ],
    )
    assert m.zones["z1"].air_loop == ""
    (item,) = [i for i in m.review_queue if i.kind == "air_loop"]
    assert "more than one air handler: AHU-1, AHU-2" in item.description
    assert _item(m, "AHU-1").serves == [] and _item(m, "AHU-2").serves == []


def test_two_boxes_of_the_same_unit_in_one_zone_stay_resolved():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_equipment(
        m,
        [_ahu(), _vav("VAV-1", 800), _vav("VAV-2", 700, located=[_loc("sp-1")])],
    )
    assert m.zones["z1"].air_loop == "AHU-1" and m.review_queue == []
    ahu = _item(m, "AHU-1")
    assert sorted((r.zone_id, r.source) for r in ahu.serves) == [
        ("z1", "plan_tag_placement"), ("z1", "schedule_box_link")]


def test_flags_do_not_duplicate_on_a_second_pass():
    m = _model([_z("z1", ["sp-1"], "VAV-9")])
    build_equipment(m, [_ahu(), _vav("VAV-9", 500, ahu="")])
    build_equipment(m, [_ahu(), _vav("VAV-9", 500, ahu="")])
    assert len([i for i in m.review_queue if i.kind == "air_loop"]) == 1


# ---- plumbing ---------------------------------------------------------------


def test_no_schedule_rows_do_nothing():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    assert build_equipment(m, []) == []
    assert m.equipment == [] and m.review_queue == []


def test_equipment_round_trips_through_json():
    m = _model([_z("z1", ["sp-1"], "VAV-1")])
    build_equipment(
        m, [_ahu(), _vav("VAV-1", 800, located=[_loc("sp-1", 0.7)])]
    )
    back = BuildingModel.from_json(m.to_json())
    eq = next(e for e in back.equipment if e.tag == "AHU-1")
    assert isinstance(eq, Equipment) and eq.id == "eq-AHU-1"
    assert [(r.zone_id, r.source, r.note) for r in eq.serves] == [
        ("z1", "schedule_box_link", "via box VAV-1"),
        ("z1", "plan_tag_placement", "VAV-1 tagged in sp-1"),
    ]
    box = next(e for e in back.equipment if e.tag == "VAV-1")
    assert box.provenance.method == "pdf_schedule_equipment"
    (loc,) = box.plan_locs
    assert loc["space_id"] == "sp-1"
    assert back.zones["z1"].air_loop == "AHU-1"
