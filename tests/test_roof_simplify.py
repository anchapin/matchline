"""Roof mode for geometry simplification (#616)."""

import copy
import json
import math
import random

import pytest

from building_model import Provenance
from roof_geometry import roof_plane
from roof_simplify import roof_simplify_report, simplify_roof
from solar_aperture import solar_aperture

LAT = 40.0
PROV = Provenance(
    sheet_id="x.ifc", revision=1, method="ifc_import:tier0:roof_plane", confidence=0.9
)


def _on(tilt_deg, facing, z0, pts):
    """Lift plan points (canonical, y down) onto a plane rising away from
    ``facing`` ('S' falls to +y, 'N' to -y, 'E' to +x, 'W' to -x) at z0 on
    the line x=0 / y=0."""
    t = math.tan(math.radians(tilt_deg))
    rise = {
        "S": lambda x, y: -y,
        "N": lambda x, y: y + 6,
        "E": lambda x, y: 10 - x,
        "W": lambda x, y: x,
    }[facing]
    return [(x, y, z0 + t * rise(x, y)) for x, y in pts]


def _rp(pid, verts, level="L1"):
    return roof_plane(pid, verts, level_id=level, host_global_id=pid, provenance=PROV)


def _break(t2):
    """South slope with a slight pitch break: 30 deg up to y=-1.5, then t2."""
    z1 = 3 + 1.5 * math.tan(math.radians(30))
    z2 = z1 + 1.5 * math.tan(math.radians(t2))
    a = _rp("A", [(0, 0, 3), (10, 0, 3), (10, -1.5, z1), (0, -1.5, z1)])
    b = _rp("B", [(0, -1.5, z1), (10, -1.5, z1), (10, -3, z2), (0, -3, z2)])
    return a, b


def _hip(notch=False):
    s_pts = [(0, 0), (10, 0), (7, -3), (3, -3)]
    if notch:
        s_pts = [(0, 0), (4, 0), (4, -1), (6, -1), (6, 0), (10, 0), (7, -3), (3, -3)]
    return [
        _rp("S", _on(30, "S", 3, s_pts)),
        _rp("N", _on(30, "N", 3, [(10, -6), (0, -6), (3, -3), (7, -3)])),
        _rp("E", _on(30, "E", 3, [(10, 0), (10, -6), (7, -3)])),
        _rp("W", _on(30, "W", 3, [(0, -6), (0, 0), (3, -3)])),
    ]


def _dormer():
    # a small shed facing south at 15 deg, meeting the host south face along y = -1
    z_top = 3 + math.tan(math.radians(30))
    t15 = math.tan(math.radians(15))
    return _rp("D", [(4, 0, z_top - t15), (6, 0, z_top - t15), (6, -1, z_top), (4, -1, z_top)])


def test_near_coplanar_pair_merges_within_budget():
    a, b = _break(32)
    res = simplify_roof([a, b], LAT, angle_tol_deg=5)
    assert res.n_before == 2 and res.n_after == 1
    assert res.merges[0]["from"] == ["A", "B"]
    assert abs(res.area_delta_pct) <= 2 and abs(res.aperture_delta_pct) <= 2
    m = res.planes[0]
    assert 30 < m.tilt_deg < 32 and m.azimuth_deg == pytest.approx(180, abs=0.01)
    assert m.provenance.method == "roof_simplify:merge" and "A, B" in m.provenance.note


def test_hip_with_dormer_simplifies_within_budget():
    planes = _hip(notch=True) + [_dormer()]
    res = simplify_roof(planes, LAT)
    assert res.n_before == 5 and res.n_after == 4
    assert [m["from"] for m in res.merges] == [["D", "S"]]
    assert abs(res.area_delta_pct) <= 2 and abs(res.aperture_delta_pct) <= 2
    south = next(p for p in res.planes if p.id == "S")
    assert south.azimuth_deg == pytest.approx(180, abs=0.01)
    assert south.area_m2 * math.cos(math.radians(south.tilt_deg)) == pytest.approx(21, rel=1e-6)


def test_hip_ends_are_not_folded_into_the_sides():
    res = simplify_roof(_hip(), LAT)
    assert res.n_after == 4 and res.merges == []
    assert sorted(round(p.azimuth_deg) for p in res.planes) == [0, 90, 180, 270]


