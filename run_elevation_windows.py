"""Validate exact window positioning from elevations.

For each synthetic building:
  1. Detection: contour detector on both elevation rasters vs GT
     (along-wall center/width, sill/head errors in meters; recall).
  2. Tag association: size-matched tag vs GT tag.
  3. Dedup: merged count == GT window count across the two elevations
     (drawn at different px/m scales); merged position error vs GT.
  4. Attach: merged window -> room vs GT window_room.
  5. Reconciliation: clean run -> 0 flags; injected mismatches ->
     flagged in the review queue.
  6. Daylighting: zone polygons inside the right room, depths ==
     min(factor*H, room depth), lateral extents as specified.
  7. JSON round-trip of the extended model.

Run with the venv python (needs cv2):
    ~/workspace/.venv-ocr/bin/python run_elevation_windows.py
"""

from __future__ import annotations

import math
from typing import Any

from building_model import BuildingModel  # noqa: E402
from elevation_windows import (  # noqa: E402
    DaylightParams,
    assign_window_tag,
    build_model_with_elevations,
    detect_windows,
    merge_elevation_observations,
    observations_to_facade,
    reconcile_window_counts,
)
from link._schedules import _schedules  # noqa: E402
from registration import (  # noqa: E402
    Facade,
    point_in_polygon,
    register_elevation_geometric,
    register_elevation_grid,
)
from synth.multidiscipline import SILL_M, generate_building  # noqa: E402
from synth.sheets import SCHED_BY_TAG  # noqa: E402

SILL = SILL_M


def _gt_windows_m(bldg):
    """GT windows in facade meters: {wid: dict}."""
    out = {}
    for i, w in enumerate(bldg["south_windows"]):
        h = SCHED_BY_TAG[w["tag"]]["height_m"]
        out[f"W{i + 1}"] = {
            "s0": w["s0_m"],
            "s1": w["s1_m"],
            "center": (w["s0_m"] + w["s1_m"]) / 2,
            "width": w["s1_m"] - w["s0_m"],
            "sill": SILL,
            "head": SILL + h,
            "tag": w["tag"],
            "room": w["room_number"],
        }
    return out


def _register(bldg, key):
    sh = bldg["sheets"][key]
    meta, data = sh["meta"], sh["data"]
    D, W = bldg["D_m"], bldg["W_m"]
    facade = Facade(name="south", ref_corner_m=(0.0, D), length_m=W, fixed_coord_m=D, axis="x")
    if data.get("bubbles"):
        reg = register_elevation_grid(
            meta["sheet_id"],
            facade,
            plan_grid_m=bldg["grids_v"],
            elev_bubbles=data["bubbles"],
            v_ground_px=data["v_ground_px"],
            elev_px_per_m=data["px_per_m"],
            revision=meta["revision"],
        )
    else:
        reg = register_elevation_geometric(
            meta["sheet_id"],
            facade,
            wall_u0_px=data["wall_u0_px"],
            elev_px_per_m=data["px_per_m"],
            v_ground_px=data["v_ground_px"],
            revision=meta["revision"],
        )
    return reg


def score_detection(bldg) -> dict[str, dict[str, Any]]:
    """Per-elevation detection error vs GT (meters)."""
    gt = _gt_windows_m(bldg)
    rows = {}
    for key in ("elev_grid", "elev_nogrid"):
        sh = bldg["sheets"][key]
        meta, data = sh["meta"], sh["data"]
        obs = detect_windows(sh["image"], data["px_per_m"], meta["sheet_id"], meta["revision"])
        reg = _register(bldg, key)
        fw = observations_to_facade(obs, reg, "south")
        # match each GT window to nearest detected center
        errs, tag_ok, matched = [], 0, 0
        used = set()
        for wid, g in gt.items():
            best, bj = None, -1
            for j, f in enumerate(fw):
                if j in used:
                    continue
                dc = abs(f.s_center_m - g["center"])
                if best is None or dc < best:
                    best, bj = dc, j
            if bj < 0 or best > 0.5:
                continue  # missed
            used.add(bj)
            f = fw[bj]
            matched += 1
            errs.append(
                (
                    abs(f.s_center_m - g["center"]),
                    abs(f.width_m - g["width"]),
                    abs(f.sill_m - g["sill"]),
                    abs(f.head_m - g["head"]),
                )
            )
            win_sched, _ = _schedules(bldg)
            tag_ok += assign_window_tag(f.width_m, f.height_m, win_sched) == g["tag"]
        fp = len(fw) - matched
        rows[key] = {
            "detected": len(obs),
            "gt": len(gt),
            "matched": matched,
            "fp": fp,
            "tag_ok": tag_ok,
            "errs": errs,
        }
    return rows


