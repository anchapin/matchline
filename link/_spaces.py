from __future__ import annotations

from building_model import (
    BuildingModel,
    Provenance,
    Space,
)
from datasets_adapter import polygon_area_px2
from polygon_classify import classify_polygons

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Space construction from room polygons."""


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
