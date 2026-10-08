"""Run the pipeline on a real PDF drawing set (#741).

``build_set_model(pdf, out_dir)`` runs the drawing stages one sheet at a time
and assembles a :class:`BuildingModel` that the rest of ``run_pipeline``
(simplify, validate, BEM export) consumes unchanged:

    ingest -> sheet index -> scale -> walls/rooms -> symbols -> schedules
    -> building model

Every stage writes its output under ``out_dir`` and records, per sheet,
whether it ran, was skipped (does not apply to that sheet) or failed, with the
reason, in ``stage_00_set_report.json``. A sheet that cannot be read (no scale,
raster-only with no detector, no walls) fails loudly there and the run goes on
with the sheets that could be read; the run itself stops only when no floor
plan produced a room.

Facts the drawings did not give are never invented silently: a storey height
the set does not state is the default with ``storey_height_default``
provenance and a review note, door heights are left unset (the BEM export
skips them with a note), and stages not built yet (symbols on a vector
mechanical sheet without detections, schedules) are reported as such.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from shapely.geometry import LineString, Point, Polygon

SCHEMA = "matchline.set_report/1"
DEFAULT_STOREY_HEIGHT_M = 3.0


@dataclass
class SheetStatus:
    file: str
    number: Optional[str] = None
    title: Optional[str] = None
    discipline: Optional[str] = None
    level: Optional[str] = None
    stages: Dict[str, dict] = field(default_factory=dict)

    def mark(self, stage: str, status: str, reason: str = "", **extra) -> None:
        self.stages[stage] = {"status": status, "reason": reason, **extra}


@dataclass
class SetReport:
    source: str
    sheets: List[SheetStatus] = field(default_factory=list)
    levels: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    schedules: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        failed = sorted(
            {s.file for s in self.sheets for st in s.stages.values() if st["status"] == "failed"}
        )
        return {
            "schema": SCHEMA,
            "source": self.source,
            "n_sheets": len(self.sheets),
            "failed_sheets": failed,
            "levels": self.levels,
            "notes": self.notes,
            "schedules": self.schedules,
            "sheets": [asdict(s) for s in self.sheets],
        }


class SetError(RuntimeError):
    """No floor plan in the set produced a room: nothing to model."""


def _val(entry: dict, key: str):
    v = entry.get(key)
    return v.get("value") if isinstance(v, dict) else v


_PREFIX = {"A": "architectural", "M": "mechanical", "E": "electrical", "P": "plumbing"}


def _discipline(st: SheetStatus) -> Optional[str]:
    """Discipline name from the index, else from the sheet number's prefix."""
    d = (st.discipline or "").lower()
    if d:
        return d
    if st.number and st.number[0].upper() in _PREFIX:
        return _PREFIX[st.number[0].upper()]
    return None


def _level_order(levels: List[str]) -> List[str]:
    def key(lv: str):
        if lv.startswith("B") and lv[1:].isdigit():
            return (0, -int(lv[1:]))
        if lv.startswith("L") and lv[1:].isdigit():
            return (1, int(lv[1:]))
        return {"MEZZ": (1, 1.5), "PH": (2, 0), "ROOF": (3, 0)}.get(lv, (2, 1))

    return sorted(set(levels), key=key)