def test_merge_that_swings_aperture_is_refused():
    # hip ends made eligible as "small", area budget opened wide: only the
    # solar budget stands between an east end and the south side
    res = simplify_roof(_hip(), LAT, small_facet_frac=0.2, area_tol=1.0, aperture_tol=0.01)
    assert res.n_after == 4 and res.merges == []
    assert {tuple(s["pair"]) for s in res.skipped} == {
        ("E", "N"),
        ("E", "S"),
        ("N", "W"),
        ("S", "W"),
    }
    assert all(s["reason"].startswith("solar aperture budget") for s in res.skipped)
    assert res.area_delta_pct == 0 and res.aperture_delta_pct == 0


def test_near_coplanar_merge_barely_moves_aperture():
    # a merged plane spans the same outline, so its area-weighted normal keeps
    # the sun-facing vector area: aperture moves far less than area
    res = simplify_roof(list(_break(34)), LAT, angle_tol_deg=5)
    assert res.n_after == 1
    assert abs(res.aperture_delta_pct) < abs(res.area_delta_pct)


def test_merge_that_breaks_the_area_budget_is_refused():
    a, b = _break(34)
    res = simplify_roof([a, b], LAT, angle_tol_deg=5, aperture_tol=0.5, area_tol=1e-6)
    assert res.n_after == 2
    assert res.skipped[0]["reason"].startswith("area budget")


def test_input_planes_are_never_changed():
    planes = _hip(notch=True) + [_dormer()]
    before = copy.deepcopy(planes)
    res = simplify_roof(planes, LAT)
    assert planes == before
    assert all(p is not q for p in res.planes for q in planes)


def test_report_is_deterministic_in_any_input_order():
    planes = _hip(notch=True) + [_dormer()]
    first = json.dumps(roof_simplify_report(simplify_roof(planes, LAT)), sort_keys=True)
    rng = random.Random(7)
    for _ in range(3):
        shuffled = planes[:]
        rng.shuffle(shuffled)
        again = json.dumps(roof_simplify_report(simplify_roof(shuffled, LAT)), sort_keys=True)
        assert again == first


def test_report_carries_both_deltas_and_budgets():
    rep = roof_simplify_report(simplify_roof(_hip(notch=True) + [_dormer()], LAT))
    assert rep["method"] == "greedy_min_aperture_change"
    assert rep["n_planes_before"] == 5 and rep["n_planes_after"] == 4
    assert rep["area_tol_pct"] == 2 and rep["aperture_tol_pct"] == 2
    assert {"area_delta_pct", "aperture_delta_pct"} <= set(rep["merges"][0])
    assert {p["id"] for p in rep["planes"]} == {"S", "N", "E", "W"}


def test_reported_deltas_match_a_fresh_measurement():
    planes = _hip(notch=True) + [_dormer()]
    res = simplify_roof(planes, LAT)
    a0, a1 = sum(p.area_m2 for p in planes), sum(p.area_m2 for p in res.planes)
    s0, s1 = solar_aperture(planes, LAT).total, solar_aperture(res.planes, LAT).total
    assert res.area_delta_pct == pytest.approx((a1 - a0) / a0 * 100, abs=1e-4)
    assert res.aperture_delta_pct == pytest.approx((s1 - s0) / s0 * 100, abs=1e-4)


def test_other_levels_and_stepped_roofs_stay_apart():
    a = _rp("A", _on(30, "S", 3, [(0, 0), (5, 0), (5, -3), (0, -3)]))
    b = _rp("B", _on(30, "S", 3, [(5, 0), (10, 0), (10, -3), (5, -3)]), level="L2")
    assert simplify_roof([a, b], LAT).n_after == 2
    c = _rp("C", _on(30, "S", 4, [(5, 0), (10, 0), (10, -3), (5, -3)]))  # 1 m higher
    assert simplify_roof([a, c], LAT).n_after == 2


def test_no_latitude_is_an_error():
    with pytest.raises(ValueError):
        simplify_roof(_hip(), None)


def test_empty_roof_is_a_no_op():
    res = simplify_roof([], LAT)
    assert res.planes == [] and res.area_delta_pct == 0 and res.aperture_delta_pct == 0
