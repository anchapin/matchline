"""Roof mode for geometry simplification (#616)."""

import copy
import json
import math
import random
import time

import pytest

from building_model import Provenance
from geometry.roof_geometry import roof_plane
from geometry.roof_simplify import roof_simplify_report, simplify_roof
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


def _grid(nx, ny, size=2.0):
    """A flat roof tiled with coplanar square facets, all mergeable."""
    planes = []
    i = 0
    for j in range(ny):
        for k in range(nx):
            x0, y0 = k * size, j * size
            planes.append(
                _rp(
                    f"P{i:04d}",
                    [
                        (x0, y0, 3.0),
                        (x0 + size, y0, 3.0),
                        (x0 + size, y0 + size, 3.0),
                        (x0, y0 + size, 3.0),
                    ],
                )
            )
            i += 1
    return planes


def test_big_roof_is_deterministic_in_any_input_order():
    """Batching must not depend on the order the planes arrive in (#1265)."""
    planes = _grid(8, 8)
    first = json.dumps(roof_simplify_report(simplify_roof(planes, LAT)), sort_keys=True)
    rng = random.Random(11)
    for _ in range(3):
        shuffled = planes[:]
        rng.shuffle(shuffled)
        again = json.dumps(roof_simplify_report(simplify_roof(shuffled, LAT)), sort_keys=True)
        assert again == first


def test_big_roof_simplifies_quickly_down_to_one_plane():
    """A 225-plane roof must not cost minutes per pass (#1265)."""
    planes = _grid(15, 15)
    assert len(planes) == 225
    t0 = time.time()
    res = simplify_roof(planes, LAT)
    dt = time.time() - t0
    assert res.n_before == 225 and res.n_after == 1
    assert abs(res.area_delta_pct) <= 2 and abs(res.aperture_delta_pct) <= 2
    # generous wall-clock bound: the old scan took minutes per pass here
    assert dt < 60.0, f"simplify took {dt:.1f}s"
    # structural bound: the candidate scan stays linear in the roof size
    # instead of quadratic, and the plan cache is not rebuilt per pair
    assert res.stats["pair_scans"] < 12 * len(planes)
    assert res.stats["plan_computations"] <= 2 * len(planes)
    # and the merges are batched: many merges per pass, not one
    assert len(res.merges) == 224
    assert res.stats["passes"] < len(res.merges)


def test_batched_merges_stay_inside_the_budgets():
    """Every accepted merge is inside the area and aperture budgets (#1265)."""
    planes = _grid(9, 9)
    res = simplify_roof(planes, LAT)
    assert res.n_after == 1
    for m in res.merges:
        assert abs(m["area_delta_pct"]) <= 2
        assert abs(m["aperture_delta_pct"]) <= 2
    # the one remaining plane covers the whole roof
    assert res.planes[0].area_m2 == pytest.approx(18 * 18, rel=1e-6)
    note = res.planes[0].provenance.note.replace("merged from ", "")
    assert sorted(note.split(", ")) == sorted(p.id for p in planes)


def _reference_one_per_pass(planes, lat, angle_tol_deg=5.0, small_facet_frac=0.05):
    """The pre-#1265 greedy: exactly one merge per pass, full pair scan.

    Kept in the test suite as the reference the batched implementation is
    compared against (same filters, same budgets, same "cheapest first"
    order, but no batching and no caching).
    """
    from geometry.roof_simplify import (
        MIN_SHARED_EDGE_M,
        _angle_deg,
        _merge,
        _pct,
        _plan,
        _shared_edge_ok,
        _unit_normal,
    )
    from solar_aperture import solar_aperture

    work = sorted((copy.deepcopy(p) for p in planes), key=lambda p: p.id)
    area0 = sum(p.area_m2 for p in work)
    ap0 = solar_aperture(work, lat).total
    merges, refused = [], set()
    while True:
        total = sum(p.area_m2 for p in work)
        cands = []
        for i, a in enumerate(work):
            for b in work[i + 1 :]:
                key = (a.id, b.id)
                if a.level_id != b.level_id or key in refused:
                    continue
                pa, pb = _plan(a), _plan(b)
                if _shared_edge_ok(a, b, pa, pb) < MIN_SHARED_EDGE_M:
                    continue
                ang = _angle_deg(_unit_normal(a.vertices_m), _unit_normal(b.vertices_m))
                small = min(a.area_m2, b.area_m2) <= small_facet_frac * total
                if ang > angle_tol_deg and not small:
                    continue
                host, other = (a, b) if (a.area_m2, b.id) >= (b.area_m2, a.id) else (b, a)
                merged, _why = _merge(host, other, host.id)
                if merged is None:
                    refused.add(key)
                    continue
                trial = [p for p in work if p.id not in key] + [merged]
                da = _pct(sum(p.area_m2 for p in trial), area0)
                dap = _pct(solar_aperture(trial, lat).total, ap0)
                cands.append((abs(dap), abs(da), key, host.id, other.id, merged, trial, da, dap))
        if not cands:
            break
        progressed = False
        for c in sorted(cands, key=lambda c: (round(c[0], 12), round(c[1], 12), c[2])):
            _, _, key, hid, oid, merged, trial, da, dap = c
            if abs(da) > 0.02 or abs(dap) > 0.02:
                refused.add(key)
                continue
            merges.append((hid, oid))
            work = sorted(trial, key=lambda p: p.id)
            progressed = True
            break
        if not progressed:
            break
    return work, merges