def _dist_to_poly(pt, poly):
    dmin = float("inf")
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        abx, aby = bx - ax, by - ay
        t = ((pt[0] - ax) * abx + (pt[1] - ay) * aby) / max(abx**2 + aby**2, 1e-12)
        t = max(0.0, min(1.0, t))
        dx, dy = pt[0] - (ax + t * abx), pt[1] - (ay + t * aby)
        dmin = min(dmin, math.hypot(dx, dy))
    return dmin


def check_daylighting(model, bldg, params: DaylightParams) -> tuple[int, int, list[str]]:
    """Zone polygons inside the right room; depths per the params."""
    D = bldg["D_m"]
    bad = []
    n_p = n_s = 0
    for sid, sp in model.spaces.items():
        poly = [list(p) for p in sp.polygon_m]
        for z in list(sp.daylight.primary) + list(sp.daylight.secondary):
            n_p += z.zone_class == "primary"
            n_s += z.zone_class == "secondary"
            for v in z.polygon_m:
                # vertices may lie exactly ON the room boundary (the
                # wall edge); allow 2 mm tolerance
                if not (point_in_polygon(v, poly) or _dist_to_poly(v, poly) < 0.002):
                    bad.append(f"{z.id}: vertex {v} outside room {sid}")
                    break
            # depth check: max inward distance from the south wall
            o = next((x for x in sp.openings if x.id == z.window_id), None)
            if o is None:
                bad.append(f"{z.id}: no matching opening")
                continue
            H = z.head_height_m
            depths = [D - v[1] for v in z.polygon_m]
            if z.zone_class == "primary":
                exp = min(params.primary_depth_factor * H, max(D - v[1] for v in poly))
            else:
                exp = min(params.secondary_depth_factor * H, max(D - v[1] for v in poly))
            if abs(max(depths) - exp) > 0.06:
                bad.append(f"{z.id}: max depth {max(depths):.2f} != expected {exp:.2f}")
    return n_p, n_s, bad


def test_reconciliation_flags() -> None:
    """Injected mismatches must land in the review queue."""
    from building_model import BuildingModel as BM

    model = BM(name="recon_test")
    plan_counts = {"A": 3, "B": 2}
    # elevation sees: one extra A, one missing B, one untagged
    from elevation_windows import MergedWindow

    merged = [
        MergedWindow(
            id="MW-1",
            facade="south",
            s0_m=1,
            s1_m=2.2,
            s_center_m=1.6,
            width_m=1.2,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag="A",
            category_hint="window",
        ),
        MergedWindow(
            id="MW-2",
            facade="south",
            s0_m=3,
            s1_m=4.2,
            s_center_m=3.6,
            width_m=1.2,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag="A",
            category_hint="window",
        ),
        MergedWindow(
            id="MW-3",
            facade="south",
            s0_m=5,
            s1_m=6.2,
            s_center_m=5.6,
            width_m=1.2,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag="A",
            category_hint="window",
        ),
        MergedWindow(
            id="MW-4",
            facade="south",
            s0_m=7,
            s1_m=8.2,
            s_center_m=7.6,
            width_m=1.2,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag="A",
            category_hint="window",
        ),
        MergedWindow(
            id="MW-5",
            facade="south",
            s0_m=9,
            s1_m=10.8,
            s_center_m=9.9,
            width_m=1.8,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag="B",
            category_hint="window",
        ),
        MergedWindow(
            id="MW-6",
            facade="south",
            s0_m=11,
            s1_m=12.0,
            s_center_m=11.5,
            width_m=1.0,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            tag=None,
            category_hint="window",
        ),
    ]
    flags = reconcile_window_counts(model, plan_counts, merged, "south", "arch_A101")
    kinds = [i.kind for i in model.review_queue]
    ok = (
        len(flags) == 3
        and kinds.count("window_reconciliation") == 3
        and any(
            "1 'B'" in f["description"] and "no elevation instance" in f["description"]
            for f in flags
        )
        and any(
            "'A'" in f["description"] and "no matching arch-plan" in f["description"] for f in flags
        )
        and any("UNTAGGED" in f["description"] for f in flags)
    )
    return ok, [f["description"] for f in flags]


def test_dedup_conflict() -> bool:
    """Overlapping-but-disagreeing observations -> conflict, not merge."""
    from building_model import Provenance
    from elevation_windows import FacadeWindow

    a = FacadeWindow(
        id="s1:EW-1",
        sheet_id="s1",
        facade="south",
        s0_m=5.0,
        s1_m=6.2,
        s_center_m=5.6,
        width_m=1.2,
        sill_m=0.9,
        head_m=2.4,
        height_m=1.5,
        category_hint="window",
        confidence=0.8,
        provenance=Provenance(sheet_id="s1", revision=1, method="x", confidence=0.8),
    )
    b = FacadeWindow(
        id="s2:EW-1",
        sheet_id="s2",
        facade="south",
        s0_m=5.5,
        s1_m=6.7,
        s_center_m=6.1,
        width_m=1.2,
        sill_m=0.9,
        head_m=2.4,
        height_m=1.5,
        category_hint="window",
        confidence=0.8,
        provenance=Provenance(sheet_id="s2", revision=1, method="x", confidence=0.8),
    )
    merged, conflicts = merge_elevation_observations([[a], [b]], tol_m=0.30)
    # centers differ by 0.5 > tol, intervals overlap -> conflict
    return len(merged) == 2 and len(conflicts) == 1 and conflicts[0]["delta_center_m"] == 0.5


