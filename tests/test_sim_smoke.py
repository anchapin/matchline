"""The annual simulation smoke test's parsing and verdict (#752).

The simulation itself needs the OpenStudio CLI and runs nightly
(.github/workflows/sim-smoke.yml); these tests pin what the harness reads
from EnergyPlus output and how it judges it, on small synthetic files.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

import sim_smoke as s
from sim import cbecs_band as cbecs

ABUPS = "AnnualBuildingUtilityPerformanceSummary"


def _sql(tmp_path, site_gj=100.0, area=200.0, cond=200.0, unmet_h=5.0, unmet_c=2.0):
    p = tmp_path / "eplusout.sql"
    con = sqlite3.connect(p)
    con.execute(
        "create table TabularDataWithStrings "
        "(ReportName, TableName, RowName, ColumnName, Value, Units)"
    )
    rows = [
        ("Site and Source Energy", "Total Site Energy", "Total Energy", f"  {site_gj}", "GJ"),
        ("Building Area", "Total Building Area", "Area", f"  {area}", "m2"),
        ("Building Area", "Net Conditioned Building Area", "Area", f"  {cond}", "m2"),
        (
            "Comfort and Setpoint Not Met Summary",
            "Time Setpoint Not Met During Occupied Heating",
            "Facility",
            str(unmet_h),
            "Hours",
        ),
        (
            "Comfort and Setpoint Not Met Summary",
            "Time Setpoint Not Met During Occupied Cooling",
            "Facility",
            str(unmet_c),
            "Hours",
        ),
        ("End Uses", "Heating", "Natural Gas", "  40.0", "GJ"),
        ("End Uses", "Cooling", "Electricity", "  10.0", "GJ"),
        ("End Uses", "Pumps", "Electricity", "  0.00", "GJ"),
        ("End Uses", "Total End Uses", "Electricity", "  10.0", "GJ"),
    ]
    con.executemany(
        "insert into TabularDataWithStrings values (?,?,?,?,?,?)",
        [(ABUPS, *r) for r in rows],
    )
    con.commit()
    con.close()
    return p


def _err(tmp_path, text):
    p = tmp_path / "eplusout.err"
    p.write_text(text)
    return p


def _ref():
    return s.load_reference(s.REFERENCE, "SmallOffice", "90.1-2019", "5A")


OK_ERR = {"completed": True, "warnings": 3, "severe": 0, "fatal": False}


def test_read_err_completed_run(tmp_path):
    p = _err(
        tmp_path,
        "   ************* EnergyPlus Completed Successfully-- 23 Warning; 0 Severe Errors; "
        "Elapsed Time=00hr 00min  0.74sec\n",
    )
    assert s.read_err(p) == {"completed": True, "warnings": 23, "severe": 0, "fatal": False}


def test_read_err_fatal_run(tmp_path):
    p = _err(
        tmp_path,
        "   ** Severe  ** [Construction][const-roof] Missing required property 'outside_layer'\n"
        "   **  Fatal  ** Errors occurred on processing input file.\n"
        "   ** Fatal  ** Preceding condition causes termination.\n"
        "   ************* EnergyPlus Terminated--Fatal Error Detected. "
        "0 Warning; 1 Severe Errors\n",
    )
    r = s.read_err(p)
    assert r["completed"] is False and r["fatal"] is True and r["severe"] == 1


def test_read_err_missing_file_is_not_completed(tmp_path):
    assert s.read_err(tmp_path / "nope.err")["completed"] is False


def test_read_sql_site_eui_and_end_uses(tmp_path):
    r = s.read_sql(_sql(tmp_path))
    assert r["site_eui_mj_m2"] == pytest.approx(500.0)
    assert r["conditioned_area_m2"] == 200.0
    assert (r["unmet_heating_h"], r["unmet_cooling_h"]) == (5.0, 2.0)
    assert r["end_uses_mj_m2"] == {"Cooling / Electricity": 50.0, "Heating / Natural Gas": 200.0}


def test_eui_inside_the_band_is_ok(tmp_path):
    ref, data = _ref()
    res = s.read_sql(_sql(tmp_path, site_gj=ref["site_eui_mj_m2"] * 0.2, area=200.0))
    v = s.judge(OK_ERR, res, ref, data)
    assert v["status"] == "ok" and v["reasons"] == [] and v["eui_ratio"] == pytest.approx(1.0)


@pytest.mark.parametrize("eui", [100.0, 1000.0])
def test_eui_miss_is_reported_not_failed(tmp_path, eui):
    ref, data = _ref()
    res = s.read_sql(_sql(tmp_path, site_gj=eui * 0.2, area=200.0))
    v = s.judge(OK_ERR, res, ref, data)
    assert v["status"] == "eui_out_of_band" and v["reasons"] == []
    assert any("outside" in n and "CBECS 2018" in n for n in v["notes"])


def test_seed_101_first_run_is_above_the_p70_cap(tmp_path):
    # #768: 836.2 MJ/m2 is about CBECS p73, above the p70 cap for a new-code model
    ref, data = _ref()
    res = s.read_sql(_sql(tmp_path, site_gj=836.2 * 0.2, area=200.0))
    v = s.judge(OK_ERR, res, ref, data)
    assert v["status"] == "eui_out_of_band" and v["reasons"] == []
    assert v["eui_ratio"] == pytest.approx(2.596, abs=0.001)


@pytest.mark.parametrize(
    "err, cond, reason",
    [
        ({"completed": False, "warnings": None, "severe": 1, "fatal": True}, 200.0, "complete"),
        ({"completed": True, "warnings": 0, "severe": 2, "fatal": False}, 200.0, "severe"),
        (OK_ERR, 0.0, "conditioned"),
    ],
)
def test_broken_simulation_fails(tmp_path, err, cond, reason):
    ref, data = _ref()
    res = s.read_sql(_sql(tmp_path, cond=cond))
    v = s.judge(err, res, ref, data)
    assert v["status"] == "fail" and any(reason in r for r in v["reasons"])


def test_unmet_hours_over_appendix_g_limit_are_noted_only(tmp_path):
    ref, data = _ref()
    res = s.read_sql(_sql(tmp_path, site_gj=ref["site_eui_mj_m2"] * 0.2, unmet_h=450.0))
    v = s.judge(OK_ERR, res, ref, data)
    assert v["status"] == "ok"
    assert any("unmet_heating_h 450 h" in n for n in v["notes"])


def test_reference_file_states_its_band_and_provenance():
    data = json.loads(s.REFERENCE.read_text())
    assert data["schema"] == "matchline.sim_reference/1"
    assert data["band"]["source"] == "cbecs" and data["band"]["why"]
    assert data["unmet_hours_limit"]["source"]
    for ref in data["references"]:
        for k in ("produced_by", "tools", "weather", "date"):
            assert ref[k], k
        assert re.fullmatch(r"[0-9a-f]{64}", ref["weather_sha256"])
        assert ref["site_eui_mj_m2"] > 0
        cb = ref["cbecs"]
        assert cb["sha256"] == cbecs.SHA256 and cb["url"] == cbecs.URL
        lo_p, hi_p = cb["band_percentiles"]
        pct = cb["percentiles_mj_m2"]
        assert cb["band_mj_m2"] == [pct[f"p{lo_p}"], pct[f"p{hi_p}"]]
        assert list(pct.values()) == sorted(pct.values())
        assert cb["records"] >= 30, "too few CBECS records for a percentile band"
        # a code-built prototype must not fall outside its own band
        assert cb["band_mj_m2"][0] <= ref["site_eui_mj_m2"] <= cb["band_mj_m2"][1]


@pytest.mark.parametrize(
    "zone, code",
    [("5A", 2), ("5c", 2), ("6B", 1), ("7", 1), ("4C", 3), ("3B", 4), ("2A", 5), ("1B", 5)],
)
def test_ashrae_zone_to_cbecs_climate_group(zone, code):
    assert cbecs.pubclim_for(zone) == code


def test_reference_climate_filter_matches_eia_grouping():
    for ref in json.loads(s.REFERENCE.read_text())["references"]:
        assert ref["cbecs"]["filter"]["pubclim"] == [cbecs.pubclim_for(ref["climate_zone"])]
    with pytest.raises(KeyError):
        cbecs.pubclim_for("0A")


def test_weighted_percentile():
    assert cbecs.weighted_percentile([1, 2, 3], [1, 1, 1], 50) == 2
    assert cbecs.weighted_percentile([1, 2, 3], [1, 1, 1], 1) == 1
    assert cbecs.weighted_percentile([1, 2, 3], [1, 1, 1], 99) == 3
    # a heavy record pulls the median toward itself
    assert 2.5 < cbecs.weighted_percentile([1, 2, 3], [1, 1, 10], 50) < 3


def test_cbecs_band_filters_and_converts(tmp_path):
    p = tmp_path / "cbecs.csv"
    p.write_text(
        "PBA,SQFTC,PUBCLIM,SQFT,MFBTU,FINALWT\n"
        "2,2,2,1000,50000,10\n"  # 50 kBtu/ft2
        "2,2,2,2000,200000,10\n"  # 100 kBtu/ft2
        "2,2,1,1000,999999,10\n"  # wrong climate
        "14,2,2,1000,999999,10\n"  # not an office
        "2,2,2,1000,,10\n"  # no consumption
    )
    cb = {"filter": {"pba": [2], "sqftc": [2, 3], "pubclim": [2]}, "band_percentiles": [5, 95]}
    out = cbecs.band(p, cb)
    assert out["records"] == 2 and out["buildings_represented"] == 20
    assert out["band_mj_m2"] == [pytest.approx(567.8, abs=0.1), pytest.approx(1135.7, abs=0.1)]


def test_unknown_reference_raises():
    with pytest.raises(KeyError):
        s.load_reference(s.REFERENCE, "Hospital", "90.1-2019", "5A")


def test_typical_rb_uses_prototype_infiltration_per_wall_area():
    """#768: PNNL-18898 Idesign 0.2016 cfm/ft2 of above-grade wall area."""
    import re
    from pathlib import Path

    rb = (Path(__file__).resolve().parents[1] / "sim" / "typical.rb").read_text()
    rate = float(re.search(r"INFIL_M3_S_PER_M2_WALL = ([0-9.]+)", rb).group(1))
    cfm_ft2_to_m3_s_m2 = 0.3048**3 / 60 / 0.3048**2
    assert rate == pytest.approx(0.2016 * cfm_ft2_to_m3_s_m2, abs=5e-7)
    assert "setFlowperExteriorWallArea(INFIL_M3_S_PER_M2_WALL)" in rb
    assert "PNNL-18898" in rb