def build_set_model(
    pdf,
    out_dir,
    *,
    detections: Optional[dict] = None,
    storey_height_m: Optional[float] = None,
    dpi: float = 150,
):
    """Read a PDF set and assemble a BuildingModel. Returns (model, report)."""
    from bem_helpers import _edge_facades
    from building_model import BuildingModel, EnvelopeWall, Level, Provenance, ReviewItem, Space
    from drawing_scale import scale_sheets
    from geometry_simplify import footprint_from_regions
    from pdf_ingest import ingest_pdf
    from pdf_schedules import schedules_for_sheets
    from plan_walls import walls_for_sheets
    from sheet_index import index_sheets

    out = Path(out_dir)
    sheets_dir = out / "sheets"
    report = SetReport(source=str(pdf))

    # ---- ingest, index, scale, walls ------------------------------------
    ing = ingest_pdf(pdf, out_dir=sheets_dir, dpi=dpi)
    files = [f"sheet_{i + 1:03d}.json" for i in range(len(ing.sheets))]
    status = {f: SheetStatus(file=f) for f in files}
    for f, sh in zip(files, ing.sheets):
        kind = sh.kind if hasattr(sh, "kind") else sh["kind"]
        status[f].mark("ingest", "ok", kind=kind)

    idx = index_sheets(sheets_dir).to_dict()
    entries = {e["file"]: e for e in idx["sheets"]}
    for f in files:
        e = entries.get(f, {})
        st = status[f]
        st.number, st.title = _val(e, "number"), _val(e, "title")
        st.discipline, st.level = _val(e, "discipline"), _val(e, "level")
        st.mark(
            "sheet_index",
            "ok" if st.number else "failed",
            "" if st.number else "no sheet number found in the title block",
            use_for_takeoff=bool(e.get("use_for_takeoff")),
            sheet_type=_val(e, "type"),
        )

    scales = scale_sheets(sheets_dir)
    for f in files:
        sc = scales.get(f, {})
        if sc.get("m_per_pt"):
            status[f].mark("scale", "ok", m_per_pt=sc["m_per_pt"], method=sc.get("method"))
        elif entries.get(f, {}).get("use_for_takeoff"):
            status[f].mark("scale", "failed", "no drawing scale found on a takeoff sheet")
        else:
            status[f].mark("scale", "skipped", "no scale; not a takeoff sheet")

    walls = walls_for_sheets(sheets_dir)
    schedules = schedules_for_sheets(
        sheets_dir, {f: status[f].number for f in files if status[f].number}
    )
    plan_files = [f for f in files if entries.get(f, {}).get("use_for_takeoff")]
    arch_plans = [f for f in plan_files if _discipline(status[f]) in ("architectural", None)]
    for f in files:
        if f not in plan_files:
            status[f].mark("walls_rooms", "skipped", "not a floor plan for takeoff")
        elif f not in arch_plans:
            status[f].mark("walls_rooms", "skipped", "rooms come from the architectural plan")
        else:
            res = walls.get(f)
            kinds = [r["kind"] for r in (res or {}).get("review", [])]
            if not res or "no_scale" in kinds:
                status[f].mark("walls_rooms", "failed", "no scale, walls not read")
            elif status[f].stages["ingest"]["kind"] == "raster_only":
                status[f].mark(
                    "walls_rooms", "failed", "scanned sheet; raster wall finding is not built yet"
                )
            elif not res["rooms"]:
                status[f].mark(
                    "walls_rooms",
                    "failed",
                    "no closed rooms" + (" (no walls found)" if "no_walls" in kinds else ""),
                    review=len(res["review"]),
                )
            else:
                status[f].mark(
                    "walls_rooms",
                    "ok",
                    walls=res["stats"]["walls"],
                    rooms=res["stats"]["rooms"],
                    openings=res["stats"]["openings"],
                    review=len(res["review"]),
                )

    # ---- symbols and schedules: report what ran, never fake it ----------
    for f in files:
        st = status[f]
        if f not in plan_files:
            st.mark("symbols", "skipped", "not a floor plan for takeoff")
        elif detections and f in detections:
            st.mark("symbols", "ok", n=len(detections[f]))
        elif st.stages["ingest"]["kind"] == "raster_only":
            st.mark("symbols", "failed", "scanned sheet and no detector run (#743)")
        else:
            st.mark(
                "symbols",
                "failed" if _discipline(st) == "mechanical" else "skipped",
                "no detections supplied; the detector provider is #743",
            )
        sch = schedules.get(f, {}).get("schedules", [])
        bad = [x for x in sch if x["status"] != "ok"]
        if st.stages["ingest"]["kind"] == "raster_only":
            st.mark("schedules", "skipped", "scanned sheet; schedule tables need the text layer")
        elif not sch:
            st.mark("schedules", "skipped", "no schedule table on this sheet")
        else:
            st.mark(
                "schedules",
                "failed" if bad else "ok",
                "; ".join(f"{x['title']}: {x['reason']}" for x in bad),
                n=len(sch),
                kinds=sorted({x["kind"] for x in sch}),
                unparsed=len(bad),
            )

    # ---- levels ---------------------------------------------------------
    by_level: Dict[str, List[str]] = {}
    unknown = 0
    for f in arch_plans:
        if status[f].stages["walls_rooms"]["status"] != "ok":
            continue
        lv = status[f].level
        if not lv:
            unknown += 1
            lv = f"L?{unknown}"
            report.notes.append(f"{f}: no level in the title; modelled as its own level {lv}")
        by_level.setdefault(lv, []).append(f)
    if not by_level:
        report.sheets = list(status.values())
        _write(out, report)
        raise SetError(
            "no floor plan produced a room; see stage_00_set_report.json for each sheet's reason"
        )

    h = storey_height_m or DEFAULT_STOREY_HEIGHT_M
    h_src = "config" if storey_height_m else "storey_height_default"
    levels: List[Level] = []
    spaces: Dict[str, Space] = {}
    review: List[ReviewItem] = []
    envelope: List[EnvelopeWall] = []
    plan_of_level: Dict[str, tuple] = {}
    for k, lv in enumerate(_level_order(list(by_level))):
        lid = lv if not lv.startswith("L?") else f"LX{lv[2:]}"
        levels.append(Level(id=lid, name=lid, elevation_z_m=round(k * h, 4), wall_height_m=h))
        report.levels.append(
            {
                "id": lid,
                "sheets": by_level[lv],
                "elevation_m": round(k * h, 4),
                "height_source": h_src,
                # every gap the plan shows; "openings" below says which were modelled
                "plan_openings": len(walls[by_level[lv][0]]["openings"]),
            }
        )
        if len(by_level[lv]) > 1:
            report.notes.append(
                f"level {lid}: {len(by_level[lv])} plan sheets; rooms from {by_level[lv][0]} only "
                "(partial plans are not stitched yet)"
            )
        f = by_level[lv][0]
        res = walls[f]
        sheet_id = status[f].number or f
        unl = 0
        for room in res["rooms"]:
            lab = room.get("label") or {}
            number = (lab.get("number") or "").strip()
            name = (lab.get("name") or "").strip()
            if number and f"{lid}-{number}" not in spaces:
                sid = f"{lid}-{number}"
            else:
                unl += 1
                sid = f"{lid}-UNLABELED-{unl}"
            conf = float(lab.get("parse_confidence") or 0.0)
            prov = Provenance(
                sheet_id=sheet_id,
                revision=0,
                method="plan_walls_vector",
                confidence=0.9 if not room["needs_review"] else 0.6,
                note=f"{f} {room['id']}; label: {lab.get('raw_text', '') or 'none'}",
            )
            poly = [[x, -y] for x, y in room["polygon_m"][:-1]]  # y-up -> canonical y-down
            sp = Space(
                id=sid,
                level_id=lid,
                name=name,
                number=number,
                polygon_m=poly,
                area_m2=room["area_m2"],
                volume_m3=round(room["area_m2"] * h, 4),
                core_provenance=prov,
                label_confidence=conf,
            )
            spaces[sid] = sp
            if room["needs_review"]:
                review.append(
                    ReviewItem(
                        id=f"rq-{sid}",
                        kind="space_no_geometry",
                        description=f"{sid}: {'; '.join(room['reasons'])}",
                        confidence=0.6,
                        provenance=prov,
                        needs_review=True,
                    )
                )
        envelope += _envelope(
            lid, [s for s in spaces.values() if s.level_id == lid], h, sheet_id,
            footprint_from_regions, _edge_facades, EnvelopeWall, Provenance,
        )  # fmt: skip
        plan_of_level[lid] = (f, sheet_id, len(report.levels) - 1)
    if h_src == "storey_height_default":
        review.append(
            ReviewItem(
                id="rq-storey-height",
                kind="elevation_extraction",  # storey height comes from sections/elevations
                description=(
                    f"storey height {h:g} m is the default: no section or elevation was read; "
                    "set wall_height in the run config to override"
                ),
                confidence=0.5,
                provenance=Provenance(str(pdf), 0, "storey_height_default", 0.5),
                needs_review=False,
            )
        )

    sched_entries, equipment = _merge_schedules(
        files, status, schedules, review, Provenance, ReviewItem
    )
    for lid, (f, sheet_id, li) in plan_of_level.items():
        report.levels[li]["openings"] = _plan_openings(
            lid, walls[f]["openings"], [w for w in envelope if w.id.startswith(f"{lid}-E")],
            spaces, sched_entries, sheet_id, review,
        )  # fmt: skip
    report.schedules = {
        "entries": len(sched_entries),
        "equipment": equipment,
        "tables": [
            {"sheet": f, "title": x["title"], "kind": x["kind"], "status": x["status"],
             "reason": x["reason"], "rows": len(x["rows"])}
            for f in files for x in schedules.get(f, {}).get("schedules", [])
        ],
    }  # fmt: skip

    model = BuildingModel(
        name=Path(pdf).stem,
        levels=levels,
        spaces=spaces,
        zones={},
        envelope=envelope,
        bim_elements=[],
        schedules=sched_entries,
        review_queue=review,
    )
    report.sheets = list(status.values())
    _write(out, report)
    return model, report


