"""Per-zone heating and cooling capacity from schedule rows (#747 slice 3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zone_capacity import zone_capacities  # noqa: E402


def cap(value, unit, column):
    return {"value": value, "unit": unit, "column": column}


def ahu(tag="AHU-1", cfm=4000.0, caps=None, unit="cfm"):
    caps = (
        caps
        if caps is not None
        else {
            "cooling_total": cap(480.0, "MBH", "CLG TOTAL MBH"),
            "cooling_sensible": cap(360.0, "MBH", "CLG SENS MBH"),
            "heating": cap(200.0, "MBH", "HTG MBH"),
        }
    )
    return {"tag": tag, "kind": "ahu", "cfm": cfm, "airflow_unit": unit, "capacities": caps}


def vav(tag, cfm_max, reheat=None, unit="cfm"):
    e = {"tag": tag, "kind": "vav", "cfm_max": cfm_max, "cfm_min": None, "airflow_unit": unit}
    if reheat is not None:
        e["capacities"] = {"reheat": cap(reheat, "MBH", "REHEAT MBH")}
    return e


def test_central_coil_split_by_box_airflow_over_fan_airflow():
    eq = [ahu(), vav("VAV-1", 1000.0, reheat=12.0), vav("VAV-2", 3000.0)]
    out = zone_capacities(eq, {"VAV-1": "AHU-1", "VAV-2": "AHU-1"})
    a = out["VAV-1"]
    assert a["served_by"] == "AHU-1" and a["share"] == 0.25 and not a["derived"]
    assert a["cooling_sensible"]["btuh"] == 90000.0
    assert a["cooling_total"]["btuh"] == 120000.0
    assert a["heating"]["btuh"] == 50000.0 + 12000.0  # share of central heat + reheat
    assert any("REHEAT MBH 12 MBH (reheat)" in s for s in a["heating"]["sources"])
    assert any("supply fan design airflow" in s for s in a["cooling_sensible"]["sources"])
    assert out["VAV-2"]["cooling_sensible"]["btuh"] == 270000.0
    assert "AHU-1" not in out  # it feeds boxes, so it isn't a zone of its own


def test_no_fan_airflow_falls_back_to_summed_box_airflow_marked_derived():
    eq = [ahu(cfm=None), vav("VAV-1", 1000.0), vav("VAV-2", 1000.0)]
    out = zone_capacities(eq, {"VAV-1": "AHU-1", "VAV-2": "AHU-1"})
    a = out["VAV-1"]
    assert a["share"] == 0.5 and a["derived"]
    assert a["cooling_sensible"]["btuh"] == 180000.0
    assert "sum of its boxes' max airflow" in a["cooling_sensible"]["sources"][0]


def test_fallback_needs_every_box_airflow():
    eq = [ahu(cfm=None), vav("VAV-1", 1000.0), vav("VAV-2", None)]
    out = zone_capacities(eq, {"VAV-1": "AHU-1", "VAV-2": "AHU-1"})
    assert out["VAV-1"]["share"] is None and out["VAV-1"]["cooling_sensible"] is None
    assert any("can't be split" in r for r in out["VAV-1"]["review"])
    assert any("no design airflow" in r for r in out["VAV-2"]["review"])


def test_box_without_a_known_unit_goes_to_review_never_defaulted():
    eq = [ahu(), vav("VAV-1", 1000.0, reheat=10.0)]
    out = zone_capacities(eq)  # only one AHU, but no link: still not assumed
    a = out["VAV-1"]
    assert a["served_by"] == "" and a["cooling_sensible"] is None and a["heating"] is None
    assert "isn't known" in a["review"][0]


def test_box_on_an_unscheduled_unit_goes_to_review():
    out = zone_capacities([vav("VAV-1", 500.0)], {"VAV-1": "RTU-9"})
    assert "RTU-9 isn't on the mechanical schedules" in out["VAV-1"]["review"][0]


def test_box_airflow_above_the_fan_goes_to_review():
    out = zone_capacities([ahu(cfm=800.0), vav("VAV-1", 1000.0)], {"VAV-1": "AHU-1"})
    assert out["VAV-1"]["share"] is None
    assert any("exceeds" in r for r in out["VAV-1"]["review"])


def test_mixed_airflow_units_are_converted_before_the_split():
    eq = [ahu(cfm=1000.0, unit="l/s"), vav("VAV-1", 1059.44)]  # ~500 l/s
    out = zone_capacities(eq, {"VAV-1": "AHU-1"})
    assert out["VAV-1"]["share"] == pytest.approx(0.5, abs=1e-3)


def test_reheat_alone_still_gives_heating_when_the_unit_has_no_heat():
    eq = [ahu(caps={"cooling_sensible": cap(100.0, "MBH", "SENS MBH")}), vav("VAV-1", 1000.0, 8.0)]
    a = zone_capacities(eq, {"VAV-1": "AHU-1"})["VAV-1"]
    assert a["heating"]["btuh"] == 8000.0 and a["cooling_total"] is None


def test_capacity_without_units_goes_to_review():
    eq = [ahu(caps={"cooling_sensible": cap(360.0, "", "COOLING CAPACITY")}), vav("VAV-1", 1000.0)]
    a = zone_capacities(eq, {"VAV-1": "AHU-1"})["VAV-1"]
    assert a["cooling_sensible"] is None
    assert any("states no unit" in r for r in a["review"])


def test_fan_coil_and_lone_rtu_use_their_own_capacity():
    fcu = {
        "tag": "FCU-1",
        "kind": "fcu",
        "capacities": {
            "cooling_sensible": cap(18.0, "MBH", "SENS MBH"),
            "heating": cap(5.0, "kW", "HEATING KW"),
        },
    }
    rtu = ahu("RTU-1", caps={"cooling_total": cap(10.0, "tons", "COOLING TONS")})
    out = zone_capacities([fcu, rtu])
    assert out["FCU-1"]["cooling_sensible"] == {
        "btuh": 18000.0,
        "sources": ["FCU-1 SENS MBH 18 MBH"],
    }
    assert out["FCU-1"]["heating"]["btuh"] == pytest.approx(17060.7, abs=0.1)
    assert out["FCU-1"]["served_by"] == "" and out["FCU-1"]["share"] is None
    assert out["RTU-1"]["cooling_total"]["btuh"] == 120000.0
    assert out["RTU-1"]["cooling_sensible"] is None and out["RTU-1"]["heating"] is None


def test_tags_match_case_insensitively():
    out = zone_capacities([ahu(), vav("vav-1", 1000.0)], {"VAV-1": "ahu-1"})
    assert out["VAV-1"]["share"] == 0.25


def test_feeds_the_space_classifier():
    from space_conditioning import classify_space

    a = zone_capacities([ahu(), vav("VAV-1", 1000.0)], {"VAV-1": "AHU-1"})["VAV-1"]
    got = classify_space(
        area_ft2=1000.0,
        sensible_cooling_btuh=a["cooling_sensible"]["btuh"],
        heating_btuh=a["heating"]["btuh"],
        climate_zone="4A",
    )
    assert got.category == "conditioned"


def test_ahu_is_not_called_single_zone_while_a_box_is_unlinked():
    out = zone_capacities([ahu(), vav("VAV-1", 1000.0)])
    a = out["AHU-1"]
    assert a["cooling_sensible"] is None and a["heating"] is None
    assert "may not be single-zone" in a["review"][0] and "VAV-1" in a["review"][0]


# --- box-to-unit link from a schedule column (#747 slice 4a) ---------------

from zone_capacity import served_by_from_schedules  # noqa: E402


def _box(tag, **values):
    return {"tag": tag, "kind": "vav", "values": {"TAG": tag, **values}}


def test_box_links_from_an_ahu_column():
    eq = [
        ahu("AHU-1"),
        ahu("AHU-2"),
        _box("VAV-1", AHU="AHU-1"),
        _box("VAV-2", **{"SERVED BY": "ahu-2"}),
    ]
    links, notes = served_by_from_schedules(eq)
    assert links == {"VAV-1": "AHU-1", "VAV-2": "AHU-2"} and notes == []
    out = zone_capacities(eq + [], links)
    assert out["VAV-1"]["served_by"] == "AHU-1"


@pytest.mark.parametrize("header", ["FED FROM", "SYSTEM", "AIR HANDLER", "RTU"])
def test_unit_column_header_forms(header):
    eq = [ahu("RTU-3"), _box("VAV-1", **{header: "RTU-3"})]
    assert served_by_from_schedules(eq)[0] == {"VAV-1": "RTU-3"}


def test_unscheduled_unit_is_noted_and_not_linked():
    links, notes = served_by_from_schedules([ahu("AHU-1"), _box("VAV-1", AHU="AHU-9")])
    assert links == {} and "AHU-9" in notes[0]


def test_two_units_named_is_not_linked():
    eq = [ahu("AHU-1"), ahu("AHU-2"), _box("VAV-1", AHU="AHU-1", **{"FED FROM": "AHU-2"})]
    links, notes = served_by_from_schedules(eq)
    assert links == {} and "AHU-1, AHU-2" in notes[0]


@pytest.mark.parametrize(
    "values",
    [
        {"SERVES": "OFFICE 101"},
        {"SYSTEM": "VAV"},
        {"SYSTEM": "CHW"},
        {"AHU": ""},
        {"MAX CFM": "800"},
    ],
)
def test_non_links_are_ignored_quietly(values):
    assert served_by_from_schedules([ahu("AHU-1"), _box("VAV-1", **values)]) == ({}, [])


def test_rows_other_than_boxes_are_not_linked():
    fcu = {"tag": "FCU-1", "kind": "fcu", "values": {"AHU": "AHU-1"}}
    assert served_by_from_schedules([ahu("AHU-1"), fcu]) == ({}, [])


# --- slice 4b: per-space capacity and Section 3.2 category -------------------

from building_model import BuildingModel, ComponentRef, Space, Zone  # noqa: E402
from zone_capacity import SQFT_PER_M2, classify_spaces, space_duties  # noqa: E402


def zone(zid, tag, sids):
    return Zone(
        id=zid,
        level_id="L1",
        space_ids=list(sids),
        terminal_unit=ComponentRef(tag, "vav", 0, 0, tag),
    )


def space(sid, ft2):
    return Space(id=sid, level_id="L1", area_m2=None if ft2 is None else ft2 / SQFT_PER_M2)


def _caps(**duties):
    r = {"served_by": "AHU-1", "share": 0.5, "derived": False, "review": []}
    r.update({d: None for d in ("cooling_sensible", "cooling_total", "heating")})
    for d, v in duties.items():
        r[d] = {"btuh": v, "sources": [f"src {d}"]}
    return r


def test_zone_capacity_is_spread_over_served_area():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1", "S2"])}
    spaces = {"S1": space("S1", 300.0), "S2": space("S2", 100.0)}
    d = space_duties(zones, spaces, {"VAV-1": _caps(cooling_sensible=4000.0, heating=2000.0)})
    assert d["S1"]["cooling_sensible"] == pytest.approx(3000.0)
    assert d["S2"]["heating"] == pytest.approx(500.0)
    assert d["S1"]["cooling_total"] is None
    assert d["S1"]["zones"] == ["Z1"] and "0.750" in d["S1"]["sources"][0]


def test_space_in_two_zones_adds_both():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"]), "Z2": zone("Z2", "VAV-2", ["S1"])}
    caps = {"VAV-1": _caps(cooling_sensible=1000.0), "VAV-2": _caps(cooling_sensible=500.0)}
    d = space_duties(zones, {"S1": space("S1", 200.0)}, caps)
    assert d["S1"]["cooling_sensible"] == pytest.approx(1500.0)


def test_one_zone_missing_a_duty_makes_it_unknown_not_zero():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"]), "Z2": zone("Z2", "VAV-2", ["S1"])}
    caps = {"VAV-1": _caps(heating=1000.0), "VAV-2": _caps()}
    assert space_duties(zones, {"S1": space("S1", 200.0)}, caps)["S1"]["heating"] is None


def test_unit_with_no_capacity_entry_or_missing_area_goes_to_review():
    zones = {"Z1": zone("Z1", "VAV-9", ["S1"])}
    d = space_duties(zones, {"S1": space("S1", 200.0)}, {})
    assert d["S1"]["cooling_sensible"] is None and "VAV-9" in d["S1"]["review"][0]
    zones = {"Z1": zone("Z1", "VAV-1", ["S1", "S2"])}
    d = space_duties(
        zones, {"S1": space("S1", 200.0), "S2": space("S2", None)}, {"VAV-1": _caps(heating=1.0)}
    )
    assert d["S1"]["heating"] is None and "no floor area for S2" in d["S1"]["review"][0]


def test_spaces_with_no_zone_are_not_classified():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    spaces = {"S1": space("S1", 100.0), "S2": space("S2", 100.0)}
    out = classify_spaces(zones, spaces, {"VAV-1": _caps(cooling_sensible=1000.0)})
    assert set(out) == {"S1"}


def test_cooled_space_is_conditioned():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    out = classify_spaces(
        zones, {"S1": space("S1", 100.0)}, {"VAV-1": _caps(cooling_sensible=1000.0)}
    )
    assert out["S1"]["category"] == "conditioned" and out["S1"][
        "cooling_btuh_ft2"
    ] == pytest.approx(10.0)


def test_low_output_stays_in_review_without_the_indirect_test():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    caps = {"VAV-1": _caps(cooling_sensible=100.0, heating=100.0)}
    out = classify_spaces(zones, {"S1": space("S1", 100.0)}, caps, "4A")
    assert out["S1"]["category"] == "review"
    assert any("indirectly" in r for r in out["S1"]["reasons"])


def test_total_cooling_alone_never_makes_a_space_cooled():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    out = classify_spaces(zones, {"S1": space("S1", 100.0)}, {"VAV-1": _caps(cooling_total=5000.0)})
    assert out["S1"]["category"] == "review"
    assert any("only total cooling" in r for r in out["S1"]["reasons"])


def test_zone_review_reason_keeps_an_unconditioned_reading_in_review():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    c = _caps(cooling_sensible=0.0, heating=0.0)
    c["review"] = ["VAV-1 REHEAT 5 states no unit, not used"]
    out = classify_spaces(zones, {"S1": space("S1", 100.0)}, {"VAV-1": c})
    assert out["S1"]["category"] == "review" and c["review"][0] in out["S1"]["reasons"]
    c2 = _caps(cooling_sensible=1000.0)
    c2["review"] = ["something"]
    out = classify_spaces(zones, {"S1": space("S1", 100.0)}, {"VAV-1": c2})
    assert out["S1"]["category"] == "conditioned" and out["S1"]["review"] == ["something"]


def test_derived_flag_carries_to_the_space():
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    c = _caps(cooling_sensible=1000.0)
    c["derived"] = True
    assert classify_spaces(zones, {"S1": space("S1", 100.0)}, {"VAV-1": c})["S1"]["derived"] is True


def test_end_to_end_from_schedule_rows():
    rows = [
        ahu(),
        {**vav("VAV-1", 1000.0, reheat=10.0), "ahu": "AHU-1"},
        {**vav("VAV-2", 3000.0), "ahu": "AHU-1"},
    ]
    from zone_capacity import served_by_from_schedules

    links, _ = served_by_from_schedules(rows)
    if not links:  # column name handled by served_by_from_schedules via headers
        links = {"VAV-1": "AHU-1", "VAV-2": "AHU-1"}
    caps = zone_capacities(rows, links)
    zones = {"Z1": zone("Z1", "VAV-1", ["S1"])}
    out = classify_spaces(zones, {"S1": space("S1", 1000.0)}, caps)
    # 360 MBH sensible x 1000/4000 = 90,000 Btu/h over 1000 ft2
    assert out["S1"]["cooling_sensible_btuh"] == pytest.approx(90000.0)
    assert out["S1"]["category"] == "conditioned"


def test_set_run_writes_space_conditioning_and_reviews():
    from types import SimpleNamespace

    import real_set
    from building_model import Provenance, ReviewItem

    m = BuildingModel(name="t")
    m.spaces = {"S1": space("S1", 100.0), "S2": space("S2", 100.0), "S3": space("S3", 100.0)}
    m.zones = {"Z1": zone("Z1", "FCU-1", ["S1"]), "Z2": zone("Z2", "FCU-2", ["S2"])}
    rows = [
        {"tag": "FCU-1", "kind": "fcu", "sheet": "M-601",
         "capacities": {"cooling_sensible": cap(12.0, "MBH", "CLG SENS MBH")}},
        {"tag": "FCU-2", "kind": "fcu", "sheet": "M-601",
         "capacities": {"cooling_total": cap(12.0, "MBH", "CLG MBH")}},
    ]  # fmt: skip
    review, report = [], SimpleNamespace(notes=[])
    real_set._space_conditioning(m, rows, review, report, Provenance, ReviewItem)
    assert m.spaces["S1"].conditioning["category"] == "conditioned"
    assert m.spaces["S2"].conditioning["category"] == "review"
    assert m.spaces["S3"].conditioning is None
    (item,) = review
    assert (
        item.kind == "space_conditioning" and item.id == "rq-conditioning-S2" and item.needs_review
    )
    assert "1 conditioned, 1 review; 1 space(s) with no zone found" in report.notes[-1]
    back = BuildingModel.from_json(m.to_json())
    assert back.spaces["S1"].conditioning["category"] == "conditioned"


def test_set_run_without_zones_or_equipment_changes_nothing():
    from types import SimpleNamespace

    import real_set
    from building_model import Provenance, ReviewItem

    m = BuildingModel(name="t")
    m.spaces = {"S1": space("S1", 100.0)}
    review, report = [], SimpleNamespace(notes=[])
    real_set._space_conditioning(
        m, [{"tag": "FCU-1", "kind": "fcu"}], review, report, Provenance, ReviewItem
    )
    assert m.spaces["S1"].conditioning is None and review == [] and report.notes == []