def test_batching_merges_at_least_as_much_as_one_merge_per_pass():
    """Batched, disjoint merges must not leave more planes behind (#1265)."""
    planes = _grid(6, 6)
    res = simplify_roof(planes, LAT)
    ref_work, ref_merges = _reference_one_per_pass(planes, LAT)
    assert res.n_after <= len(ref_work)
    assert abs(res.area_delta_pct) <= 2 and abs(res.aperture_delta_pct) <= 2
    for m in res.merges:
        assert abs(m["area_delta_pct"]) <= 2 and abs(m["aperture_delta_pct"]) <= 2


def test_batching_keeps_the_hip_and_dormer_merges():
    """The small case the budgets were written for is unchanged (#1265)."""
    planes = _hip(notch=True) + [_dormer()]
    res = simplify_roof(planes, LAT)
    ref_work, ref_merges = _reference_one_per_pass(planes, LAT)
    assert [m["from"] for m in res.merges] == [list(sorted(m)) for m in ref_merges]
    assert res.n_after == len(ref_work)


def test_build_refusal_does_not_leak_to_merged_facet_with_same_id(monkeypatch):
    """#1269: refusals are keyed by plane identity, not by plane id.

    Pass 1 refuses the (A, B) merge artificially while A is still the
    original 4-vertex facet; after A absorbs C, the merged facet wears A's
    id, and the old id-keyed refusal would have blocked it forever.
    """

    import geometry.roof_simplify as rs

    a = _rp("A", _on(30, "S", 3, [(0, 0), (10, 0), (10, -6), (0, -6)]))
    b = _rp("B", _on(30, "S", 3, [(0, -6), (10, -6), (10, -9), (0, -9)]))
    c = _rp("C", _on(30, "S", 3, [(0, 0), (10, 0), (10, 3), (0, 3)]))
    real_merge = rs._merge

    def flaky(host, other, pid, pa=None, pb=None, cache=None):
        # The deep-copied original A keeps A's area; a merged facet wearing
        # A's id covers A plus C and is bigger, so this refuses only the
        # first-pass (A, B) merge.
        if {host.id, other.id} == {"A", "B"} and abs(host.area_m2 - a.area_m2) < 1e-9:
            return None, "boom"
        return real_merge(host, other, pid, pa=pa, pb=pb, cache=cache)

    monkeypatch.setattr(rs, "_merge", flaky)
    res = simplify_roof([a, b, c], LAT)
    assert len(res.planes) == 1
    assert len(res.merges) == 2
    assert all(m["into"] == "A" for m in res.merges)
    assert any(s["reason"] == "boom" for s in res.skipped)


def test_budget_refusal_is_reported_once_but_reoffered():
    """#1269: a budget-refused pair is re-offered next pass, reported once.

    A and C are real candidates whose merged plane differs from A's, so the
    merged sloped area falls short of the parts (vector areas add, lengths
    do not); with a near-zero area tolerance the pair is refused for budget
    in every pass. B1/B2 are a coplanar merge that keeps the loop alive for
    a second pass. The report must carry the (A, C) refusal exactly once
    even though the pair was evaluated twice.
    """
    t30, t40 = math.tan(math.radians(30)), math.tan(math.radians(40))
    z_edge = 3 + 3 * t30
    c0 = z_edge - 3 * t40
    a = _rp("A", _on(30, "S", 3, [(0, 0), (10, 0), (10, -3), (0, -3)]))
    c = _rp("C", [(x, y, c0 + t40 * -y) for x, y in [(0, -3), (10, -3), (10, -3.3), (0, -3.3)]])
    b1 = _rp("B1", _on(30, "S", 3, [(20, 0), (30, 0), (30, -3), (20, -3)]))
    b2 = _rp("B2", _on(30, "S", 3, [(20, -3), (30, -3), (30, -6), (20, -6)]))
    res = simplify_roof([a, b1, b2, c], LAT, area_tol=1e-9)
    assert [m["into"] for m in res.merges] == ["B1"]
    assert res.stats["passes"] >= 2
    pair_entries = [s for s in res.skipped if s["pair"] == ["A", "C"]]
    assert len(pair_entries) == 1
    assert "area budget" in pair_entries[0]["reason"]