def _merge_schedules(files, status, schedules, review, Provenance, ReviewItem):
    """Door, window and lighting rows -> model.schedules (tag -> entry dict);
    mechanical rows -> an equipment list. A tag scheduled twice with different
    values is kept from the first sheet and sent to review, never overwritten."""
    entries: Dict[str, dict] = {}
    where: Dict[str, str] = {}
    equipment: List[dict] = []
    for f in files:
        sheet_id = status[f].number or f
        for x in schedules.get(f, {}).get("schedules", []):
            if x["status"] != "ok":
                review.append(
                    ReviewItem(
                        id=f"rq-schedule-{len(review)}",
                        kind="fixture_schedule",
                        description=f"{sheet_id}: {x['title']} not read ({x['reason']})",
                        confidence=0.5,
                        provenance=Provenance(sheet_id, 0, "pdf_ruled_table", 0.5),
                    )
                )
                continue
            for e in x["equipment"]:
                equipment.append({**e, "sheet": sheet_id, "schedule": x["title"]})
            for tag, e in x["entries"].items():
                if tag in entries and entries[tag] != e:
                    review.append(
                        ReviewItem(
                            id=f"rq-schedule-{len(review)}",
                            kind="fixture_schedule",
                            description=(
                                f"tag {tag} is scheduled on {where[tag]} and again with "
                                f"different values on {sheet_id}; kept {where[tag]}"
                            ),
                            confidence=0.5,
                            provenance=Provenance(sheet_id, 0, "pdf_ruled_table", 0.5),
                        )
                    )
                    continue
                entries[tag] = e
                where[tag] = sheet_id
    return entries, equipment


