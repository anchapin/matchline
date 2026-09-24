from __future__ import annotations

from typing import TYPE_CHECKING

from building_model import (
    REVIEW_CONFIDENCE,
    BuildingModel,
    Provenance,
    SpaceOpening,
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