def _budget_payoff_case():
    """Four facets set up so (A, C) is refused for budget in pass 1 and
    accepted in a later pass (#1283).

    A/C are a coplanar north-facing pair, B1/B2 a coplanar south-facing
    pair.  The *reported* areas are deliberately out of step with the
    geometry: A and C under-report 3 m2 each, B1 and B2 over-report 2 m2
    each.  A merged facet's area is measured from its geometry, so merging
    A and C *adds* 6 m2 to the reported total, while merging B1 and B2
    *removes* 4 m2.

    The north-facing pair also moves the solar aperture less than the
    south-facing one, so the cheapest-first order puts (A, C) first: it is
    refused while the working set is still untouched (+2.73 % area, over the
    2 % budget).  B1/B2 is merged next (-1.82 %, inside the budget).  In
    the next pass the working set has lost those 4 m2, so the very same
    (cached) merge of A and C now costs only +0.91 % and the pair is
    accepted.

    With consistent areas this could not happen: a merge never increases
    the reported area (the merged sloped area is |w_a + w_b|, at most the
    sum of the two) nor the solar aperture, so a budget refusal could only
    get worse in later passes.  A pair can only be accepted later when its
    own merge *adds* area relative to the reported totals, which is what
    the under-reported A/C pair does here.
    """
    a = _rp("A", _on(20, "N", 3, [(0, 0), (10, 0), (10, -5), (0, -5)]))
    c = _rp("C", _on(20, "N", 3, [(0, -5), (10, -5), (10, -10), (0, -10)]))
    b1 = _rp("B1", _on(30, "S", 3, [(20, 0), (30, 0), (30, -5), (20, -5)]))
    b2 = _rp("B2", _on(30, "S", 3, [(20, -5), (30, -5), (30, -10), (20, -10)]))
    a.area_m2 -= 3.0
    c.area_m2 -= 3.0
    b1.area_m2 += 2.0
    b2.area_m2 += 2.0
    return a, c, b1, b2


def test_budget_refused_pair_is_accepted_in_a_later_pass():
    """#1283: the payoff of the re-offer -- a pair refused for budget in
    pass 1 lands in a later pass, once the competing candidate has merged
    away and the budget has room again.

    The pair must be refused *and* merged: the refusal shows up in
    ``skipped`` (reported once), the merge in ``merges``.  If the re-offer
    were broken (the pair remembered and never offered again) the merge
    would be missing; if the refusal never happened the skip entry would
    be missing.
    """
    a, c, b1, b2 = _budget_payoff_case()
    res = simplify_roof([a, c, b1, b2], LAT, area_tol=0.02, aperture_tol=0.03)
    # pass 1 refuses (A, C) and merges (B1, B2); a later pass merges (A, C)
    assert res.stats["passes"] >= 2
    assert {m["into"]: m["from"] for m in res.merges} == {
        "B1": ["B1", "B2"],
        "A": ["A", "C"],
    }
    assert res.n_before == 4 and res.n_after == 2
    # the refusal is reported exactly once, with the pass-1 numbers
    entries = [s for s in res.skipped if s["pair"] == ["A", "C"]]
    assert len(entries) == 1
    assert "area budget" in entries[0]["reason"]
    assert "+2.729%" in entries[0]["reason"]
    # and the merge that followed it was accepted inside the budget
    ac = next(m for m in res.merges if m["into"] == "A")
    assert ac["area_delta_pct"] == pytest.approx(0.9096, abs=1e-3)
    assert abs(ac["area_delta_pct"]) <= 2.0