# An exterior plan opening is a gap whose middle lies within this distance of
# the level outline (the outline runs along wall centrelines; the thickest
# exterior walls on a plan are well under a metre).
EXTERIOR_TOL_M = 0.5
# A plan gap matches a scheduled door/window when its measured width is within
# this of the scheduled width (about 2 in; gaps are read from the wall outline).
WIDTH_TOL_M = 0.05


def _plan_openings(lid, plan_openings, env_walls, spaces, sched, sheet_id, review) -> dict:
    """Exterior plan openings -> SpaceOpenings sized by the door/window schedule.

    A plan gap carries a width and a position but no tag, so it is matched to
    the schedule by width: when every scheduled door/window within
    ``WIDTH_TOL_M`` of the gap agrees on category and height, the gap is
    modelled on the wall it sits in at the scheduled size. A gap no schedule
    row explains, or one several disagree on, goes to review and is not
    modelled: no default size is invented. Interior gaps are
    skipped (they do not touch the exterior envelope).
    """
    from building_model import Provenance, ReviewItem, SpaceOpening

    counts = {"exterior": 0, "interior": 0, "modelled": 0, "unsized": 0}
    lines = [(w, LineString([w.from_m, w.to_m])) for w in env_walls]
    sized = sorted(
        (tag, e)
        for tag, e in sched.items()
        if e.get("category") in ("door", "window") and e.get("width_m") and e.get("height_m")
    )
    for k, op in enumerate(plan_openings):
        (ax, ay), (bx, by) = op["a_m"], op["b_m"]
        mid = Point((ax + bx) / 2, -(ay + by) / 2)  # y-up -> canonical y-down
        if not lines:
            counts["interior"] += 1
            continue
        wall, ls = min(lines, key=lambda t: t[1].distance(mid))
        if ls.distance(mid) > EXTERIOR_TOL_M:
            counts["interior"] += 1
            continue
        counts["exterior"] += 1
        width = float(op["width_m"])
        cands = [(t, e) for t, e in sized if abs(e["width_m"] - width) <= WIDTH_TOL_M]
        kinds = {(e["category"], round(e["height_m"], 3)) for _t, e in cands}
        oid = f"{lid}-OP{k + 1}"
        if len(kinds) != 1:
            counts["unsized"] += 1
            why = (
                "no scheduled door or window is this wide"
                if not kinds
                else "scheduled doors/windows of this width disagree on type or height ("
                + ", ".join(t for t, _e in cands)
                + ")"
            )
            review.append(
                ReviewItem(
                    id=f"rq-{oid}",
                    kind="opening_unsized",
                    description=(
                        f"{oid}: {width:.2f} m gap in {wall.facade} wall {wall.id}; {why}; "
                        "not modelled"
                    ),
                    confidence=0.5,
                    provenance=Provenance(sheet_id, 0, "plan_walls_vector", 0.5),
                    needs_review=True,
                )
            )
            continue
        cat, height = kinds.pop()
        tags = [t for t, _e in cands]
        # the schedule states the size; the plan gap only places it (and the
        # takeoff reconcile check holds count x schedule size to the area)
        width = float(cands[0][1]["width_m"])
        prov = Provenance(
            sheet_id=sheet_id,
            revision=0,
            method="plan_gap_schedule_width",
            confidence=0.8 if len(tags) == 1 else 0.7,
            note=(
                f"plan gap {float(op['width_m']):.3f} m matched schedule {'/'.join(tags)} by width"
            ),
        )
        sp = spaces.get(wall.space_id)
        if sp is None:
            continue
        s = ls.project(mid)
        sp.openings.append(
            SpaceOpening(
                id=oid,
                tag=tags[0],
                category=cat,
                width_m=width,
                height_m=height,
                host_facade=wall.facade,
                host_interval_m=[round(s - width / 2, 4), round(s + width / 2, 4)],
                s_center_m=round(s, 4),
                area_m2=width * height,
                provenance=prov,
                needs_review=False,
            )
        )
        counts["modelled"] += 1
    return counts


