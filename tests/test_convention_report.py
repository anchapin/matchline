"""Convention report: volume bias, non-room area, area budget in one place."""

from __future__ import annotations

import json
from types import SimpleNamespace

from convention_report import SCHEMA, build_convention_report
from tests.model_factory import _ensure_bim_wall, make_clean_model
from validate import run_checks


def _check(rep, cid):
    return next(r for r in rep.results if r.check_id == cid)


def test_schema_and_json_serialisable():
    m = make_clean_model()
    out = build_convention_report(m)
    assert out["schema"] == SCHEMA and out["building"] == m.name
    json.dumps(out)


def test_volume_bias_unavailable_is_not_zero():
    m = make_clean_model()
    rep = run_checks(m)
    vb = build_convention_report(m, rep)["volume_bias"]
    assert vb["available"] is False
    assert vb["reason"] == _check(rep, "convention_bias").message
    assert "delta_m3" not in vb


def test_volume_bias_matches_check_payload():
    m = make_clean_model()
    _ensure_bim_wall(m, thickness_m=0.3)
    rep = run_checks(m)
    chk = _check(rep, "convention_bias")
    assert chk.severity in ("pass", "warn")
    vb = build_convention_report(m, rep)["volume_bias"]
    assert vb["available"] and vb["thickness_m"] == 0.3
    assert vb["interior_volume_m3"] == chk.expected
    assert vb["levels"] == chk.actual["levels"]
    lv_delta = sum(lv["delta_m3"] for lv in chk.actual["levels"].values())
    assert abs(vb["delta_m3"] - lv_delta) < 1e-9 and vb["delta_m3"] > 0


def test_all_rooms_has_empty_non_room_table():
    nr = build_convention_report(make_clean_model())["non_room_area"]
    assert nr["non_room_area_m2"] == 0 and nr["spaces"] == [] and nr["by_type_m2"] == {}
    assert nr["non_room_share_pct"] == 0.0


def test_non_room_area_by_type_and_level():
    m = make_clean_model()
    a, b = sorted(m.spaces.values(), key=lambda s: s.id)[:2]
    a.poly_type, b.poly_type = "closet", "shaft"
    total = sum(abs(s.area_m2 or 0) for s in m.spaces.values())
    nr = build_convention_report(m)["non_room_area"]
    assert nr["by_type_m2"] == {"closet": a.area_m2, "shaft": b.area_m2}
    assert nr["by_level_m2"][a.level_id]["closet"] == a.area_m2
    assert abs(nr["non_room_share_pct"] - 100 * (a.area_m2 + b.area_m2) / total) < 1e-9
    assert [r["space_id"] for r in nr["spaces"]] == [a.id, b.id]
    assert nr["total_floor_area_m2"] == total


def test_missing_area_named_not_counted():
    m = make_clean_model()
    sp = next(iter(m.spaces.values()))
    sp.poly_type, sp.area_m2 = "unassigned", None
    nr = build_convention_report(m)["non_room_area"]
    assert nr["spaces_without_area"] == [sp.id]
    assert nr["by_type_m2"]["unassigned"] == 0.0


def test_area_budget_from_sres():
    sres = SimpleNamespace(area_delta_pct=-1.2, tol=0.02, valid=True)
    ab = build_convention_report(make_clean_model(), sres=sres)["area_budget"]
    assert ab == {
        "available": True,
        "area_delta_pct": -1.2,
        "budget_pct": 2.0,
        "valid": True,
        "within_budget": True,
    }
    sres.area_delta_pct = 3.0
    assert (
        build_convention_report(make_clean_model(), sres=sres)["area_budget"]["within_budget"]
        is False
    )


def test_area_budget_without_sres_is_unavailable():
    ab = build_convention_report(make_clean_model())["area_budget"]
    assert ab["available"] is False and "reason" in ab