def test_reoffered_pair_reuses_the_cached_merge_unchanged(monkeypatch):
    """#1283: the cached merge of a re-offered pair is shared state.

    The merge of a pair is built once and cached; a later pass must reuse
    that facet, and nothing may mutate it on the way.  So the facet that
    ends up in the result must be geometrically the same as a fresh
    ``_merge`` of the same pair.
    """
    import geometry.roof_simplify as rs

    a, c, b1, b2 = _budget_payoff_case()
    real_merge = rs._merge
    calls = []

    def counting(host, other, pid, pa=None, pb=None, cache=None):
        calls.append((host.id, other.id))
        return real_merge(host, other, pid, pa=pa, pb=pb, cache=cache)

    monkeypatch.setattr(rs, "_merge", counting)
    res = simplify_roof([a, c, b1, b2], LAT, area_tol=0.02, aperture_tol=0.03)
    # the (A, C) merge was built once and taken from the cache in pass 2
    assert calls.count(("A", "C")) == 1
    assert ("B1", "B2") in calls
    # and the cached facet came out of the run unchanged
    fresh, why = real_merge(a, c, "A")
    assert why == ""
    merged = next(p for p in res.planes if p.id == "A")
    assert len(merged.vertices_m) == len(fresh.vertices_m)
    for got, want in zip(merged.vertices_m, fresh.vertices_m):
        assert got == pytest.approx(want, abs=1e-9)
    assert merged.area_m2 == pytest.approx(fresh.area_m2, rel=1e-9)
    assert merged.tilt_deg == pytest.approx(fresh.tilt_deg, abs=1e-9)
    assert merged.azimuth_deg == pytest.approx(fresh.azimuth_deg, abs=1e-9)


def test_refusal_memory_key_follows_the_id_order():
    """#1283: the refusal memory is keyed by ``(id(a), id(b))``.

    That key is direction dependent, and it is safe only because the
    candidate pairs are enumerated over the id-sorted working set, so the
    first element always carries the smaller id.  Pin the invariant, so a
    future change in the enumeration cannot silently make the same pair
    look like two different ones.
    """
    import geometry.roof_simplify as rs

    planes = _hip(notch=True) + [_dormer()]
    work = sorted((copy.deepcopy(p) for p in planes), key=lambda p: p.id)
    assert [p.id for p in work] == sorted(p.id for p in work)
    index = {id(p): i for i, p in enumerate(work)}
    pairs = rs._candidate_pairs(work, rs._PlaneCache())
    assert pairs
    for a, b in pairs:
        assert a.id < b.id
        assert index[id(a)] < index[id(b)]
    # the pairs come out in that order too, each one once
    assert pairs == sorted(pairs, key=lambda ab: (ab[0].id, ab[1].id))
    assert len(pairs) == len({(a.id, b.id) for a, b in pairs})
    # and the report keys the pairs the same way
    res = simplify_roof(planes, LAT)
    for s in res.skipped:
        assert s["pair"][0] < s["pair"][1]


def _edge_twist_pair():
    """Two facets sharing a 10 m edge whose *midpoint* heights agree but
    whose end heights differ by 8 cm (> EDGE_Z_TOL_M = 5 cm).

    The height difference along the shared edge is linear (both facets are
    planar), so it runs +8 cm at x=0 through 0 at the midpoint to -8 cm at
    x=10: a midpoint probe sees perfect agreement and misses both ends.
    """
    a = _rp("A", [(0, 0, 3.0), (10, 0, 3.0), (10, 5, 3.0), (0, 5, 3.0)])
    b = _rp("B", [(0, 0, 3.08), (10, 0, 2.92), (10, -5, 2.92), (0, -5, 3.08)])
    return a, b


def test_shared_edge_is_probed_at_its_endpoints_not_only_the_midpoint():
    """A shared edge only counts when the heights agree along all of it (#1271)."""
    from geometry.roof_simplify import EDGE_Z_TOL_M, _plan, _plane, _shared_edge_ok, _z_at

    a, b = _edge_twist_pair()
    assert EDGE_Z_TOL_M == 0.05
    # the midpoint agrees ...
    assert abs(_z_at(_plane(a), 5.0, 0.0) - _z_at(_plane(b), 5.0, 0.0)) <= EDGE_Z_TOL_M
    # ... the ends do not, so the edge must not be counted
    assert _shared_edge_ok(a, b, _plan(a), _plan(b)) == 0.0


def test_merge_is_refused_when_the_shared_edge_ends_disagree():
    """simplify_roof must not merge across an edge whose ends disagree (#1271)."""
    a, b = _edge_twist_pair()
    res = simplify_roof([a, b], LAT)
    assert res.n_before == 2 and res.n_after == 2
    assert res.merges == []


def test_roof_simplify_does_not_reach_into_solar_aperture_privates():
    # #1272: the aperture cache must go through solar_aperture's public
    # helpers, so a solar_aperture internal refactor cannot break roof
    # simplify at call time.
    from pathlib import Path

    import geometry.roof_simplify as _rs

    source = Path(_rs.__file__).read_text()
    assert "_cos_sum" not in source