def _envelope(lid, spaces, h, sheet_id, footprint, edge_facades, EnvelopeWall, Provenance):
    """Exterior walls of one level: the outline of its rooms at wall centrelines.

    Each edge of the level's footprint is one wall, faced by its outward
    normal and owned by the room it bounds.
    """
    ring = footprint([sp.polygon_m for sp in spaces])
    if not ring or len(ring) < 3:
        return []
    facades = edge_facades(ring, y_north=False)
    out = []
    for i, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1])):
        edge = LineString([a, b])
        owner = min(
            spaces,
            key=lambda sp: Polygon(sp.polygon_m).exterior.distance(
                edge.interpolate(0.5, normalized=True)
            ),
        )
        L = edge.length
        out.append(
            EnvelopeWall(
                id=f"{lid}-E{i + 1}",
                facade=facades[i],
                from_m=[float(a[0]), float(a[1])],
                to_m=[float(b[0]), float(b[1])],
                length_m=round(L, 4),
                height_m=h,
                area_m2=round(L * h, 4),
                provenance=Provenance(sheet_id, 0, "room_outline", 0.85),
                space_id=owner.id,
            )
        )
    return out


def _write(out: Path, report: SetReport) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "stage_00_set_report.json").write_text(json.dumps(report.to_dict(), indent=2))
