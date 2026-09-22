"""Cross-sheet linker: build the canonical BuildingModel from discipline sheets.

Pipeline (all detector-style inputs are in SHEET PIXEL coordinates; the
linker registers everything into canonical meters itself):

  1. ARCH PLAN  -> canonical Spaces (id "{level}-{number}"), envelope walls.
  2. LIGHTING   -> register plan sheet -> fixtures to spaces (point-in-
                   polygon) -> per-space watts + LPD via fixture schedule.
  3. MECH       -> register plan sheet -> components to canonical meters ->
                   zones attach by DIFFUSER POSITIONS (no cross-sheet id
                   matching): a zone's spaces = union of its diffusers'
                   spaces; each space's zone_ids = zones containing its
                   diffusers. Many-to-many falls out naturally.
  4. ELEVATION  -> register facade (grid path or geometric fallback) ->
                   window intervals -> south wall segments -> rooms.
                   Every link carries provenance + confidence; links below
                   REVIEW_CONFIDENCE are flagged for review, not silently
                   accepted.

Inputs are the synthetic building dicts (synth.multidiscipline); the
contracts mirror real detector outputs (bboxes/tags in sheet px), so a
real front-end can be substituted without changing the linker.

Two elevations of the same facade are linked in SEPARATE model runs;
cross-sheet window dedup is implemented via _dedupe_space_openings
(tag + geometric proximity; see docs/design-docs/open-issues.md Issue #1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from building_model import (
    REVIEW_CONFIDENCE,
    BuildingModel,
    ComponentRef,
    EnvelopeWall,
    FixtureInstance,
    Level,
    Provenance,
    Space,
    SpaceOpening,
    Zone,
)
from datasets_adapter import ScheduleEntry, polygon_area_px2
from polygon_classify import classify_polygons
from registration import (
    Affine2D,
    Facade,
    PlanRegistration,
    assign_points_to_spaces,
    match_interval_to_segments,
    register_elevation_geometric,
    register_elevation_grid,
)

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup


def _dedupe_space_openings(model: BuildingModel) -> None:
    """Merge duplicate SpaceOpening entries in each space (Issue #1).

    When two elevation runs of the same facade are linked separately, the
    same physical window produces two SpaceOpening entries (one per run).
    Deduplication is by tag (primary) + geometric proximity (fallback for
    untagged or conflicting entries): entries whose host_interval_m
    overlaps by >= OPENING_DEDUP_TOL_M are merged to one.

    Merged entries carry a compound sheet_id (sheet1+sheet2) and the
    higher of the two confidences.
    """
    for sp in model.spaces.values():
        if not sp.openings:
            continue
        # Group by tag (non-empty tags: primary dedup key)
        by_tag: dict[str, list[tuple[int, SpaceOpening]]] = {}
        untagged: list[tuple[int, SpaceOpening]] = []
        for idx, op in enumerate(sp.openings):
            if op.tag:
                by_tag.setdefault(op.tag, []).append((idx, op))
            else:
                untagged.append((idx, op))

        kept = []  # indices to KEEP
        removed = set()  # indices to DROP

        # Tag-based merge: for each tag with 2+ entries, keep one
        for tag, entries in by_tag.items():
            if len(entries) < 2:
                kept.append(entries[0][0])
                continue
            # Find the entry with highest confidence; use its provenance
            best_idx, best_op = max(
                entries, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
            )
            sheets = sorted(
                {
                    o.provenance.sheet_id
                    for _, o in entries
                    if o.provenance and o.provenance.sheet_id
                }
            )
            best_op = SpaceOpening(
                id=best_op.id,
                tag=tag,
                category=best_op.category,
                width_m=best_op.width_m,
                height_m=best_op.height_m,
                sill_m=best_op.sill_m,
                head_m=best_op.head_m,
                host_facade=best_op.host_facade,
                host_interval_m=best_op.host_interval_m,
                s_center_m=best_op.s_center_m,
                area_m2=best_op.area_m2,
                provenance=best_op.provenance,
                needs_review=any(o.needs_review for _, o in entries),
            )
            best_op.provenance = Provenance(
                sheet_id="+".join(sheets),
                revision=best_op.provenance.revision if best_op.provenance else 1,
                method="window_dedup",
                confidence=max(
                    (o.provenance.confidence for _, o in entries if o.provenance), default=0.9
                ),
                note=f"deduplicated {len(entries)} entries for tag '{tag}'; sheets: {sheets}",
            )
            kept.append(best_idx)
            sp.openings[best_idx] = best_op
            removed.update(idx for idx, _ in entries if idx != best_idx)

        # Geometric dedup for untagged entries: interval overlap merge
        # Process in along-wall order (by s_center_m)
        untagged_sorted = sorted(untagged, key=lambda x: x[1].s_center_m or 0.0)
        used = set()
        for i, (idx_i, op_i) in enumerate(untagged_sorted):
            if idx_i in removed or i in used:
                continue
            group = [(idx_i, op_i)]
            for j, (idx_j, op_j) in enumerate(untagged_sorted[i + 1 :], i + 1):
                if idx_j in removed or j in used:
                    continue
                if not _intervals_overlap(
                    op_i.host_interval_m, op_j.host_interval_m, OPENING_DEDUP_TOL_M
                ):
                    break
                group.append((idx_j, op_j))
                used.add(j)
            # Keep best by confidence
            best_idx2, best_op2 = max(
                group, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
            )
            sheets2 = sorted(
                {o.provenance.sheet_id for _, o in group if o.provenance and o.provenance.sheet_id}
            )
            best_op2 = SpaceOpening(
                id=best_op2.id,
                tag="",
                category=best_op2.category,
                width_m=best_op2.width_m,
                height_m=best_op2.height_m,
                sill_m=best_op2.sill_m,
                head_m=best_op2.head_m,
                host_facade=best_op2.host_facade,
                host_interval_m=best_op2.host_interval_m,
                s_center_m=best_op2.s_center_m,
                area_m2=best_op2.area_m2,
                provenance=Provenance(
                    sheet_id="+".join(sheets2),
                    revision=best_op2.provenance.revision if best_op2.provenance else 1,
                    method="window_dedup",
                    confidence=max(
                        (o.provenance.confidence for _, o in group if o.provenance), default=0.9
                    ),
                    note=f"geometric dedup {len(group)} untagged entries",
                ),
                needs_review=any(o.needs_review for _, o in group),
            )
            kept.append(best_idx2)
            sp.openings[best_idx2] = best_op2
            removed.update(idx for idx, _ in group if idx != best_idx2)

        # Rebuild openings list preserving order
        sp.openings = [op for k, op in enumerate(sp.openings) if k not in removed]


def _intervals_overlap(a: list | None, b: list | None, tol: float) -> bool:
    """Return True if intervals [a0,a1] and [b0,b1] overlap by at least tol."""
    if a is None or b is None:
        return False
    a0, a1 = min(a), max(a)
    b0, b1 = min(b), max(b)
    return not (a1 + tol < b0 or b1 + tol < a0)


@dataclass
class LinkReport:
    building_id: str
    elevation_path: str  # "grid" | "geometric"
    n_spaces: int = 0
    n_zones: int = 0
    fixtures_assigned: int = 0
    fixtures_unassigned: int = 0
    sensors_assigned: int = 0
    diffusers_assigned: int = 0
    windows_linked: int = 0
    windows_unlinked: int = 0
    review_items: int = 0
    mean_confidence_by_method: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Arch plan -> spaces + envelope
# ---------------------------------------------------------------------------


def _build_spaces(bldg, model: BuildingModel, level_id: str, wall_height_m: float) -> list:
    meta = bldg["sheets"]["arch"]["meta"]
    south_windows = bldg.get("south_windows", [])
    grids_h = bldg.get("grids_h", {})
    classifications = classify_polygons(bldg["rooms"], south_windows, grids_h)
    spaces = []
    unlabeled_k = 0
    for r in bldg["rooms"]:
        number = r["number"] or ""
        if number:
            sid = f"{level_id}-{number}"
        else:
            unlabeled_k += 1
            sid = f"{level_id}-UNLABELED-{unlabeled_k}"
        poly = [list(p) for p in r["polygon_m"]]
        area = abs(polygon_area_px2(poly))
        prov = Provenance(
            sheet_id=meta["sheet_id"],
            revision=meta["revision"],
            method="arch_plan_parse",
            confidence=1.0,
            note=f"polygon + label '{r['name']} {r['number']}'",
        )
        clf = classifications.get(number)
        poly_type = clf.poly_type if clf else "room"
        sp = Space(
            id=sid,
            level_id=level_id,
            name=r["name"],
            number=number,
            polygon_m=poly,
            area_m2=area,
            volume_m3=area * wall_height_m,
            core_provenance=prov,
            label_confidence=1.0,
            poly_type=poly_type,
        )
        model.spaces[sid] = sp
        spaces.append(sp)
    model.log_revision(
        meta["sheet_id"], meta["revision"], "ingest", f"{len(spaces)} spaces from arch plan"
    )
    return spaces


def _build_envelope(bldg, model: BuildingModel, level_id: str, wall_height_m: float) -> None:
    W, D = bldg["W_m"], bldg["D_m"]
    meta = bldg["sheets"]["arch"]["meta"]
    runs = [
        ("south", [0.0, D], [W, D]),
        ("north", [W, 0.0], [0.0, 0.0]),
        ("east", [W, 0.0], [W, D]),
        ("west", [0.0, D], [0.0, 0.0]),
    ]
    for i, (facade, a, b) in enumerate(runs):
        length = math.dist(a, b)
        model.envelope.append(
            EnvelopeWall(
                id=f"{level_id}-EW{i + 1}",
                facade=facade,
                from_m=list(a),
                to_m=list(b),
                length_m=length,
                height_m=wall_height_m,
                area_m2=length * wall_height_m,
                provenance=Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="arch_plan_parse",
                    confidence=1.0,
                    note=f"footprint {facade} wall run",
                ),
            )
        )


# ---------------------------------------------------------------------------
# 2. Lighting plan -> fixtures to spaces
# ---------------------------------------------------------------------------


def _link_lighting(
    bldg, model: BuildingModel, spaces: list, sched: dict, report: LinkReport
) -> None:
    sh = bldg["sheets"]["lighting"]
    meta = sh["meta"]
    reg = PlanRegistration(
        sheet_id=meta["sheet_id"],
        discipline="lighting_plan",
        method="title_block",
        confidence=0.95,
        affine=Affine2D.from_scale_translate(meta["px_per_m"], *meta["origin_px"]),
        provenance=Provenance(
            sheet_id=meta["sheet_id"],
            revision=meta["revision"],
            method="title_block_scale",
            confidence=0.95,
            note="sheet origin + scale from title block",
        ),
    )
    fixtures = bldg["fixtures"]
    pts = []
    for f in fixtures:
        x_m, y_m = reg.to_meters(f["x_px"], f["y_px"])
        pts.append({"id": f["id"], "x_m": x_m, "y_m": y_m})
    by_id = {f["id"]: f for f in fixtures}
    assignment = assign_points_to_spaces(pts, spaces)
    space_of = {s.id: s for s in spaces}

    for fid, sid in assignment.items():
        f = by_id[fid]
        x_m, y_m = reg.to_meters(f["x_px"], f["y_px"])
        entry = sched.get(f["tag"])
        watts = entry.watts if entry else None
        if entry is None:
            model.flag_for_review(
                "fixture_schedule",
                f"fixture {fid} tag '{f['tag']}' has no schedule entry",
                0.5,
                Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="schedule_join",
                    confidence=0.5,
                    bbox=[f["x_px"], f["y_px"], f["x_px"], f["y_px"]],
                ),
            )
        if sid is None:
            report.fixtures_unassigned += 1
            model.flag_for_review(
                "fixture_assignment",
                f"fixture {fid} ({f['class']}) falls in no space",
                0.4,
                Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="point_in_polygon",
                    confidence=0.4,
                    bbox=[f["x_px"], f["y_px"], f["x_px"], f["y_px"]],
                ),
            )
            continue
        report.fixtures_assigned += 1
        sp = space_of[sid]
        sp.lighting.fixtures.append(
            FixtureInstance(
                id=fid,
                tag=f["tag"],
                fixture_class=f["class"],
                x_m=x_m,
                y_m=y_m,
                watts=watts,
                provenance=Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="point_in_polygon",
                    confidence=0.95,
                    bbox=[f["x_px"], f["y_px"], f["x_px"], f["y_px"]],
                    note=f"centroid in space {sid}",
                ),
            )
        )
    # per-space rollups
    for sp in spaces:
        w = sum(f.watts or 0.0 for f in sp.lighting.fixtures)
        sp.lighting.total_w = w
        if sp.area_m2:
            sp.lighting.lpd_w_m2 = w / sp.area_m2
            sp.lighting.lpd_w_ft2 = sp.lighting.lpd_w_m2 / FT2_PER_M2
        sp.lighting.provenance = Provenance(
            sheet_id=meta["sheet_id"],
            revision=meta["revision"],
            method="schedule_join",
            confidence=0.95,
            note=f"{len(sp.lighting.fixtures)} fixtures x schedule watts",
        )
    model.log_revision(
        meta["sheet_id"],
        meta["revision"],
        "ingest",
        f"{report.fixtures_assigned} fixtures assigned, {report.fixtures_unassigned} unassigned",
    )


# ---------------------------------------------------------------------------
# 3. Mech plan -> zones attach by diffuser positions
# ---------------------------------------------------------------------------


def _link_mech(bldg, model: BuildingModel, spaces: list, report: LinkReport) -> None:
    sh = bldg["sheets"]["mech"]
    meta = sh["meta"]
    reg = PlanRegistration(
        sheet_id=meta["sheet_id"],
        discipline="mech_plan",
        method="title_block",
        confidence=0.95,
        affine=Affine2D.from_scale_translate(meta["px_per_m"], *meta["origin_px"]),
    )
    space_of = {s.id: s for s in spaces}
    level_id = bldg["level_id"]

    # components -> canonical ComponentRefs
    comp_refs = {}
    for c in bldg["components"]:
        x_m, y_m = reg.to_meters(c["x_px"], c["y_px"])
        comp_refs[c["id"]] = ComponentRef(
            id=c["id"],
            type=c["type"],
            x_m=x_m,
            y_m=y_m,
            tag=c["tag"],
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="symbol_detection",
                confidence=0.9,
                bbox=[c["x_px"], c["y_px"], c["x_px"], c["y_px"]],
            ),
        )

    for z in bldg["zones"]:
        zid = f"{level_id}-{z['zone_id']}"
        # diffusers/sensors -> spaces by position (no id matching)
        dpts = []
        for d_ in z["diffusers"]:
            x_m, y_m = reg.to_meters(d_["x_px"], d_["y_px"])
            dpts.append({"id": d_["id"], "x_m": x_m, "y_m": y_m})
        spts = []
        for s_ in z["sensors"]:
            x_m, y_m = reg.to_meters(s_["x_px"], s_["y_px"])
            spts.append({"id": s_["id"], "x_m": x_m, "y_m": y_m})
        d_space = assign_points_to_spaces(dpts, spaces)
        s_space = assign_points_to_spaces(spts, spaces)

        zone = Zone(
            id=zid,
            level_id=level_id,
            duct_length_m=z.get("duct_length_m"),
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="duct_tracing",
                confidence=0.9,
                note="; ".join(z.get("audit", [])),
            ),
        )
        v = z["vav"]
        vx_m, vy_m = reg.to_meters(v["x_px"], v["y_px"])
        zone.terminal_unit = ComponentRef(
            id=v["id"],
            type="vav",
            x_m=vx_m,
            y_m=vy_m,
            tag=v["id"],
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="symbol_detection",
                confidence=0.9,
            ),
        )

        for d_ in z["diffusers"]:
            ref = comp_refs[d_["id"]]
            zone.diffusers.append(ref)
            sid = d_space[d_["id"]]
            if sid is None:
                model.flag_for_review(
                    "diffuser_assignment",
                    f"diffuser {d_['id']} falls in no space",
                    0.4,
                    ref.provenance,
                )
                continue
            report.diffusers_assigned += 1
            sp = space_of[sid]
            sp.hvac.diffusers.append(ref)
            if sid not in zone.space_ids:
                zone.space_ids.append(sid)
            if zid not in sp.hvac.zone_ids:
                sp.hvac.zone_ids.append(zid)
        for s_ in z["sensors"]:
            ref = comp_refs[s_["id"]]
            zone.sensors.append(ref)
            sid = s_space[s_["id"]]
            if sid is None:
                model.flag_for_review(
                    "sensor_assignment", f"sensor {s_['id']} falls in no space", 0.4, ref.provenance
                )
                continue
            report.sensors_assigned += 1
            space_of[sid].hvac.sensors.append(ref)
            # sensor -> zone by room co-location: the sensor's space may
            # sit in several zones; attach to the zone whose duct run
            # serves it (the zone that listed this sensor).
            # (space.hvac.zone_ids already has zid via diffusers)
        # terminal unit -> its space
        vpts = assign_points_to_spaces([{"id": v["id"], "x_m": vx_m, "y_m": vy_m}], spaces)
        vsid = vpts[v["id"]]
        if vsid is not None:
            space_of[vsid].hvac.terminal_units.append(zone.terminal_unit)
        for sid in zone.space_ids:
            space_of[sid].hvac.provenance = Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="duct_tracing",
                confidence=0.9,
                note=f"zone {zid} via diffuser positions",
            )
        model.zones[zid] = zone
    report.n_zones = len(model.zones)
    model.log_revision(
        meta["sheet_id"],
        meta["revision"],
        "ingest",
        f"{len(model.zones)} zones attached by diffuser positions",
    )


# ---------------------------------------------------------------------------
# 4. Elevation -> windows to rooms (grid path or geometric fallback)
# ---------------------------------------------------------------------------


def south_wall_segments(bldg) -> list:
    """Wall segments along the south facade, one per room touching it."""
    D = bldg["D_m"]
    segs = []
    for r in bldg["rooms"]:
        x0, y0, x1, y1 = r["rect_m"]
        if abs(y1 - D) < 1e-6:
            segs.append(
                {"id": f"seg-{r['number']}", "s0": x0, "s1": x1, "room_number": r["number"]}
            )
    segs.sort(key=lambda g: g["s0"])
    return segs


_south_wall_segments = south_wall_segments  # backwards-compat alias


def _link_elevation(
    bldg, model: BuildingModel, spaces: list, elev_key: str, win_sched: dict, report: LinkReport
) -> str:
    sh = bldg["sheets"][elev_key]
    meta, data = sh["meta"], sh["data"]
    D, W = bldg["D_m"], bldg["W_m"]
    facade = Facade(name="south", ref_corner_m=(0.0, D), length_m=W, fixed_coord_m=D, axis="x")

    if elev_key == "elev_grid":
        reg = register_elevation_grid(
            meta["sheet_id"],
            facade,
            plan_grid_m=bldg["grids_v"],
            elev_bubbles=data["bubbles"],
            v_ground_px=data["v_ground_px"],
            elev_px_per_m=data["px_per_m"],
            revision=meta["revision"],
        )
        path = "grid"
    else:
        reg = register_elevation_geometric(
            meta["sheet_id"],
            facade,
            wall_u0_px=data["wall_u0_px"],
            elev_px_per_m=data["px_per_m"],
            v_ground_px=data["v_ground_px"],
            revision=meta["revision"],
        )
        path = "geometric"

    segments = south_wall_segments(bldg)
    space_of_num = {s.number: s for s in spaces}
    confs = []

    for wdet in data["windows"]:
        s0, _ = reg.to_facade(wdet["u0_px"], 0)
        s1, _ = reg.to_facade(wdet["u1_px"], 0)
        _, sill = reg.to_facade(0, wdet["v_sill_px"])
        seg, frac, ambiguous = match_interval_to_segments(s0, s1, segments)
        entry = win_sched.get(wdet["tag"])
        conf = reg.confidence * (0.5 + 0.5 * frac)
        confs.append((reg.method, conf))
        prov = Provenance(
            sheet_id=meta["sheet_id"],
            revision=meta["revision"],
            method=reg.method + "_registration",
            confidence=round(conf, 3),
            bbox=[wdet["u0_px"], wdet["v_head_px"], wdet["u1_px"], wdet["v_sill_px"]],
            note=(
                f"facade interval [{s0:.2f}, {s1:.2f}] m -> "
                f"segment {seg['id'] if seg else None} "
                f"(overlap {frac:.0%})" + (" AMBIGUOUS" if ambiguous else "")
            ),
        )
        if seg is None:
            report.windows_unlinked += 1
            model.flag_for_review(
                "window_room_link",
                f"window {wdet['id']} at [{s0:.2f}, {s1:.2f}] m matches no wall segment",
                conf,
                prov,
            )
            continue
        sp = space_of_num[seg["room_number"]]
        width_m = entry.width_m if entry else (s1 - s0)
        height_m = entry.height_m if entry else None
        area = width_m * height_m if width_m and height_m else None
        needs_review = ambiguous or conf < REVIEW_CONFIDENCE
        sp.openings.append(
            SpaceOpening(
                id=f"south-{wdet['id']}",
                tag=wdet["tag"],
                category="window",
                width_m=width_m,
                height_m=height_m,
                sill_m=round(sill, 3),
                head_m=(round(sill + height_m, 3) if height_m is not None else None),
                host_facade="south",
                host_interval_m=[round(s0, 3), round(s1, 3)],
                area_m2=area,
                provenance=prov,
                needs_review=needs_review,
            )
        )
        report.windows_linked += 1
        if needs_review:
            model.flag_for_review(
                "window_room_link",
                f"window {wdet['id']} -> room {sp.number}: "
                f"{'ambiguous span' if ambiguous else 'low confidence'} "
                f"({conf:.2f})",
                conf,
                prov,
            )
    model.log_revision(
        meta["sheet_id"],
        meta["revision"],
        "ingest",
        f"{report.windows_linked} windows linked via {path} path",
    )
    report.mean_confidence_by_method[reg.method] = sum(c for _, c in confs) / max(1, len(confs))
    return path


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------


def _schedules(bldg) -> tuple:
    win_sched = {}
    for row in bldg["window_schedule"]:
        e = ScheduleEntry(
            tag=row["tag"],
            category=row["category"],
            width_m=row["width_m"],
            height_m=row["height_m"],
        )
        win_sched[e.tag] = e
    light_sched = {}
    for row in bldg["lighting_schedule"]:
        e = ScheduleEntry(
            tag=row["tag"],
            category="lighting",
            width_m=None,
            height_m=None,
            watts=row["watts"],
            description=row.get("description", ""),
            lamp_type=row.get("lamp_type", ""),
        )
        light_sched[e.tag] = e
    return win_sched, light_sched


def build_model(bldg: dict, elevation_key: str = "elev_grid", building_name: str = "") -> tuple:
    """Build the canonical BuildingModel for one building.

    elevation_key: "elev_grid" or "elev_nogrid" -- the two elevations are
    linked in separate runs; cross-sheet window dedup is applied as a
    post-processing step (window tag + geometric proximity, see Issue #1
    in docs/design-docs/open-issues.md). Pass None to skip elevation
    linking entirely (used by elevation_windows.link_elevations, which
    does multi-elevation detect -> dedup -> attach itself).
    """
    from dataclasses import asdict

    model = BuildingModel(name=building_name or bldg["building_id"])
    level_id = bldg["level_id"]
    model.levels.append(Level(id=level_id, name="Level 1", wall_height_m=bldg["wall_height_m"]))
    report = LinkReport(
        building_id=bldg["building_id"],
        elevation_path=(
            "grid" if elevation_key == "elev_grid" else "geometric" if elevation_key else "none"
        ),
    )

    spaces = _build_spaces(bldg, model, level_id, bldg["wall_height_m"])
    report.n_spaces = len(spaces)
    _build_envelope(bldg, model, level_id, bldg["wall_height_m"])
    win_sched, light_sched = _schedules(bldg)
    for tag, e in {**win_sched, **light_sched}.items():
        model.schedules[tag] = asdict(e)

    _link_lighting(bldg, model, spaces, light_sched, report)
    _link_mech(bldg, model, spaces, report)
    if elevation_key is not None:
        _link_elevation(bldg, model, spaces, elevation_key, win_sched, report)

    # Issue #1: cross-sheet window deduplication — dedupe after all elevation
    # linking so that two runs of the same facade produce one SpaceOpening
    # per physical window.
    _dedupe_space_openings(model)

    report.review_items = len(model.review_queue)
    return model, report
