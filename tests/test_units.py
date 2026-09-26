"""Unit tests for the geometry / registration / schedule / rollup primitives
the validation layer depends on."""

import pytest

from datasets_adapter import Detection, ScheduleEntry, polygon_area_px2, rollup_takeoff
from geometry_simplify import simplify_ring
from registration import (
    Affine2D,
    Facade,
    match_interval_to_segments,
    point_in_polygon,
    register_elevation_geometric,
    register_elevation_grid,
)

# --- polygon area ---------------------------------------------------------


def test_polygon_area_square():
    assert polygon_area_px2([(0, 0), (20, 0), (20, 30), (0, 30)]) == pytest.approx(600.0)


def test_polygon_area_l_shape():
    # 10x10 square minus 5x5 notch
    poly = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)]
    assert polygon_area_px2(poly) == pytest.approx(75.0)


def test_polygon_area_degenerate():
    assert polygon_area_px2([(0, 0), (1, 1)]) == 0.0
    assert polygon_area_px2([]) == 0.0


# --- affine registration --------------------------------------------------


def test_affine_scale_translate_roundtrip():
    aff = Affine2D.from_scale_translate(px_per_m=50.0, ox_px=120.0, oy_px=150.0)
    x_m, y_m = aff.apply(120 + 50 * 3.0, 150 + 50 * 4.0)
    assert (x_m, y_m) == pytest.approx((3.0, 4.0))


def test_affine_from_tie_points():
    pts_px = [(0, 0), (100, 0), (0, 100), (100, 100)]
    pts_m = [(1, 2), (3, 2), (1, 4), (3, 4)]
    aff = Affine2D.from_tie_points(pts_px, pts_m)
    assert aff.apply(50, 50) == pytest.approx((2.0, 3.0))


def test_facade_registration_grid():
    fac = Facade(
        name="south", ref_corner_m=(0.0, 15.0), length_m=24.0, fixed_coord_m=15.0, axis="x"
    )
    reg = register_elevation_grid(
        "elev_A201",
        fac,
        plan_grid_m={"A": 0.0, "B": 8.0, "C": 16.0, "D": 24.0},
        elev_bubbles=[
            {"label": "A", "u_px": 60.0},
            {"label": "B", "u_px": 380.0},
            {"label": "C", "u_px": 700.0},
            {"label": "D", "u_px": 1020.0},
        ],
        v_ground_px=800.0,
        elev_px_per_m=40.0,
        revision=1,
    )
    s, z = reg.to_facade(60.0, 800.0)
    assert s == pytest.approx(0.0, abs=0.05)
    assert z == pytest.approx(0.0, abs=0.05)
    s2, _ = reg.to_facade(1020.0, 800.0)
    assert s2 == pytest.approx(24.0, abs=0.05)
    assert reg.confidence >= 0.85  # grid path is high confidence


def test_facade_registration_geometric():
    fac = Facade(
        name="south", ref_corner_m=(0.0, 15.0), length_m=24.0, fixed_coord_m=15.0, axis="x"
    )
    reg = register_elevation_geometric(
        "elev_A202", fac, wall_u0_px=60.0, elev_px_per_m=40.0, v_ground_px=800.0, revision=1
    )
    # room spanning [10, 30] ft from the corner maps to the same interval
    s10, _ = reg.to_facade(60.0 + 10 * 0.3048 * 40.0, 800.0)
    assert s10 == pytest.approx(10 * 0.3048, abs=1e-9)
    assert reg.confidence < 0.80  # penalized: flagged for review


def test_point_in_polygon():
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon((5, 5), poly)
    assert not point_in_polygon((11, 5), poly)
    assert not point_in_polygon((5, 5), [(0, 0), (1, 1)])


def test_match_interval_to_segments():
    segs = [{"id": "a", "s0": 0, "s1": 10}, {"id": "b", "s0": 10, "s1": 20}]
    seg, frac, amb = match_interval_to_segments(2, 8, segs)
    assert seg["id"] == "a" and frac == pytest.approx(1.0) and not amb
    seg, frac, amb = match_interval_to_segments(8, 12, segs)
    assert amb  # straddles the boundary -> flagged


# --- schedule tag join ----------------------------------------------------


def test_rollup_takeoff_count_x_dims():
    sched = {"A": ScheduleEntry(tag="A", category="window", width_m=1.5, height_m=1.2)}
    dets = [
        Detection(
            label="Window",
            tag="A",
            score=1.0,
            bbox=(0, 0, 10, 10),
            source="t",
            drawing_type="elevation",
        )
        for _ in range(4)
    ]
    res = rollup_takeoff(dets, sched, drawing_type="elevation")
    assert res.lines[0].count == 4
    assert res.lines[0].area_m2 == pytest.approx(4 * 1.5 * 1.2)
    assert res.area_m2["window"] == pytest.approx(7.2)


def test_rollup_unmatched_reported_not_dropped():
    sched = {}
    dets = [Detection(label="Window", tag="ZZZ", score=1.0, bbox=(0, 0, 10, 10), source="t")]
    res = rollup_takeoff(dets, sched)
    assert len(res.unmatched) == 1 and not res.lines


# --- LPD computation via the real linker ----------------------------------


def test_lpd_rollup_matches_schedule(bldg_3room):
    bldg, model, _ = bldg_3room
    sched_w = {r["tag"]: r["watts"] for r in bldg["lighting_schedule"]}
    for sid, sp in model.spaces.items():
        exp = sum(sched_w[f.tag] for f in sp.lighting.fixtures)
        assert sp.lighting.total_w == pytest.approx(exp)
        if sp.area_m2:
            assert sp.lighting.lpd_w_m2 == pytest.approx(exp / sp.area_m2)


# --- envelope budget via the real simplifier -------------------------------


def test_simplify_ring_rectangle_zero_delta():
    ring = [(0, 0), (20, 0), (20, 10), (0, 10)]
    res = simplify_ring(ring, tol=0.02)
    assert res.simplified_count == 4
    assert abs(res.area_delta_pct) < 1e-9
    assert res.valid


@pytest.mark.xfail(
    reason="pre-existing: noisy rectangle with budget=30 causes 0.79% area growth (exceeds MAX_GROWTH=0.1% hard limit); tol=2% docstring vs 0.1% implementation mismatch - see issue #466"
)
def test_simplify_ring_respects_budget():
    import random

    rng = random.Random(7)
    # noisy rectangle: jittered vertices in perimeter order (simple polygon)
    corners = [(0, 0), (20, 0), (20, 10), (0, 10)]
    ring = []
    per_edge = 10
    for i in range(4):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % 4]
        for k in range(per_edge):
            t = k / per_edge
            ring.append(
                (
                    x0 + (x1 - x0) * t + rng.uniform(-0.05, 0.05),
                    y0 + (y1 - y0) * t + rng.uniform(-0.05, 0.05),
                )
            )
    res = simplify_ring(ring, tol=0.02)
    assert abs(res.area_delta_pct) <= 2.0 + 1e-9
    assert res.valid
    assert res.simplified_count < res.original_count