def main() -> None:
    params = DaylightParams()
    all_ok = True
    for seed, span in [(201, False), (202, True), (203, False)]:
        bldg = generate_building(seed, open_office_span=span)
        gt = _gt_windows_m(bldg)
        print(f"=== bldg_{seed:03d} span={span}: {len(gt)} GT windows ===")

        det = score_detection(bldg)
        for key, r in det.items():
            errs = r["errs"]
            if errs:
                mc = sum(e[0] for e in errs) / len(errs)
                mx = max(e[0] for e in errs)
                mw = sum(e[1] for e in errs) / len(errs)
                ms = sum(e[2] for e in errs) / len(errs)
                mh = sum(e[3] for e in errs) / len(errs)
            else:
                mc = mx = mw = ms = mh = float("nan")
            ok = r["matched"] == r["gt"] and r["fp"] == 0 and r["tag_ok"] == r["gt"]
            all_ok &= ok
            print(
                f"  [{key}] detected {r['detected']}/{r['gt']} GT, "
                f"matched {r['matched']}, FP {r['fp']}, "
                f"tag_ok {r['tag_ok']}/{r['gt']}"
            )
            print(
                f"    center err mean {mc * 100:.1f} cm max {mx * 100:.1f} cm; "
                f"width {mw * 100:.1f} cm; sill {ms * 100:.1f} cm; "
                f"head {mh * 100:.1f} cm   {'OK' if ok else '<-- FAIL'}"
            )

        model, report = build_model_with_elevations(bldg, daylight_params=params)
        print(
            f"  [dedup] merged {report.n_merged} (GT {len(gt)}), "
            f"conflicts {report.n_conflicts}, attached "
            f"{report.n_attached}, unlinked {report.n_unlinked}, "
            f"reconcile_flags {report.n_reconcile_flags}"
        )
        ok = (
            report.n_merged == len(gt)
            and report.n_conflicts == 0
            and report.n_attached == len(gt)
            and report.n_reconcile_flags == 0
        )
        all_ok &= ok
        print(f"    {'OK' if ok else '<-- FAIL'}")

        # merged position error vs GT
        errs = []
        for sid, sp in model.spaces.items():
            for o in sp.openings:
                g = next(
                    (
                        v
                        for v in gt.values()
                        if v["room"] == sp.number and abs(v["center"] - o.s_center_m) < 0.5
                    ),
                    None,
                )
                if g:
                    errs.append(abs(g["center"] - o.s_center_m))
        if errs:
            print(
                f"  [attach] {len(errs)}/{len(gt)} openings matched to GT "
                f"rooms; center err mean "
                f"{sum(errs) / len(errs) * 100:.1f} cm max "
                f"{max(errs) * 100:.1f} cm"
            )
            all_ok &= len(errs) == len(gt)

        # exact placement fields present?
        placed = [
            o
            for sp in model.spaces.values()
            for o in sp.openings
            if o.s_center_m is not None and o.head_m is not None
        ]
        print(f"  [placement] {len(placed)}/{len(gt)} openings carry s_center_m + head_m")
        all_ok &= len(placed) == len(gt)

        n_p, n_s, bad = check_daylighting(model, bldg, params)
        print(
            f"  [daylight] primary zones {n_p}, secondary zones {n_s}; "
            f"geometry violations: {len(bad)}"
        )
        for b in bad[:5]:
            print(f"    <-- {b}")
        all_ok &= not bad

        js = model.to_json()
        m2 = BuildingModel.from_json(js)
        rt = len(m2.spaces) == len(model.spaces) and all(
            len(m2.spaces[s].daylight.primary) == len(model.spaces[s].daylight.primary)
            for s in model.spaces
        )
        print(f"  [json round-trip] {rt}")
        all_ok &= rt
        print()

    ok, descs = test_reconciliation_flags()
    print(f"[reconciliation injection test] {'OK' if ok else '<-- FAIL'}")
    for d in descs:
        print(f"    - {d}")
    all_ok &= ok
    okc = test_dedup_conflict()
    print(f"[dedup conflict test] {'OK' if okc else '<-- FAIL'}")
    all_ok &= okc
    print()
    print("ALL PASS" if all_ok else "FAILURES PRESENT")


if __name__ == "__main__":
    main()
