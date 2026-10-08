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
    detector: dict = field(default_factory=dict)
    elevations: List[dict] = field(default_factory=list)

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
            "detector": self.detector,
            "elevations": self.elevations,
            "sheets": [asdict(s) for s in self.sheets],
        }


class SetError(RuntimeError):
    """No floor plan in the set produced a room: nothing to model."""


def _val(entry: dict, key: str):
    v = entry.get(key)
    return v.get("value") if isinstance(v, dict) else v


_PREFIX = {"A": "architectural", "M": "mechanical", "E": "electrical", "P": "plumbing"}


def _det_dict(x) -> dict:
    """One detection as JSON: label, tag, score, bbox (sheet raster pixels)."""
    from dataclasses import is_dataclass

    d = asdict(x) if is_dataclass(x) else dict(x)
    return {
        "label": str(d.get("label", "")),
        "tag": str(d.get("tag", "") or ""),
        "score": float(d.get("score", 0.0) or 0.0),
        "bbox": [float(v) for v in d.get("bbox", ())][:4],
    }


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
    provider=None,
):
    """Read a PDF set and assemble a BuildingModel. Returns (model, report).

    ``provider`` is a ``detection_provider.DetectionProvider`` (#743), chosen by
    config; it runs on each floor plan's rendered image unless ``detections``
    already holds that sheet. The report records which provider ran and whether
    its weights are evaluation only under the license ledger.
    """
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
    detections = dict(detections or {})
    if provider is not None and provider.info.provider != "none":
        report.detector = asdict(provider.info)
        report.notes.append(f"detector: {provider.info.note()}")
    for f in files:
        st = status[f]
        png = sheets_dir / f.replace(".json", ".png")
        ran = False
        if (
            f in plan_files
            and f not in detections
            and report.detector
            and png.exists()
            and (not hasattr(provider, "has") or provider.has(png))
        ):
            detections[f] = provider.detect(png, f"{Path(str(pdf)).name}:{f}")
            ran = True
        if f not in plan_files:
            st.mark("symbols", "skipped", "not a floor plan for takeoff")
        elif f in detections:
            extra = {"provider": provider.info.provider} if ran else {}
            if ran and provider.info.eval_only:
                extra["eval_only"] = True
            st.mark("symbols", "ok", n=len(detections[f]), **extra)
        elif st.stages["ingest"]["kind"] == "raster_only":
            st.mark("symbols", "failed", "scanned sheet and no detector run (#743)")
        else:
            st.mark(
                "symbols",
                "failed" if _discipline(st) == "mechanical" else "skipped",
                "no detections supplied; the detector provider is #743",
            )
        if f in detections:
            # kept beside the sheet so the review report can draw them (#749)
            (sheets_dir / f.replace("sheet_", "detections_")).write_text(
                json.dumps([_det_dict(x) for x in detections[f]], indent=1)
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

    h_marks, marks_note, marks_conflict = _storey_from_elevations(
        files, entries, status, scales, sheets_dir
    )
    if storey_height_m:
        h, h_src = storey_height_m, "config"
    elif h_marks:
        h, h_src = h_marks, "elevation_level_marks"
        report.notes.append(f"storey height {h:g} m from elevation level marks ({marks_note})")
    else:
        h, h_src = DEFAULT_STOREY_HEIGHT_M, "storey_height_default"
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
                        target={"kind": "space", "id": sid},
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
    elev_reads, elev_bbox = _read_elevations(
        files, entries, status, scales, sheets_dir, plan_of_level, walls, review, report
    )
    elevations = len(elev_reads)
    if h_src == "storey_height_default":
        review.append(
            ReviewItem(
                id="rq-storey-height",
                kind="elevation_extraction",  # storey height comes from sections/elevations
                description=(
                    f"storey height {h:g} m is the default: "
                    + (
                        f"elevation level marks disagree ({marks_conflict}); "
                        if marks_conflict
                        else "the elevations carry no named floor level marks; "
                        if elevations
                        else "no section or elevation was read; "
                    )
                    + "set wall_height in the run config to override"
                ),
                confidence=0.5,
                provenance=Provenance(
                    str(pdf),
                    0,
                    "storey_height_default",
                    0.5,
                    note=(
                        f"storey height {h:g} m for every level (matchline default "
                        "DEFAULT_STOREY_HEIGHT_M); no section or elevation level marks gave one"
                    ),
                ),  # fmt: skip
                needs_review=False,
            )
        )

    sched_entries, equipment = _merge_schedules(
        files, status, schedules, review, Provenance, ReviewItem
    )
    constructions: dict = {}
    centers: dict = {}
    for lid, (f, sheet_id, li) in plan_of_level.items():
        report.levels[li]["openings"] = _plan_openings(
            lid, walls[f]["openings"], [w for w in envelope if w.id.startswith(f"{lid}-E")],
            spaces, sched_entries, sheet_id, review, constructions, centers,
        )  # fmt: skip
    if elev_reads:
        first = next(iter(plan_of_level))
        pf, psid, _li = plan_of_level[first]
        plan_end = {
            "sheet": pf, "sheet_id": psid,
            "h_pt": float(json.loads((sheets_dir / pf).read_text())["height_pt"]),
            "m_per_pt": (scales.get(pf) or {}).get("m_per_pt"),
        }  # fmt: skip
        _join_elevations(
            elev_reads, elev_bbox,
            [sp for sp in spaces.values() if sp.level_id == first], centers, review, report,
            plan_end, sheets_dir,
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
        constructions=constructions,
    )
    report.sheets = list(status.values())
    _write(out, report)
    return model, report


def _storey_from_elevations(files, entries, status, scales, sheets_dir):
    """Storey height from the level marks on the set's vector elevations.

    Returns ``(height, note, conflict)``: a height only when every floor-to-floor
    step on every elevation agrees within ``STOREY_AGREE_M`` (the model has one
    storey height); ``conflict`` lists the steps when they do not.
    """
    from elevation_sheets import STOREY_AGREE_M, level_marks, storey_heights

    found = []
    for f in files:
        e = entries.get(f, {})
        if e.get("type") != "elevation" and _val(e, "type") != "elevation":
            continue
        if status[f].stages["ingest"]["kind"] == "raster_only":
            continue
        try:
            sheet = json.loads((sheets_dir / f).read_text())
        except (OSError, ValueError):
            continue
        hs = storey_heights(level_marks(sheet, (scales.get(f) or {}).get("m_per_pt")))
        if hs:
            found.append((status[f].number or f, hs))
    if not found:
        return None, "", ""
    steps = [x for _sid, hs in found for x in hs]
    desc = "; ".join(f"{sid}: " + ", ".join(f"{x:.2f} m" for x in hs) for sid, hs in found)
    if max(steps) - min(steps) > STOREY_AGREE_M:
        return None, "", desc
    return round(sum(steps) / len(steps), 4), desc, ""


def _read_elevations(files, entries, status, scales, sheets_dir, plan_of_level, walls, review,
                     report) -> int:  # fmt: skip
    """Read windows off the set's elevation sheets (#810, first slice).

    Each elevation is registered to the lowest level's plan footprint: by the
    shared column grid when both sheets carry it, else by the facade outline.
    Writes ``sheets/elevation_NNN.json`` and returns the reads that registered,
    as ``(ElevationRead, Provenance)`` pairs, with the footprint box they share.
    """
    from building_model import Provenance, ReviewItem
    from elevation_sheets import facade_from_title, read_elevation
    from grid_detect import detect_grids, plan_grid_m

    elev_files = [f for f in files if entries.get(f, {}).get("type") == "elevation"
                  or _val(entries.get(f, {}), "type") == "elevation"]  # fmt: skip
    if not elev_files or not plan_of_level:
        return [], None
    lid = next(iter(plan_of_level))
    pf = plan_of_level[lid][0]
    # exterior footprint: wall centrelines widened by half their thickness,
    # canonical metres (x right, y down)
    xs, ys = [], []
    for w in walls[pf]["walls"]:
        t = float(w.get("thickness_m") or 0) / 2
        for x, y in (w["a_m"], w["b_m"]):
            xs += [x - t, x + t]
            ys += [-y - t, -y + t]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    plan = json.loads((sheets_dir / pf).read_text())
    pm = (scales.get(pf) or {}).get("m_per_pt")
    try:
        pgrid = detect_grids(plan, status[pf].number or pf)
    except Exception:  # noqa: BLE001 - a grid failure falls back to geometry
        pgrid = None
    reads = []
    for f in elev_files:
        st = status[f]
        sheet_id = st.number or f
        if st.stages["ingest"]["kind"] == "raster_only":
            st.mark("elevation", "failed", "scanned elevation; raster window reading is not built")
            continue
        sheet = json.loads((sheets_dir / f).read_text())
        name_s = None
        fac = facade_from_title(st.title or "")
        if pgrid is not None and pm and fac:
            if fac in ("south", "north"):
                g = plan_grid_m(pgrid, "v", pm)
                name_s = {k: round(v - bbox[0], 4) for k, v in g.items()}
            else:
                g = plan_grid_m(pgrid, "h", pm, origin_pt=float(plan["height_pt"]))
                name_s = {k: round(v - bbox[1], 4) for k, v in g.items()}
        try:
            egrid = detect_grids(sheet, sheet_id) if name_s else None
        except Exception:  # noqa: BLE001
            egrid = None
        er = read_elevation(
            sheet, f, sheet_id, st.title or "", (scales.get(f) or {}).get("m_per_pt"),
            bbox, name_s, egrid,
        )  # fmt: skip
        prov = Provenance(
            sheet_id=sheet_id,
            revision=0,
            method="elevation_vector",
            confidence=(er.registration or {}).get("confidence", 0.5),
            note=f"{f}: {er.reason or (er.registration or {}).get('note', '')}",
        )
        if not er.ok:
            st.mark("elevation", "failed", er.reason)
            review.append(
                ReviewItem(
                    id=f"rq-elev-{sheet_id}",
                    kind="elevation_extraction",
                    target={"kind": "sheet", "id": sheet_id},
                    description=f"elevation {sheet_id} not read: {er.reason}",
                    confidence=0.5,
                    provenance=prov,
                )
            )
            continue
        reads.append((er, prov))
        out = f.replace("sheet_", "elevation_")
        (sheets_dir / out).write_text(json.dumps(er.to_dict(), indent=1))
        st.mark(
            "elevation", "ok", facade=er.facade, windows=len(er.windows),
            doors=len(er.openings) - len(er.windows),
            registration=er.registration["method"], file=out,
        )  # fmt: skip
        report.elevations.append(
            {"sheet": f, "sheet_id": sheet_id, "facade": er.facade, "file": out,
             "windows": len(er.windows), "registration": er.registration["method"],
             "confidence": er.registration["confidence"]}
        )  # fmt: skip
        for k, r in enumerate(er.review):
            review.append(
                ReviewItem(
                    id=f"rq-elev-{sheet_id}-{k + 1}",
                    kind="elevation_extraction",
                    target={"kind": "sheet", "id": sheet_id, "facade": er.facade},
                    description=f"elevation {sheet_id}: {r['reason']}",
                    confidence=0.6,
                    provenance=prov,
                )
            )
    return reads, bbox


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
                prev = {k: v for k, v in entries.get(tag, {}).items() if k != "schedule_sheet"}
                if tag in entries and prev != e:
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
                entries[tag] = {**e, "schedule_sheet": sheet_id}
                where[tag] = sheet_id
    return entries, equipment


# An exterior plan opening is a gap whose middle lies within this distance of
# the level outline (the outline runs along wall centrelines; the thickest
# exterior walls on a plan are well under a metre).
EXTERIOR_TOL_M = 0.5
# A plan gap matches a scheduled door/window when its measured width is within
# this of the scheduled width (about 2 in; gaps are read from the wall outline).
WIDTH_TOL_M = 0.05


def _schedule_construction(constructions, cat, tags, cands, sheet_id, review, oid):
    """Construction id from the scheduled U/SHGC/VT of the matched tags (#746).

    All matched tags must state the same values; a disagreement goes to review
    and leaves the opening without a construction. A row with no U gives none.
    """
    from building_model import Construction, Provenance, ReviewItem

    vals = {(e.get("u_value_w_m2k"), e.get("shgc"), e.get("vt")) for _t, e in cands}
    if len(vals) != 1:
        review.append(
            ReviewItem(
                id=f"rq-{oid}-thermal",
                kind="opening_thermal_ambiguous",
                target={"kind": "opening", "id": oid, "field": "construction_id"},
                description=(
                    f"{oid}: scheduled {'/'.join(tags)} match by width but state different "
                    "U/SHGC/VT; no stated construction used"
                ),
                confidence=0.5,
                provenance=Provenance(sheet_id, 0, "pdf_schedule_thermal", 0.5),
                needs_review=True,
            )
        )
        return ""
    u, shgc, vt = vals.pop()
    if u is None:
        return ""
    cid = f"SCHED-{cat.upper()}-U{u:.4f}"
    if shgc is not None:
        cid += f"-S{shgc:.4f}"
    if vt is not None:
        cid += f"-V{vt:.4f}"
    if cid not in constructions:
        e = cands[0][1]
        name = f"Scheduled {cat}, U {u:.4f} W/m2K"
        if shgc is not None:
            name += f", SHGC {shgc:g}"
        if vt is not None:
            name += f", VT {vt:g}"
        conf = e.get("thermal_confidence") or 0.75
        constructions[cid] = Construction(
            id=cid,
            name=name,
            u_value_w_m2k=u,
            provenance=Provenance(
                sheet_id=e.get("schedule_sheet") or sheet_id,
                revision=0,
                method="pdf_schedule_thermal",
                confidence=conf,
                note=(
                    f"{cat} schedule {'/'.join(tags)}: {e.get('thermal_note', '')}; "
                    f"first placed on plan {sheet_id}"
                ),
            ),
            shgc=shgc,
            vt=vt,
        )
    return cid


def _plan_openings(
    lid,
    plan_openings,
    env_walls,
    spaces,
    sched,
    sheet_id,
    review,
    constructions=None,
    centers=None,
) -> dict:
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
        drawn = op.get("kind") if op.get("kind") in ("door", "window") else None
        if drawn:
            # a door swing or a glazing line on the plan rules out same-width
            # rows of the other category (#743)
            cands = [(t, e) for t, e in cands if e["category"] == drawn]
        # a scheduled tag written next to the gap names its row (#793)
        tag = op.get("tag_text") or ""
        row = dict(sized).get(tag)
        tag_why = ""
        if row is not None:
            if drawn and row["category"] != drawn:
                tag_why = f"tag {tag} is a scheduled {row['category']} but the plan draws a {drawn}"
            elif abs(row["width_m"] - width) > WIDTH_TOL_M:
                tag_why = (
                    f"tag {tag} is scheduled {row['width_m']:.2f} m wide but the gap is "
                    f"{width:.2f} m"
                )
            cands = [] if tag_why else [(tag, row)]
        kinds = {(e["category"], round(e["height_m"], 3)) for _t, e in cands}
        oid = f"{lid}-OP{k + 1}"
        s = ls.project(mid)
        if len(kinds) != 1:
            counts["unsized"] += 1
            why = (
                tag_why
                if tag_why
                else (f"the plan draws a {drawn} but no scheduled {drawn} is this wide")
                if drawn and not kinds
                else "no scheduled door or window is this wide"
                if not kinds
                else "scheduled doors/windows of this width disagree on type or height ("
                + ", ".join(t for t, _e in cands)
                + ")"
            )
            review.append(
                ReviewItem(
                    id=f"rq-{oid}",
                    kind="opening_unsized",
                    # what a review edit needs to add the opening back (#798)
                    target={
                        "kind": "wall",
                        "id": wall.id,
                        "field": "opening",
                        "gap": {
                            "opening_id": oid,
                            "space_id": wall.space_id if wall.space_id in spaces else "",
                            "facade": wall.facade,
                            "s_center_m": round(s, 4),
                            "width_m": round(width, 4),
                            "drawn": drawn or "",
                            "candidates": [t for t, _e in cands] or ([tag] if row else []),
                            "sheet_id": sheet_id,
                        },
                    },
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
            method="plan_gap_tag" if row is not None else "plan_gap_schedule_width",
            confidence=0.85 if row is not None else 0.8 if len(tags) == 1 else 0.7,
            note=(
                (
                    f"plan gap {float(op['width_m']):.3f} m tagged {tag} on the plan "
                    f"({op.get('tag_dist_m', 0):.2f} m away)"
                    if row is not None
                    else f"plan gap {float(op['width_m']):.3f} m matched schedule "
                    f"{'/'.join(tags)} by width"
                )
                + (
                    f"; {op['swing']} door swing drawn on the plan"
                    if drawn == "door"
                    else "; glazing line drawn on the plan"
                    if drawn == "window"
                    else ""
                )
            ),
        )
        sp = spaces.get(wall.space_id)
        if sp is None:
            continue
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
                construction_id=(
                    _schedule_construction(constructions, cat, tags, cands, sheet_id, review, oid)
                    if constructions is not None
                    else ""
                ),
            )
        )
        counts["modelled"] += 1
        if centers is not None:
            centers[oid] = (mid.x, mid.y)
    return counts


