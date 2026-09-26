from __future__ import annotations

from typing import TYPE_CHECKING

from building_model import (
    BuildingModel,
    FixtureInstance,
    Provenance,
    SymbolLinkage,
)
from registration import (
    Affine2D,
    PlanRegistration,
    assign_points_to_spaces,
)

if TYPE_CHECKING:
    from link._report import LinkReport

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Artificial lighting gain linking."""


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
        model.symbol_linkages.append(
            SymbolLinkage(
                symbol_id=fid,
                symbol_tag=f["tag"],
                category="lighting",
                schedule_entry=entry,
                confidence=0.95,
                provenance=Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="schedule_join",
                    confidence=0.95,
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
