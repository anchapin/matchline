from __future__ import annotations

from typing import TYPE_CHECKING

from building_model import (
    REVIEW_CONFIDENCE,
    BuildingModel,
    Provenance,
    SpaceOpening,
    SymbolLinkage,
)
from registration import (
    Facade,
    match_interval_to_segments,
    register_elevation_geometric,
    register_elevation_grid,
)

if TYPE_CHECKING:
    from link._report import LinkReport

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Elevation-based zone association."""


_FACE_TOL_M = 1e-3

# A detection whose sill sits at grade is a door, not a window. The rule is
# the one ``elevation_windows.py`` uses to derive its ``category_hint``
# (``sill < 0.15`` -> "door"), applied here to the sill the link layer
# already computes.
DOOR_SILL_MAX_M = 0.15


def facade_from_meta(meta: dict, D: float, W: float) -> Facade:
    """Facade for an elevation sheet; meta keys override per-facade defaults.

    Defaults match ``elevation_windows._facade_frame`` (y-down meters):
    south y=D ref (0, D); north y=0 ref (0, 0); east x=W ref (W, 0);
    west x=0 ref (0, 0). Length is W for south/north, D for east/west.
    """
    name = meta.get("facade", "south")
    defaults = {
        "south": ((0.0, D), D, W, "x"),
        "north": ((0.0, 0.0), 0.0, W, "x"),
        "east": ((W, 0.0), W, D, "y"),
        "west": ((0.0, 0.0), 0.0, D, "y"),
    }
    if name not in defaults:
        raise ValueError(f"unknown facade {name!r}")
    ref, fixed, length, axis = defaults[name]
    return Facade(
        name=name,
        ref_corner_m=tuple(meta.get("facade_ref_corner_m", ref)),
        length_m=meta.get("facade_length_m", length),
        fixed_coord_m=fixed,
        axis=axis,
    )


def _room_outline(r: dict) -> list:
    """Room outline vertices: ``polygon_m`` when given, else the ``rect_m`` box."""
    if r.get("polygon_m"):
        return [tuple(v) for v in r["polygon_m"]]
    x0, y0, x1, y1 = r["rect_m"]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def wall_segments(bldg, facade: Facade) -> list:
    """Wall segments on one facade: every room edge lying on the facade line.

    Intervals are facade meters from the facade's reference corner, so they
    compare directly with elevation-registered window intervals.
    """
    fixed_i, along_i = (1, 0) if facade.axis == "x" else (0, 1)
    ref_along = facade.ref_corner_m[along_i]
    segs = []
    for r in bldg["rooms"]:
        pts = _room_outline(r)
        hits = []
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if (
                abs(a[fixed_i] - facade.fixed_coord_m) < _FACE_TOL_M
                and abs(b[fixed_i] - facade.fixed_coord_m) < _FACE_TOL_M
            ):
                s0, s1 = sorted((a[along_i] - ref_along, b[along_i] - ref_along))
                if s1 - s0 > _FACE_TOL_M:
                    hits.append((s0, s1))
        base = (
            f"seg-{r['number']}" if facade.name == "south" else f"seg-{facade.name}-{r['number']}"
        )
        for k, (s0, s1) in enumerate(sorted(hits)):
            segs.append(
                {
                    "id": base if k == 0 else f"{base}-{k}",
                    "s0": s0,
                    "s1": s1,
                    "room_number": r["number"],
                }
            )
    segs.sort(key=lambda g: g["s0"])
    return segs


def facade_grid_map(bldg, facade: Facade) -> dict:
    """Plan grid labels -> facade metres (s from the facade's reference corner).

    The plan's vertical grids (``grids_v``) run along x, so they tie an
    x-axis facade (south/north); the horizontal grids (``grids_h``) run
    along y and tie a y-axis facade (east/west). Facade metres are measured
    from the facade's reference corner, exactly like ``wall_segments``, so
    grid-fitted and geometrically-fitted intervals are comparable.
    """
    raw = bldg.get("grids_v") if facade.axis == "x" else bldg.get("grids_h")
    along_i = 0 if facade.axis == "x" else 1
    ref_along = facade.ref_corner_m[along_i]
    return {label: float(v) - ref_along for label, v in (raw or {}).items()}


def elevation_path_for(meta: dict, data: dict) -> str:
    """Registration path for one elevation sheet.

    Chosen from the SHEET, not from its key name: a sheet that carries grid
    bubbles ties to the plan grids (high confidence); one without falls back
    to the geometric corner assumption (low confidence, flagged for review).
    """
    return "grid" if (data.get("bubbles") or []) else "geometric"


def south_wall_segments(bldg) -> list:
    """Wall segments along the south facade, one per room edge touching it."""
    return wall_segments(bldg, facade_from_meta({}, bldg["D_m"], bldg["W_m"]))


_south_wall_segments = south_wall_segments  # backwards-compat alias


def _link_elevation(
    bldg, model: BuildingModel, spaces: list, elev_key: str, win_sched: dict, report: LinkReport
) -> str:
    sh = bldg["sheets"][elev_key]
    meta, data = sh["meta"], sh["data"]
    D, W = bldg["D_m"], bldg["W_m"]
    facade = facade_from_meta(meta, D, W)

    # The path follows the SHEET, not its key name: any facade's sheet with
    # grid bubbles ties to the plan grids (high confidence); one without
    # falls back to the geometric corner assumption (#1310).
    path = elevation_path_for(meta, data)
    if path == "grid":
        try:
            reg = register_elevation_grid(
                meta["sheet_id"],
                facade,
                plan_grid_m=facade_grid_map(bldg, facade),
                elev_bubbles=data["bubbles"],
                v_ground_px=data["v_ground_px"],
                elev_px_per_m=data["px_per_m"],
                revision=meta["revision"],
            )
        except (KeyError, ValueError):
            # Bubbles present but they do not tie to the plan grids (or the
            # sheet lacks the grid scale): fall back to the geometric path
            # rather than dropping the sheet.
            reg = None
    else:
        reg = None
    if reg is None:
        reg = register_elevation_geometric(
            meta["sheet_id"],
            facade,
            wall_u0_px=data["wall_u0_px"],
            elev_px_per_m=data["px_per_m"],
            v_ground_px=data["v_ground_px"],
            revision=meta["revision"],
            mirrored=meta.get("elevation_mirrored"),
        )
        path = "geometric"

    segments = wall_segments(bldg, facade)
    space_of_num = {s.number: s for s in spaces}
    confs = []

    for wdet in data["windows"]:
        s0, _ = reg.to_facade(wdet["u0_px"], 0)
        s1, _ = reg.to_facade(wdet["u1_px"], 0)
        s0, s1 = min(s0, s1), max(s0, s1)  # mirrored sheets run right to left
        _, sill = reg.to_facade(0, wdet["v_sill_px"])
        _, head = reg.to_facade(0, wdet["v_head_px"])
        # A detection at grade is a door: the same sill rule the elevation
        # pipeline uses for its category_hint (elevation_windows.py).
        category = "door" if sill < DOOR_SILL_MAX_M else "window"
        seg, frac, ambiguous = match_interval_to_segments(s0, s1, segments)
        entry = win_sched.get(wdet["tag"])
        # A schedule row whose category disagrees with the sill-derived one
        # does not describe this element: the link is kept (sill wins) but the
        # mismatch is recorded, never swallowed.
        sched_category = getattr(entry, "category", None) if entry else None
        mismatch = entry is not None and sched_category != category
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
        if mismatch:
            prov.note += (
                f"; CATEGORY MISMATCH: sill {sill:.2f} m -> {category}, "
                f"schedule row '{wdet['tag']}' is a {sched_category}"
            )
        if seg is None:
            if category == "door":
                report.doors_unlinked += 1
            else:
                report.windows_unlinked += 1
            model.flag_for_review(
                "window_room_link",
                f"{category} {wdet['id']} at [{s0:.2f}, {s1:.2f}] m matches no wall segment",
                conf,
                prov,
                target={"kind": "window_detection", "id": wdet["id"]},
            )
            model.symbol_linkages.append(
                SymbolLinkage(
                    symbol_id=wdet["id"],
                    symbol_tag=wdet["tag"],
                    category=category,
                    schedule_entry=entry,
                    confidence=conf,
                    provenance=prov,
                )
            )
            continue
        sp = space_of_num[seg["room_number"]]
        if category == "door":
            # A door joins a door schedule row. A row of another category does
            # not describe it: the measured dimensions are used instead and
            # the mismatch is flagged for review below.
            if entry is not None and sched_category == "door":
                width_m = entry.width_m
                height_m = entry.height_m
            else:
                width_m = round(s1 - s0, 3)
                height_m = round(max(head, sill) - min(head, sill), 3)
        else:
            width_m = entry.width_m if entry else (s1 - s0)
            height_m = entry.height_m if entry else None
        area = width_m * height_m if width_m and height_m else None
        needs_review = ambiguous or conf < REVIEW_CONFIDENCE or mismatch
        sp.openings.append(
            SpaceOpening(
                id=f"{facade.name}-{wdet['id']}",
                tag=wdet["tag"],
                category=category,
                width_m=width_m,
                height_m=height_m,
                sill_m=round(sill, 3),
                head_m=(round(sill + height_m, 3) if height_m is not None else None),
                host_facade=facade.name,
                host_interval_m=[round(s0, 3), round(s1, 3)],
                area_m2=area,
                provenance=prov,
                needs_review=needs_review,
            )
        )
        model.symbol_linkages.append(
            SymbolLinkage(
                symbol_id=wdet["id"],
                symbol_tag=wdet["tag"],
                category=category,
                schedule_entry=entry,
                confidence=conf,
                provenance=prov,
            )
        )
        if category == "door":
            report.doors_linked += 1
        else:
            report.windows_linked += 1
        if mismatch:
            # The flag must survive the review-confidence gate, so the
            # confidence handed over is capped below it (the mismatch itself
            # is what needs a human).
            model.flag_for_review(
                "window_room_link",
                f"{category} {wdet['id']} -> room {sp.number}: schedule row "
                f"'{wdet['tag']}' is a {sched_category}, sill {sill:.2f} m says "
                f"{category}",
                min(conf, REVIEW_CONFIDENCE - 0.01),
                prov,
                target={
                    "kind": "opening",
                    "id": f"{facade.name}-{wdet['id']}",
                    "field": "space_id",
                },
            )
        if needs_review:
            model.flag_for_review(
                "window_room_link",
                f"{category} {wdet['id']} -> room {sp.number}: "
                f"{'ambiguous span' if ambiguous else 'low confidence'} "
                f"({conf:.2f})",
                conf,
                prov,
                target={
                    "kind": "opening",
                    "id": f"{facade.name}-{wdet['id']}",
                    "field": "space_id",
                },
            )
    model.log_revision(
        meta["sheet_id"],
        meta["revision"],
        "ingest",
        f"{report.windows_linked} windows and {report.doors_linked} doors linked via {path} path",
    )
    report.mean_confidence_by_method[reg.method] = sum(c for _, c in confs) / max(1, len(confs))
    return path


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------
