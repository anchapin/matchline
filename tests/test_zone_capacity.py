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