# Elevation <-> plan join (#810, second slice). The schedule states an
# opening's size and the plan places it along the wall; the elevation is the
# only sheet that says how high it sits.
JOIN_TOL_M = 0.3  # centre-to-centre along the facade
JOIN_WIDTH_TOL_M = 0.15
JOIN_HEIGHT_TOL_M = 0.1


def _join_elevations(
    reads, bbox, spaces, centers, review, report, plan_end=None, sheets_dir=None
) -> None:
    """Give plan openings the sill and head their elevation draws.

    Each elevation opening is matched to the plan opening of the same category
    on the same facade whose centre is nearest, within ``JOIN_TOL_M``. A match
    sets ``sill_m`` (and ``head_m`` = sill + the scheduled height); a width or
    height that disagrees with the schedule beyond tolerance goes to review and
    the scheduled size stands. An elevation opening with no plan opening, or a
    plan opening an elevation of its facade does not show, goes to review too.
    Lowest level only, like the registration.

    Every review item it raises names both sheets in ``target["ends"]`` (#810,
    third slice): the elevation end with the opening's box in sheet points, the
    plan end with the opening's centre in plan-sheet points, so the review page
    can draw both (#801). Matched pairs are written back to the elevation's
    ``sheets/elevation_NNN.json`` as each opening's ``plan_opening``.
    """
    from building_model import Provenance, ReviewItem

    pe = plan_end or {}

    def elev_end(er, eo):
        return {"side": "elevation", "sheet": er.sheet, "sheet_id": er.sheet_id,
                "el": f"elev:{eo.id}", "box": [round(v, 1) for v in eo.bbox_pt]}  # fmt: skip

    def plan_end_of(op):
        end = {"side": "plan", "sheet": pe.get("sheet", ""),
               "sheet_id": pe.get("sheet_id", ""), "el": f"op:{op.id}"}  # fmt: skip
        k = pe.get("m_per_pt")
        if k and op.id in centers:
            cx, cy = centers[op.id]  # canonical metres (y down) -> sheet points
            end["point"] = [round(cx / k, 1), round(pe["h_pt"] + cy / k, 1)]
        return end

    x0, y0 = bbox[0], bbox[1]
    by_fac: dict = {}
    for sp in spaces:
        for op in sp.openings:
            if op.id in centers and op.category in ("window", "door"):
                cx, cy = centers[op.id]
                s = cx - x0 if op.host_facade in ("north", "south") else cy - y0
                by_fac.setdefault(op.host_facade, []).append((op, s))
    for er, prov in reads:
        stats = {"matched": 0, "unmatched_elevation": 0, "unmatched_plan": 0}
        pairs = {}
        cands = list(by_fac.get(er.facade, []))
        taken = set()
        for eo in er.openings:
            es = (eo.s0_m + eo.s1_m) / 2
            near = [
                (abs(s - es), op)
                for op, s in cands
                if op.id not in taken and abs(s - es) <= JOIN_TOL_M
            ]
            same = [t for t in near if t[1].category == eo.kind]
            if not same:
                stats["unmatched_elevation"] += 1
                other = f"; the plan has a {near[0][1].category} there" if near else ""
                review.append(
                    ReviewItem(
                        id=f"rq-elev-{er.sheet_id}-{eo.id}",
                        kind="window_room_link",  # an elevation opening with no plan opening
                        target={
                            "kind": "sheet",
                            "id": er.sheet_id,
                            "facade": er.facade,
                            "opening": eo.id,
                            "ends": [elev_end(er, eo)]
                            + ([plan_end_of(near[0][1])] if near else []),
                        },  # fmt: skip
                        description=(
                            f"elevation {er.sheet_id}: {eo.kind} {eo.id} "
                            f"({eo.width_m:.2f} m wide at {es:.2f} m along the {er.facade} "
                            f"facade) has no plan {eo.kind} within {JOIN_TOL_M:g} m{other}"
                        ),
                        confidence=0.6,
                        provenance=prov,
                    )
                )
                continue
            _d, op = min(same, key=lambda t: t[0])
            taken.add(op.id)
            pairs[eo.id] = op
            stats["matched"] += 1
            why = []
            if abs(eo.width_m - op.width_m) > JOIN_WIDTH_TOL_M:
                why.append(
                    f"{eo.width_m:.2f} m wide on the elevation, {op.width_m:.2f} m scheduled"
                )
            if abs(eo.height_m - op.height_m) > JOIN_HEIGHT_TOL_M:
                why.append(
                    f"{eo.height_m:.2f} m tall on the elevation, {op.height_m:.2f} m scheduled"
                )
            sill = 0.0 if eo.kind == "door" else round(eo.sill_m, 4)
            op.sill_m = sill
            op.head_m = round(sill + op.height_m, 4)
            op.history.append(
                Provenance(
                    sheet_id=er.sheet_id,
                    revision=0,
                    method="elevation_join",
                    confidence=prov.confidence,
                    note=(
                        f"{eo.id}: sill {sill:.2f} m read off the {er.facade} elevation "
                        f"({(er.registration or {}).get('method', '')} registration)"
                    ),
                )
            )
            if why:
                review.append(
                    ReviewItem(
                        id=f"rq-elev-{er.sheet_id}-{eo.id}",
                        kind="elevation_conflict",  # elevation and schedule disagree on size
                        target={
                            "kind": "opening",
                            "id": op.id,
                            "sheet": er.sheet_id,
                            "ends": [elev_end(er, eo), plan_end_of(op)],
                        },  # fmt: skip
                        description=(
                            f"{op.id} ({op.tag}) is {'; '.join(why)}; scheduled size kept"
                        ),
                        confidence=0.6,
                        provenance=prov,
                    )
                )
        for op, s in cands:
            if op.id in taken:
                continue
            stats["unmatched_plan"] += 1
            review.append(
                ReviewItem(
                    id=f"rq-elev-{er.sheet_id}-{op.id}",
                    kind="elevation_conflict",  # a plan opening the elevation does not show
                    target={
                        "kind": "opening",
                        "id": op.id,
                        "sheet": er.sheet_id,
                        "ends": [plan_end_of(op)],
                    },  # fmt: skip
                    description=(
                        f"{op.id} ({op.tag}, {op.category} at {s:.2f} m along the {er.facade} "
                        f"facade) is not drawn on elevation {er.sheet_id}; sill unknown"
                    ),
                    confidence=0.6,
                    provenance=prov,
                )
            )
        for e in report.elevations:
            if e["sheet_id"] == er.sheet_id:
                e.update(stats)
                fp = sheets_dir / e["file"] if sheets_dir else None
                if fp is not None and fp.exists():
                    d = json.loads(fp.read_text())
                    d["plan"] = {"sheet": pe.get("sheet", ""), "sheet_id": pe.get("sheet_id", "")}
                    for od in d.get("openings", []):
                        op = pairs.get(od["id"])
                        od["plan_opening"] = op.id if op else None
                        od["plan_point"] = plan_end_of(op).get("point") if op else None
                    fp.write_text(json.dumps(d, indent=1))


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
