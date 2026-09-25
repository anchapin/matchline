"""Validate the cross-sheet linker on synthetic multi-discipline buildings.

For each building: build the canonical model twice (grid elevation path,
geometric-fallback elevation path) and score every link type against GT:

  fixture->room, sensor->room, diffuser->room, diffuser->zone,
  space->zones (set equality -- the many-to-many check), zone->spaces,
  window->room (per elevation path), plus LPD and window-area rollups
  and a BuildingModel JSON round-trip.
"""

from __future__ import annotations

from typing import Any

from building_model import BuildingModel  # noqa: E402
from link import build_model  # noqa: E402
from synth.multidiscipline import generate_building  # noqa: E402


def _index_model(model):
    """Reverse indexes: fixture->space, sensor->space, diffuser->space,
    diffuser->zone, opening->space."""
    fix2sp, sen2sp, dif2sp, dif2zone, open2sp = {}, {}, {}, {}, {}
    for sid, sp in model.spaces.items():
        for f in sp.lighting.fixtures:
            fix2sp[f.id] = sp.number
        for s in sp.hvac.sensors:
            sen2sp[s.id] = sp.number
        for d in sp.hvac.diffusers:
            dif2sp[d.id] = sp.number
    for zid, z in model.zones.items():
        for d in z.diffusers:
            dif2zone[d.id] = zid
    for sid, sp in model.spaces.items():
        for o in sp.openings:
            # opening id is f"south-{wid}"
            wid = o.id.split("-", 1)[1] if "-" in o.id else o.id
            open2sp[wid] = sp.number
    return fix2sp, sen2sp, dif2sp, dif2zone, open2sp


def score_building(bldg, elevation_key) -> tuple[Any, Any, dict[str, Any]]:
    model, report = build_model(bldg, elevation_key=elevation_key)
    gt = bldg["gt_links"]
    fix2sp, sen2sp, dif2sp, dif2zone, open2sp = _index_model(model)
    lvl = bldg["level_id"]
    rows = {}

    def acc(pred, truth):
        ok = sum(1 for k, v in truth.items() if pred.get(k) == v)
        return ok, len(truth), (ok / len(truth) if truth else 1.0)

    rows["fixture_room"] = acc(fix2sp, gt["fixture_room"])
    rows["sensor_room"] = acc(sen2sp, gt["sensor_room"])
    rows["diffuser_room"] = acc(dif2sp, gt["diffuser_room"])
    # diffuser->zone: model zid "L1-Z1" vs GT "Z1"
    dz_pred = {k: v.split("-", 1)[1] if "-" in v else v for k, v in dif2zone.items()}
    rows["diffuser_zone"] = acc(dz_pred, gt["diffuser_zone"])
    # space->zones set equality (many-to-many)
    zr_ok = zr_tot = 0
    for zid, rnums in gt["zone_rooms"].items():
        mz = model.zones.get(f"{lvl}-{zid}")
        pred = {model.spaces[s].number for s in (mz.space_ids if mz else [])}
        zr_tot += 1
        zr_ok += pred == set(rnums)
    rows["zone_spaces_set"] = (zr_ok, zr_tot, zr_ok / max(1, zr_tot))
    sp_ok = sp_tot = 0
    # invert GT zone_rooms -> room -> zones
    gt_sp_zones = {}
    for zid, rnums in gt["zone_rooms"].items():
        for rn in rnums:
            gt_sp_zones.setdefault(rn, set()).add(zid)
    for sid, sp in model.spaces.items():
        if sp.number in gt_sp_zones:
            sp_tot += 1
            pred = {z.split("-", 1)[1] for z in sp.hvac.zone_ids}
            sp_ok += pred == gt_sp_zones[sp.number]
    rows["space_zones_set"] = (sp_ok, sp_tot, sp_ok / max(1, sp_tot))
    rows["window_room"] = acc(open2sp, gt["window_room"])

    # rollups: LPD and south-facade window area vs GT
    lpd_ok = win_ok = True
    exp_watts = {}
    for f in bldg["fixtures"]:
        rn = gt["fixture_room"][f["id"]]
        w = next(r["watts"] for r in bldg["lighting_schedule"] if r["tag"] == f["tag"])
        exp_watts[rn] = exp_watts.get(rn, 0.0) + w
    for sid, sp in model.spaces.items():
        if abs(sp.lighting.total_w - exp_watts.get(sp.number, 0.0)) > 1e-6:
            lpd_ok = False
    exp_win = {}
    for i, w in enumerate(bldg["south_windows"]):
        se = next(r for r in bldg["window_schedule"] if r["tag"] == w["tag"])
        exp_win[w["room_number"]] = (
            exp_win.get(w["room_number"], 0.0) + se["width_m"] * se["height_m"]
        )
    for sid, sp in model.spaces.items():
        if abs(sp.window_area_m2 - exp_win.get(sp.number, 0.0)) > 1e-6:
            win_ok = False
    rows["lpd_rollup_exact"] = lpd_ok
    rows["window_area_exact"] = win_ok

    # JSON round-trip
    js = model.to_json()
    m2 = BuildingModel.from_json(js)
    rows["json_roundtrip"] = (
        len(m2.spaces) == len(model.spaces)
        and len(m2.zones) == len(model.zones)
        and len(m2.review_queue) == len(model.review_queue)
    )
    return model, report, rows


def main() -> None:
    configs = [(101, False), (102, True), (103, False)]
    all_ok = True
    for seed, span in configs:
        bldg = generate_building(seed, open_office_span=span)
        print(
            f"=== bldg_{seed:03d} span={span}: "
            f"{len(bldg['rooms'])} rooms, {len(bldg['fixtures'])} fixt, "
            f"{len(bldg['components'])} mech comps, "
            f"{len(bldg['south_windows'])} south windows ==="
        )
        for ekey, pname in (("elev_grid", "GRID path"), ("elev_nogrid", "GEOMETRIC path")):
            model, report, rows = score_building(bldg, ekey)
            print(f"  [{pname}]")
            for k, v in rows.items():
                if isinstance(v, tuple):
                    ok, tot, frac = v
                    flag = "" if frac == 1.0 else "  <-- MISMATCH"
                    print(f"    {k:18s} {ok}/{tot} = {frac:.3f}{flag}")
                    all_ok &= frac == 1.0
                else:
                    print(f"    {k:18s} {v}" + ("" if v else "  <-- FAIL"))
                    all_ok &= bool(v)
            rev = [(i.kind, round(i.confidence, 2)) for i in model.review_queue]
            print(f"    review_queue: {len(rev)} items {rev[:6]}{'...' if len(rev) > 6 else ''}")
            mc = report.mean_confidence_by_method
            print(f"    mean window-link confidence: {mc}")
        print()
    print("ALL PASS" if all_ok else "FAILURES PRESENT")


if __name__ == "__main__":
    main()
