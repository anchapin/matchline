"""#975: area-definition registry, per-level reporting, reconciliation."""

from __future__ import annotations

import json
import math

from area_definitions import REGISTRY, SCHEMA, compute
from building_model import BuildingModel, Level, Space
from convention_report import build_convention_report
from tests.model_factory import P, _ensure_bim_wall, make_clean_model
from validate import run_checks

ROOM_A = [[0, 0], [7, 0], [7, 6], [0, 6]]
# 3 m x 6 m room with a 2 m x 2 m notch cut out for the shaft: 18 - 4 = 14 m2.
ROOM_B = [[7, 0], [10, 0], [10, 6], [7, 6], [7, 4], [9, 4], [9, 2], [7, 2]]
SHAFT = [[7, 2], [9, 2], [9, 4], [7, 4]]


def _model(spaces):
    m = BuildingModel(name="area_defs_test")
    m.levels.append(Level(id="L1", name="Level 1", wall_height_m=3.0))
    for sid, poly, area, ptype in spaces:
        m.spaces[sid] = Space(
            id=sid,
            level_id="L1",
            name=sid,
            number=sid.split("-")[1],
            polygon_m=[list(p) for p in poly],
            area_m2=area,
            volume_m3=area * 3.0,
            core_provenance=P(note=sid),
            label_confidence=1.0,
            poly_type=ptype,
        )
    return m


def _clinic_like_model():
    return _model(
        [
            ("L1-101", ROOM_A, 42.0, "room"),
            ("L1-102", ROOM_B, 14.0, "room"),
            ("L1-S1", SHAFT, 4.0, "shaft"),
        ]
    )


def _overlapping_model():
    return _model(
        [
            ("L1-101", [[0, 0], [6, 0], [6, 6], [0, 6]], 36.0, "room"),
            ("L1-102", [[3, 0], [9, 0], [9, 6], [3, 6]], 36.0, "room"),
        ]
    )


def test_registry_spells_out_formulas():
    assert [d.id for d in REGISTRY] == ["gross", "construction", "rentable", "assignable"]
    for d in REGISTRY:
        assert d.formula and d.note and d.name


def test_synthetic_level_matches_formulas():
    out = compute(_clinic_like_model(), thickness=0.3, thickness_source="test")
    lv = out["per_level"]["L1"]
    assert math.isclose(lv["construction_union_m2"], 60.0, abs_tol=1e-9)
    assert math.isclose(lv["space_area_sum_m2"], 60.0, abs_tol=1e-9)
    assert math.isclose(lv["penetration_m2"], 4.0, abs_tol=1e-9)
    assert math.isclose(lv["assignable_m2"], 56.0, abs_tol=1e-9)
    assert math.isclose(lv["rentable_m2"], 56.0, abs_tol=1e-9)
    assert math.isclose(lv["unallocated_m2"], 0.0, abs_tol=1e-9)
    assert math.isclose(lv["perimeter_m"], 32.0, abs_tol=1e-9)
    assert math.isclose(lv["gross_m2"], 60.0 + 32.0 * 0.3 + math.pi * 0.09, rel_tol=1e-9)
    rec = out["reconciliation"]["L1"]
    assert rec["gross_gte_rentable"]["verified"] is True
    assert rec["construction_gte_rentable"]["verified"] is True
    assert rec["assignable_lte_rentable"]["verified"] is True
    assert rec["components_match_union"]["attributed"] == "spaces tile the union"
    tot = out["reconciliation"]["total"]
    assert tot["gross_gte_rentable"]["verified"] is True


def test_per_space_rows_classify_inclusion():
    out = compute(_clinic_like_model(), thickness=None)
    rows = {r["space_id"]: r for r in out["per_space"]}
    assert rows["L1-101"]["in_assignable"] is True
    assert rows["L1-101"]["in_rentable"] is True
    assert rows["L1-101"]["excluded_as"] == ""
    assert rows["L1-S1"]["in_assignable"] is False
    assert rows["L1-S1"]["in_rentable"] is False
    assert rows["L1-S1"]["excluded_as"] == "vertical penetration"
    json.dumps(out)


def test_no_thickness_leaves_gross_unavailable_not_zero():
    out = compute(_clinic_like_model(), thickness=None, thickness_reason="no evidence")
    assert out["thickness"]["available"] is False
    assert out["thickness"]["reason"] == "no evidence"
    lv = out["per_level"]["L1"]
    assert lv["gross_m2"] is None
    rec = out["reconciliation"]["L1"]
    assert rec["gross_gte_rentable"]["verified"] is None
    assert "unavailable" in rec["gross_gte_rentable"]["attributed"]
    assert math.isclose(lv["rentable_m2"], 56.0, abs_tol=1e-9)
    assert lv["rentable_m2"] is not None


def test_reconciliation_attributes_overlaps():
    out = compute(_overlapping_model(), thickness=0.3, thickness_source="test")
    rec = out["reconciliation"]["L1"]
    assert rec["assignable_lte_rentable"]["verified"] is False
    assert math.isclose(rec["assignable_lte_rentable"]["delta_m2"], -18.0, abs_tol=1e-6)
    assert rec["components_match_union"]["attributed"] == "overlapping space polygons"
    assert out["reconciliation"]["total"]["assignable_lte_rentable"]["verified"] is False


def test_battery_check_passes_clean_and_flags_overlaps():
    rep = run_checks(make_clean_model())
    res = next(r for r in rep.results if r.check_id == "area_definitions_reconcile")
    assert res.severity == "pass"
    rep2 = run_checks(_overlapping_model())
    res2 = next(r for r in rep2.results if r.check_id == "area_definitions_reconcile")
    assert res2.severity == "error"
    assert "assignable_lte_rentable" in res2.message


def test_convention_report_carries_area_definitions():
    m = make_clean_model()
    _ensure_bim_wall(m, thickness_m=0.3)
    out = build_convention_report(m)
    ad = out["area_definitions"]
    assert ad["schema"] == SCHEMA
    assert ad["thickness"]["available"] is True
    assert ad["thickness"]["thickness_m"] == 0.3
    lv = ad["per_level"]["L1"]
    assert math.isclose(lv["gross_m2"], 60.0 + 32.0 * 0.3 + math.pi * 0.09, rel_tol=1e-9)
    json.dumps(out)
    out2 = build_convention_report(make_clean_model())
    assert out2["area_definitions"]["thickness"]["available"] is False
    assert out2["area_definitions"]["per_level"]["L1"]["gross_m2"] is None
