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
    hvac_legends: List[dict] = field(default_factory=list)

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
            "hvac_legends": self.hvac_legends,
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
    storey_height_m=None,
    dpi: float = 150,
    provider=None,
):
    """Read a PDF set and assemble a BuildingModel. Returns (model, report).

    ``provider`` is a ``detection_provider.DetectionProvider`` (#743), chosen by
    config; it runs on each floor plan's rendered image unless ``detections``
    already holds that sheet. The report records which provider ran and whether
    its weights are evaluation only under the license ledger.

    ``storey_height_m`` is one height for every level (``--storey-height``, run
    config ``wall_height: 3.5``) or a map of level id to height (``wall_height:
    {L2: 4.0}``) for those levels only; the rest come from the elevation level marks,
    then the default (#821).
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

    by_storey, marks_desc, per_sheet = _storey_from_elevations(
        files, entries, status, scales, sheets_dir
    )
    order = _level_order(list(by_level))
    lid_of = {lv: (lv if not lv.startswith("L?") else f"LX{lv[2:]}") for lv in order}
    override = storey_height_m
    if isinstance(storey_height_m, dict):
        known = {lid_of[lv]: lv for lv in order}
        bad = sorted(str(k) for k in storey_height_m if str(k) not in known)
        if bad:
            report.sheets = list(status.values())
            _write(out, report)
            raise SetError(
                f"wall_height names level{'s' if len(bad) > 1 else ''} not in the set: "
                f"{', '.join(bad)} (levels: {', '.join(known)})"
            )
        override = {known[str(k)]: float(v) for k, v in storey_height_m.items()}
    heights, order_miss = _level_heights(order, by_storey, per_sheet, override)
    marked = [lv for lv in order if heights[lv][1] == "elevation_level_marks"]
    if marked:
        by_order = (
            " matched to the plan levels by order" if heights[marked[0]][3] == "order" else ""
        )
        hs = {heights[lv][0] for lv in marked}
        if len(hs) == 1 and len(marked) == len(order):
            report.notes.append(
                f"storey height {next(iter(hs)):g} m from elevation level marks{by_order} "
                f"({marks_desc})"
            )
        else:
            report.notes.append(
                "storey heights from elevation level marks: "
                + ", ".join(f"{lid_of[lv]} {heights[lv][0]:g} m" for lv in marked)
                + f"{by_order} ({marks_desc})"
            )
    levels: List[Level] = []
    spaces: Dict[str, Space] = {}
    review: List[ReviewItem] = []
    envelope: List[EnvelopeWall] = []
    plan_of_level: Dict[str, tuple] = {}
    z = 0.0
    for lv in order:
        lid = lid_of[lv]
        h, h_src, _conflict, h_match = heights[lv]
        levels.append(Level(id=lid, name=lid, elevation_z_m=round(z, 4), wall_height_m=h))
        report.levels.append(
            {
                "id": lid,
                "sheets": by_level[lv],
                "elevation_m": round(z, 4),
                "height_m": h,
                "height_source": h_src,
                **({"height_match": h_match} if h_match else {}),
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
        z += h
    elev_reads, elev_bbox = _read_elevations(
        files, entries, status, scales, sheets_dir, plan_of_level, walls, review, report
    )
    elevations = len(elev_reads)
    defaulted = [lv for lv in order if heights[lv][1] == "storey_height_default"]
    if defaulted:
        dh = DEFAULT_STOREY_HEIGHT_M
        conflicts = {lv: heights[lv][2] for lv in defaulted if heights[lv][2]}
        if len(defaulted) == len(order):
            if conflicts and len(order) == 1:
                why = f"elevation level marks disagree ({conflicts[order[0]]}); "
            elif conflicts:
                why = "elevation level marks disagree (" + "; ".join(
                    f"{lid_of[lv]}: {c}" for lv, c in conflicts.items()
                ) + "); "  # fmt: skip
            elif order_miss:
                why = f"{order_miss}; "
            elif by_storey:
                why = "the elevation level marks name no modelled level; "
            elif elevations:
                why = "the elevations carry no named floor level marks; "
            else:
                why = "no section or elevation was read; "
            desc = f"storey height {dh:g} m is the default: {why}"
            who = "every level"
        else:
            parts = [
                f"{lid_of[lv]} (elevation level marks disagree: {conflicts[lv]})"
                if lv in conflicts
                else f"{lid_of[lv]} (no level mark)"
                for lv in defaulted
            ]
            desc = (
                f"storey height {dh:g} m is the default for {', '.join(parts)}; "
                "the other levels take theirs from the elevation level marks; "
            )
            who = "levels " + ", ".join(lid_of[lv] for lv in defaulted)
        review.append(
            ReviewItem(
                id="rq-storey-height",
                kind="elevation_extraction",  # storey height comes from sections/elevations
                target={"kind": "level", "ids": [lid_of[lv] for lv in defaulted]},
                description=desc
                + (
                    "set wall_height in the run config to override"
                    if len(defaulted) == len(order)
                    else "set wall_height: {"
                    + ", ".join(f"{lid_of[lv]}: <metres>" for lv in defaulted)
                    + "} in the run config to override those levels"
                ),
                confidence=0.5,
                provenance=Provenance(
                    str(pdf),
                    0,
                    "storey_height_default",
                    0.5,
                    note=(
                        f"storey height {dh:g} m for {who} (matchline default "
                        "DEFAULT_STOREY_HEIGHT_M); no section or elevation level marks gave one"
                    ),
                ),  # fmt: skip
                needs_review=bool(conflicts),
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
            walls[f].get("wall_tags", []), {w["id"]: w for w in walls[f]["walls"]},
        )  # fmt: skip
    legend, legend_conflicts = _wall_type_legend(files, status, sheets_dir, sched_entries)
    if legend or legend_conflicts:
        report.notes.append(
            f"wall-type legend: {len(legend)} tag(s) "
            + ", ".join(
                f"{t} ({v['construction_type']}, {v['sheet_id']})" for t, v in legend.items()
            )
            + (f"; {len(legend_conflicts)} disagree" if legend_conflicts else "")
        )
    for tag, rows in sorted(legend_conflicts.items()):
        review.append(
            ReviewItem(
                id=f"rq-walltype-{tag}",
                kind="wall_type_conflict",
                description=(
                    f"wall type {tag} is described as different assemblies: "
                    + "; ".join(f"{r['description']!r} on {r['sheet_id']}" for r in rows)
                    + "; not used"
                ),
                confidence=0.5,
                provenance=Provenance(rows[0]["sheet_id"], 0, "wall_type_legend", 0.5),
                needs_review=True,
            )
        )
    for lid, (f, sheet_id, li) in plan_of_level.items():
        n = _assign_wall_types(
            lid, walls[f], [w for w in envelope if w.id.startswith(f"{lid}-E")],
            legend, constructions, review, sheet_id,
        )  # fmt: skip
        if legend:
            report.levels[li]["wall_types"] = n
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
    zones = _place_equipment(
        plan_files, status, scales, sheets_dir, plan_of_level, lid_of, spaces, equipment,
        review, report,
    )  # fmt: skip
    _mech_legends(files, status, sheets_dir, review, report)
    _place_fixtures(
        files, entries, status, scales, sheets_dir, plan_of_level, lid_of, spaces,
        sched_entries, review, report,
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
        zones=zones,
        envelope=envelope,
        bim_elements=[],
        schedules=sched_entries,
        review_queue=review,
        constructions=constructions,
    )
    _heated_slab(model, equipment, review, report, Provenance, ReviewItem)
    report.sheets = list(status.values())
    _write(out, report)
    return model, report


def _storey_from_elevations(files, entries, status, scales, sheets_dir):
    """Storey heights from the level marks on the set's vector elevations.

    Returns ``(by_storey, desc, per_sheet)``: ``by_storey`` maps a storey key (1 for
    FIRST FLOOR / LEVEL 1, 0 for BASEMENT, "MEZZ", "PH"; ``storey_key``) to the
    ``(sheet, height)`` each elevation states for it; ``desc`` lists every elevation's
    steps; ``per_sheet`` keeps each elevation's steps bottom to top, for matching by
    order when no plan level matches by name (#813, #814, #822).
    """
    from elevation_sheets import level_marks, storey_steps

    by_storey: Dict[object, list] = {}
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
        steps = storey_steps(level_marks(sheet, (scales.get(f) or {}).get("m_per_pt")))
        if not steps:
            continue
        sid = status[f].number or f
        found.append((sid, [x for _o, x in steps]))
        for o, x in steps:
            if o is not None:
                by_storey.setdefault(o, []).append((sid, x))
    desc = "; ".join(f"{sid}: " + ", ".join(f"{x:.2f} m" for x in hs) for sid, hs in found)
    return by_storey, desc, found


def _level_key(lv: str):
    """The storey key of a plan level id: L1 -> 1, B1 -> 0, B2 -> -1, MEZZ, PH (#814)."""
    if lv[:1] == "L" and lv[1:].isdigit():
        return int(lv[1:])
    if lv[:1] == "B" and lv[1:].isdigit():
        return 1 - int(lv[1:])
    if lv in ("MEZZ", "PH"):
        return lv
    return None


def _level_heights(order, by_storey, per_sheet, storey_height_m):
    """Each plan level's ``(height, source, conflict, match)`` and an order-miss note.

    The config height wins for every level, or for the levels a per-level config map
    names (keyed by plan level, #821). Otherwise a level takes the height its
    elevation level marks state, matched by name (``match`` "name"), when they agree
    within ``STOREY_AGREE_M``. When no plan level matches any mark by name, the k-th
    storey of each elevation whose storey count equals the plan level count goes to
    the k-th level (``match`` "order"); ``miss`` says why when none does. Anything
    else keeps the default, ``conflict`` naming the disagreeing elevations (#814, #822).
    """
    from elevation_sheets import STOREY_AGREE_M

    per_level = storey_height_m if isinstance(storey_height_m, dict) else {}
    if storey_height_m and not per_level:
        return {lv: (storey_height_m, "config", "", "") for lv in order}, ""
    cand = {}
    for lv in order:
        k = _level_key(lv)
        cand[lv] = by_storey.get(k, []) if k is not None else []
    match, miss = "name", ""
    if per_sheet and not any(cand.values()):
        fits = [(sid, hs) for sid, hs in per_sheet if len(hs) == len(order)]
        if fits:
            match = "order"
            cand = {lv: [(sid, hs[i]) for sid, hs in fits] for i, lv in enumerate(order)}
        else:
            miss = (
                "the elevation level marks name no modelled level and their storey counts ("
                + "; ".join(f"{sid}: {len(hs)}" for sid, hs in per_sheet)
                + f") do not match the {len(order)} plan level{'s' if len(order) != 1 else ''}"
            )
    out = {}
    for lv in order:
        if lv in per_level:
            out[lv] = (per_level[lv], "config", "", "")
            continue
        hs = cand[lv]
        vals = [x for _s, x in hs]
        if vals and max(vals) - min(vals) <= STOREY_AGREE_M:
            out[lv] = (round(sum(vals) / len(vals), 4), "elevation_level_marks", "", match)
        else:
            conflict = "; ".join(f"{s}: {x:.2f} m" for s, x in hs)
            out[lv] = (DEFAULT_STOREY_HEIGHT_M, "storey_height_default", conflict, "")
    return out, miss


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
    # #818: a facade titled on two or more sheets is drawn in parts
    n_fac: dict = {}
    for f in elev_files:
        fc = facade_from_title(status[f].title or "")
        n_fac[fc] = n_fac.get(fc, 0) + 1
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
            partial=True if fac and n_fac.get(fac, 0) > 1 else None,
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


def _legend_symbol_hits(sheet: dict, legends: list, sheets_dir) -> List[Dict[str, int]]:
    """Per legend, count each mapped symbol on its own sheet's raster (#744):
    the row's drawn symbol is cut as a one-shot template and matched by NCC
    (``hvac_trace.detect_legend_symbols``); no hit counts inside any legend
    symbol box on the sheet. Empty dicts without a raster."""
    raster, k = sheet.get("raster_file"), sheet.get("px_per_pt")
    if not legends or not raster or not k or not (sheets_dir / raster).exists():
        return [{} for _ in legends]
    import numpy as np
    from PIL import Image

    from hvac_legend import legend_templates, symbol_boxes_px
    from hvac_trace import detect_legend_symbols

    gray = np.asarray(Image.open(sheets_dir / raster).convert("L"), dtype=np.float64)
    boxes = symbol_boxes_px(legends, float(k))
    out: List[Dict[str, int]] = []
    for leg in legends:
        hits: Dict[str, int] = {}
        tmpls = legend_templates(gray, [leg], float(k))
        for d in detect_legend_symbols(gray, tmpls, exclude_px=boxes):
            hits[d["label"]] = hits.get(d["label"], 0) + 1
        out.append(hits)
    return out


def _mech_legends(files, status, sheets_dir, review, report) -> None:
    """Read the symbol legend on every vector mechanical sheet (#744).

    Each legend row's description is mapped to an HVAC detector class from its
    words only (``hvac_legend.classify``); the rows go on the report under
    ``hvac_legends`` with the symbol's box and, when the sheet has a raster,
    how many times each mapped symbol appears on it. Rows whose
    description names no class go to one review item per legend.
    """
    from building_model import Provenance, ReviewItem
    from hvac_legend import CONFIDENCE, read_legend

    # a set often repeats its legend on every mechanical plan: one review item
    # per distinct set of unmapped rows, naming every sheet it is on
    unmapped: Dict[tuple, List[tuple]] = {}
    for f in files:
        st = status[f]
        if _discipline(st) != "mechanical" or st.stages["ingest"]["kind"] == "raster_only":
            continue
        sheet_id = st.number or f
        sheet = json.loads((sheets_dir / f).read_text())
        legends = read_legend(sheet)
        for leg, hits in zip(legends, _legend_symbol_hits(sheet, legends, sheets_dir)):
            report.hvac_legends.append(
                {
                    "sheet": sheet_id,
                    "title": leg.title,
                    "bbox_pt": [round(v, 2) for v in leg.bbox_pt],
                    "rows": [
                        {"description": r.description, "class": r.cls,
                         "symbol_bbox_pt": r.symbol_bbox_pt}
                        for r in leg.rows
                    ],
                    "symbol_hits": hits,
                }
            )  # fmt: skip
            mapped = sorted({r.cls for r in leg.rows if r.cls})
            report.notes.append(
                f"{sheet_id}: {leg.title} with {len(leg.rows)} symbol row(s), "
                f"{len(leg.rows) - len(leg.unmapped)} naming an HVAC class"
                + (f" ({', '.join(mapped)})" if mapped else "")
                + (
                    "; legend symbols found on the sheet: "
                    + ", ".join(f"{c} {n}" for c, n in sorted(hits.items()))
                    + " (counts only, not yet joined to zones)"
                    if hits
                    else "; no sheet raster or no legend symbol found on the sheet"
                )
            )
            if leg.unmapped:
                key = tuple(r.description for r in leg.unmapped)
                unmapped.setdefault(key, []).append((sheet_id, leg))
    for k, (names, seen) in enumerate(unmapped.items()):
        sheet_id, leg = seen[0]
        sheets = ", ".join(dict.fromkeys(sid for sid, _ in seen))
        review.append(
            ReviewItem(
                id=f"rq-legend-{sheet_id}-{k}",
                kind="hvac_legend_unmapped",
                description=(
                    f"{leg.title} on {sheets}: {len(names)} symbol row(s) name no HVAC class "
                    "(supply diffuser, return/exhaust grille, VAV, AHU, thermostat): "
                    + "; ".join(names)
                ),
                confidence=CONFIDENCE,
                provenance=Provenance(sheet_id, 0, "pdf_legend", CONFIDENCE, tuple(leg.bbox_pt)),
                needs_review=True,
            )
        )


def _place_equipment(plan_files, status, scales, sheets_dir, plan_of_level, lid_of, spaces,
                     equipment, review, report) -> dict:  # fmt: skip
    """Put each scheduled mechanical tag in the room the mechanical plan shows
    it in (#746). Writes ``level_id``, ``space_id`` and ``located`` onto the
    equipment records; anything that cannot be placed goes to review.

    Returns the HVAC zones: one per placed terminal unit (VAV or fan coil),
    serving the room it sits in. Rooms it also serves through ductwork are not
    known without duct tracing, so the zone says so and stays at the
    placement's confidence."""
    if not equipment:
        return {}
    from building_model import ComponentRef, Provenance, ReviewItem, Zone
    from grid_detect import detect_grids
    from mech_tags import find_tags, register, room_of, to_canonical

    known = {e["tag"] for e in equipment}
    found: Dict[str, List[dict]] = {}
    for f in plan_files:
        st = status[f]
        if _discipline(st) != "mechanical" or st.stages["ingest"]["kind"] == "raster_only":
            continue
        sheet_id = st.number or f
        sheet = json.loads((sheets_dir / f).read_text())
        hits = find_tags(sheet, known)
        if not hits:
            continue
        lid = lid_of.get(st.level) if st.level else None
        if lid is None or lid not in plan_of_level:
            report.notes.append(
                f"{sheet_id}: {len(hits)} scheduled equipment tag(s) on a mechanical plan "
                "with no matching architectural level; not placed"
            )
            continue
        af, asid, _li = plan_of_level[lid]
        arch = json.loads((sheets_dir / af).read_text())
        mm = (scales.get(f) or {}).get("m_per_pt")
        ma = (scales.get(af) or {}).get("m_per_pt")
        try:
            mg, ag = detect_grids(sheet, sheet_id), detect_grids(arch, asid)
        except Exception:  # noqa: BLE001 - a grid failure falls back to the frame
            mg = ag = None
        dx, dy, how, conf, why = register(sheet, arch, mm, ma, mg, ag)
        if how is None:
            report.notes.append(f"{sheet_id}: not registered to {asid} ({why}); tags not placed")
            review.append(
                ReviewItem(
                    id=f"rq-mechreg-{sheet_id}",
                    kind="mech_plan_unregistered",
                    description=(
                        f"{sheet_id} shows {len(hits)} scheduled equipment tag(s) but does not "
                        f"register to {asid}: {why}"
                    ),
                    confidence=0.5,
                    provenance=Provenance(sheet_id, 0, "mech_plan_tags", 0.5),
                    needs_review=True,
                )
            )
            continue
        rooms = [sp for sp in spaces.values() if sp.level_id == lid]
        for h in hits:
            at = to_canonical(h["bbox_pt"], mm, dx, dy, float(arch["height_pt"]), ma)
            found.setdefault(h["tag"], []).append(
                {
                    "sheet": sheet_id, "bbox_pt": h["bbox_pt"], "at_m": list(at),
                    "level_id": lid, "space_id": room_of(at, rooms),
                    "registration": f"{how}: {why} (to {asid})", "confidence": conf,
                }
            )  # fmt: skip
    placed = 0
    zones: dict = {}
    for e in equipment:
        locs = found.get(e["tag"], [])
        if not locs:
            continue
        e["located"] = locs
        rooms = {(x["level_id"], x["space_id"]) for x in locs}
        conf = min(x["confidence"] for x in locs)
        prov = Provenance(locs[0]["sheet"], 0, "mech_plan_tags", conf, tuple(locs[0]["bbox_pt"]))
        if len(rooms) == 1 and locs[0]["space_id"]:
            e["level_id"], e["space_id"] = locs[0]["level_id"], locs[0]["space_id"]
            placed += 1
            if e.get("kind") in TERMINAL_KINDS:
                _terminal_zone(e, locs, conf, spaces, zones, ComponentRef, Provenance, Zone)
            continue
        where = ", ".join(f"{x['sheet']} {x['space_id'] or 'no room'}" for x in locs)
        review.append(
            ReviewItem(
                id=f"rq-equip-{e['tag']}",
                kind="equipment_outside_rooms" if len(rooms) == 1 else "equipment_ambiguous",
                description=(
                    f"{e['tag']} ({e['schedule']}, {e['sheet']}) is tagged at {where}; "
                    "no room assigned"
                ),
                confidence=conf,
                provenance=prov,
                needs_review=True,
            )
        )
    missing = sorted(known - set(found))
    report.notes.append(
        f"mechanical equipment: {placed} of {len(known)} scheduled tag(s) placed in a room"
        + (f"; not on any mechanical plan: {', '.join(missing)}" if missing else "")
        + (f"; {len(zones)} HVAC zone(s), one per placed terminal unit" if zones else "")
    )
    return zones


# Lighting fixture tags are read off these plans (#746): a reflected ceiling
# plan, or an electrical floor plan (lighting plans are drawn as floor plans).
FIXTURE_SHEET_TYPES = ("rcp",)
# One tag per fixture is what a count from tag text assumes; drawings often tag
# one fixture of a group, so every room lit this way goes to review.
FIXTURE_TAG_CONFIDENCE = 0.5


def _in_grid_bubble(bbox_pt, grid) -> bool:
    """True when a text span's centre sits inside a column-grid bubble: grid
    line labels (A, B, C ...) read like fixture types and are not fixtures."""
    if grid is None:
        return False
    cx, cy = (bbox_pt[0] + bbox_pt[2]) / 2, (bbox_pt[1] + bbox_pt[3]) / 2
    for g in grid.lines:
        for bx, by, r in g.bubbles:
            if (cx - bx) ** 2 + (cy - by) ** 2 <= r * r:
                return True
    return False


def _place_fixtures(files, idx_entries, status, scales, sheets_dir, plan_of_level, lid_of,
                    spaces, sched, review, report) -> int:  # fmt: skip
    """Lighting power per room from scheduled fixture tags on the plans (#746).

    Each text span on a reflected ceiling plan or electrical floor plan that is
    a lighting schedule tag counts as one fixture of that type. The sheet is
    registered to the architectural plan of its level the way mechanical
    plans are (``mech_tags.register``) and each fixture goes in the room that
    contains it. A room gets its fixtures, the scheduled watts summed and the
    LPD; a fixture type with no scheduled watts is counted but adds nothing
    and is sent to review. Where a level has both an RCP and a lighting plan
    with tags, only the sheet with more tags is used, so fixtures shown on
    both are not counted twice. Returns the number of rooms given an LPD."""
    lighting = {t: e for t, e in sched.items() if e.get("category") == "lighting"}
    if not lighting:
        return 0
    from building_model import FixtureInstance, Provenance, ReviewItem
    from grid_detect import detect_grids
    from mech_tags import find_tags, register, room_of, to_canonical

    known = set(lighting)
    per_level: Dict[str, List[tuple]] = {}
    for f in files:
        st = status[f]
        kind = _val(idx_entries.get(f, {}), "type")
        if not (
            kind in FIXTURE_SHEET_TYPES
            or (_discipline(st) == "electrical" and kind in ("floor_plan", "plan"))
        ):
            continue  # fmt: skip
        if st.stages["ingest"]["kind"] == "raster_only" or not st.level:
            continue
        sheet = json.loads((sheets_dir / f).read_text())
        sheet_id = st.number or f
        try:
            grid = detect_grids(sheet, sheet_id)
        except Exception:  # noqa: BLE001 - no grid means no bubbles to drop
            grid = None
        hits = [h for h in find_tags(sheet, known) if not _in_grid_bubble(h["bbox_pt"], grid)]
        lid = lid_of.get(st.level)
        if hits and lid is not None:
            per_level.setdefault(lid, []).append((len(hits), f, sheet_id, sheet, grid, hits))
        elif hits:
            report.notes.append(
                f"{sheet_id}: {len(hits)} lighting fixture tag(s) on a plan with no matching "
                "architectural level; not counted"
            )
    lit = 0
    no_watts: Dict[str, List[str]] = {}
    for lid, cands in per_level.items():
        cands.sort(key=lambda c: (-c[0], _discipline(status[c[1]]) != "electrical", c[2]))
        n, f, sheet_id, sheet, grid, hits = cands[0]
        for other in cands[1:]:
            report.notes.append(
                f"{other[2]}: {other[0]} lighting fixture tag(s) not counted; {sheet_id} "
                f"shows more ({n}) for the same level"
            )
        if lid not in plan_of_level:
            report.notes.append(
                f"{sheet_id}: {n} lighting fixture tag(s) on a level with no rooms; not counted"
            )
            continue
        af, asid, _li = plan_of_level[lid]
        arch = json.loads((sheets_dir / af).read_text())
        mf = (scales.get(f) or {}).get("m_per_pt")
        ma = (scales.get(af) or {}).get("m_per_pt")
        try:
            ag = detect_grids(arch, asid)
        except Exception:  # noqa: BLE001 - a grid failure falls back to the frame
            ag = None
        dx, dy, how, conf, why = register(sheet, arch, mf, ma, grid, ag)
        if how is None:
            report.notes.append(
                f"{sheet_id}: not registered to {asid} ({why}); fixtures not counted"
            )
            review.append(
                ReviewItem(
                    id=f"rq-lightreg-{sheet_id}",
                    kind="lighting_plan_unregistered",
                    description=(
                        f"{sheet_id} shows {n} lighting fixture tag(s) but does not register "
                        f"to {asid}: {why}; lighting power not taken from the drawings"
                    ),
                    confidence=0.5,
                    provenance=Provenance(sheet_id, 0, "plan_fixture_tags", 0.5),
                    needs_review=True,
                )
            )
            continue
        conf = min(conf, FIXTURE_TAG_CONFIDENCE)
        rooms = [sp for sp in spaces.values() if sp.level_id == lid]
        by_room: Dict[str, List[FixtureInstance]] = {}
        outside = 0
        for h in hits:
            at = to_canonical(h["bbox_pt"], mf, dx, dy, float(arch["height_pt"]), ma)
            sid = room_of(at, rooms)
            if sid is None:
                outside += 1
                continue
            e = lighting[h["tag"]]
            if e.get("watts") is None:
                no_watts.setdefault(h["tag"], []).append(sid)
            fx = by_room.setdefault(sid, [])
            fx.append(
                FixtureInstance(
                    id=f"{sid}-LF{len(fx) + 1}", tag=h["tag"],
                    fixture_class=e.get("description") or "", x_m=at[0], y_m=at[1],
                    watts=e.get("watts"),
                    provenance=Provenance(
                        sheet_id, 0, "plan_fixture_tag", conf, tuple(h["bbox_pt"])
                    ),
                )
            )  # fmt: skip
        for sid, fx in sorted(by_room.items()):
            sp = spaces[sid]
            total = sum(x.watts for x in fx if x.watts is not None)
            sp.lighting.fixtures = fx
            if total <= 0:
                continue  # only types with no watts: no LPD of 0 that hides a default
            sp.lighting.total_w = total
            if sp.area_m2:
                sp.lighting.lpd_w_m2 = total / sp.area_m2
                sp.lighting.lpd_w_ft2 = sp.lighting.lpd_w_m2 / 10.7639104
                lit += 1
            sp.lighting.provenance = Provenance(
                sheet_id, 0, "plan_fixture_tags", conf,
                note=(
                    f"{len(fx)} fixture tag(s) on {sheet_id} x scheduled watts "
                    f"({how}: {why}, to {asid}); one tag counted as one fixture"
                ),
            )  # fmt: skip
        review.append(
            ReviewItem(
                id=f"rq-lighting-{lid}",
                kind="lighting_from_tags",
                target={"kind": "space", "ids": sorted(by_room)},
                description=(
                    f"{lid}: lighting power for {len(by_room)} room(s) from {n} fixture tag(s) "
                    f"on {sheet_id}, one tag counted as one fixture; a group of fixtures "
                    "tagged once is undercounted. Check the fixture counts"
                    + (f"; {outside} tag(s) fall in no room" if outside else "")
                ),
                confidence=conf,
                provenance=Provenance(sheet_id, 0, "plan_fixture_tags", conf),
                needs_review=False,
            )
        )
        report.notes.append(
            f"lighting: {n - outside} fixture tag(s) on {sheet_id} placed in "
            f"{len(by_room)} room(s) of {lid}" + (f"; {outside} in no room" if outside else "")
        )
    for tag, sids in sorted(no_watts.items()):
        review.append(
            ReviewItem(
                id=f"rq-fixture-watts-{tag}",
                kind="fixture_no_watts",
                target={"kind": "space", "ids": sorted(set(sids))},
                description=(
                    f"fixture type {tag} ({lighting[tag].get('schedule_sheet', '?')}) has no "
                    f"watts on the schedule; its {len(sids)} fixture(s) add nothing to the "
                    "rooms' lighting power"
                ),
                confidence=0.5,
                provenance=Provenance(
                    lighting[tag].get("schedule_sheet", ""), 0, "pdf_schedule", 0.5
                ),
                needs_review=False,
            )
        )
    return lit


TERMINAL_KINDS = ("vav", "fcu")  # equipment that defines an HVAC zone


def _terminal_zone(e, locs, conf, spaces, zones, ComponentRef, Provenance, Zone) -> None:
    """One zone for a placed terminal unit, serving the room it sits in."""
    loc, sid = locs[0], e["space_id"]
    prov = Provenance(
        loc["sheet"], 0, "mech_plan_tag", conf, tuple(loc["bbox_pt"]),
        note=(
            f"{e['tag']} scheduled on {e['sheet']} ({e['schedule']}); serves the room it is "
            "tagged in; other rooms on its ductwork need duct tracing"
        ),
    )  # fmt: skip
    ref = ComponentRef(
        id=e["tag"], type=e["kind"], x_m=loc["at_m"][0], y_m=loc["at_m"][1], tag=e["tag"],
        provenance=prov,
    )  # fmt: skip
    zid = f"{e['level_id']}-Z-{e['tag']}"
    mx, mn, why = _zone_airflow(e)
    prov.note += f"; {why}"
    zones[zid] = Zone(
        id=zid, level_id=e["level_id"], space_ids=[sid], terminal_unit=ref, provenance=prov,
        design_airflow_max_m3s=mx, design_airflow_min_m3s=mn,
    )  # fmt: skip
    sp = spaces[sid]
    sp.hvac.zone_ids.append(zid)
    sp.hvac.terminal_units.append(ref)
    e["zone_id"] = zid


# airflow unit -> m3/s (1 cfm = 0.3048**3 / 60 m3/s; 1 L/s = 0.001 m3/s)
AIRFLOW_M3S = {"cfm": 0.3048**3 / 60.0, "l/s": 0.001}


def _zone_airflow(e):
    """(max m3/s, min m3/s, note) for a terminal unit's schedule row (#746).

    Max is the row's MAX airflow, or its single airflow column when the unit
    has only one (a fan coil); min is its MIN airflow. Nothing is converted
    when the airflow headers state no unit (``AIRFLOW`` alone).
    """
    hi = e.get("cfm_max") if e.get("cfm_max") is not None else e.get("cfm")
    lo = e.get("cfm_min")
    if hi is None and lo is None:
        return None, None, "no airflow on its schedule row"
    if hi is not None and hi <= 0:
        return None, None, "schedule row max airflow is not positive, not used"
    k = AIRFLOW_M3S.get(e.get("airflow_unit") or "")
    if k is None:
        return None, None, "airflow on its schedule row states no unit, not used"
    mx = round(hi * k, 6) if hi is not None else None
    mn = round(lo * k, 6) if lo is not None else None
    if mx is not None and mn is not None and mn > mx:
        return None, None, "schedule row min airflow exceeds max, not used"
    unit = e["airflow_unit"].upper() if e["airflow_unit"] == "l/s" else "CFM"
    parts = [f"{lbl} {v:g} {unit}" for lbl, v in (("max", hi), ("min", lo)) if v is not None]
    return mx, mn, "design airflow " + ", ".join(parts) + " from its schedule row"


def _ground_level_spaces(model):
    """Spaces with polygons on the lowest level: the slab (``_slab_geometry``)."""
    spaces = [sp for sp in (model.spaces or {}).values() if len(sp.polygon_m or []) >= 3]
    if not spaces:
        return []
    elev = {lv.id: lv.elevation_z_m for lv in (model.levels or [])}
    low = min(elev.get(sp.level_id, 0.0) for sp in spaces)
    return [sp for sp in spaces if abs(elev.get(sp.level_id, 0.0) - low) < 1e-6]


def _heated_slab(model, equipment, review, report, Provenance, ReviewItem) -> None:
    """A radiant floor / in-slab heating row on a mechanical schedule marks the
    ground slab heated (#747), so the construction library uses the Table 5.5
    Heated slab F-factor. When every heated row names the rooms it serves and
    each is a ground-level room, only those rooms' slab is heated
    (``model.slab_heated_spaces``); otherwise the whole slab is treated as
    heated. Either way it goes to review."""
    from construction_library import heated_slab_evidence, heated_slab_rooms

    why = heated_slab_evidence(equipment)
    if not why:
        return
    model.slab_heated_by = why
    rooms = heated_slab_rooms(equipment) or []
    ground = _ground_level_spaces(model)
    by_no = {}
    for sp in ground:
        if sp.number:
            by_no.setdefault(sp.number.upper(), []).append(sp.id)
    missing = [r for r in rooms if r not in by_no]
    if rooms and not missing:
        model.slab_heated_spaces = sorted(i for r in rooms for i in by_no[r])
        extent = (
            "only the slab under room(s) "
            + ", ".join(rooms)
            + " (the rooms the schedule says it serves) gets the Table 5.5 "
            "heated-slab F-factor, the rest the unheated one"
        )
        report.notes.append(f"heated slab rooms: {', '.join(rooms)}")
    else:
        extent = "the whole ground slab gets the Table 5.5 heated-slab F-factor"
        if missing:
            extent += (
                f" (the schedule says it serves {', '.join(rooms)}, but "
                f"{', '.join(missing)} "
                + (
                    "is not a ground-floor room)"
                    if len(missing) == 1
                    else "are not ground-floor rooms)"
                )
            )
    report.notes.append(f"heated slab: {why}")
    sheet = next((e.get("sheet", "") for e in equipment if e.get("sheet")), "")
    review.append(
        ReviewItem(
            id="rq-heated-slab",
            kind="heated_slab",
            description=f"{why}; {extent}. Check how much of the slab is heated",
            confidence=0.6,
            provenance=Provenance(sheet, 0, "pdf_schedule_heated_slab", 0.6),
            needs_review=True,
        )
    )


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
                        provenance=Provenance(sheet_id, 0, x.get("method", "pdf_ruled_table"), 0.5),
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
                            provenance=Provenance(
                                sheet_id, 0, x.get("method", "pdf_ruled_table"), 0.5
                            ),
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
    wall_tags=(),
    plan_walls_by_id=None,
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
        # doors drawn inside a storefront run (#793): a window row as wide as
        # the run less those doors is the glass alone, and the doors are
        # modelled as doors beside it
        in_glass = [d for d in op.get("doors_in_glazing") or [] if d.get("width_m")]
        glass_w = width - sum(float(d["width_m"]) for d in in_glass)
        less_doors = False
        if in_glass and not cands:
            cands = [
                (t, e)
                for t, e in sized
                if e["category"] == "window" and abs(e["width_m"] - glass_w) <= WIDTH_TOL_M
            ]
            less_doors = bool(cands)
        # a scheduled tag written next to the gap names its row (#793); the
        # nearest scheduled one wins over a nearer wall-type mark (#829)
        near = [t["tag"] for t in op.get("tags_near") or []] or [op.get("tag_text") or ""]
        tag = next((t for t in near if t in dict(sized)), near[0])
        row = dict(sized).get(tag)
        tag_why = ""
        if row is not None:
            if drawn and row["category"] != drawn:
                tag_why = f"tag {tag} is a scheduled {row['category']} but the plan draws a {drawn}"
            elif abs(row["width_m"] - width) <= WIDTH_TOL_M:
                less_doors = False
            elif (
                in_glass
                and row["category"] == "window"
                and abs(row["width_m"] - glass_w) <= WIDTH_TOL_M
            ):
                less_doors = True
            else:
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
        door_ivs = []
        if less_doors:
            # the glass is the run less its doors: centre it on what is left
            door_ivs = [_door_interval(d, ls) for d in in_glass]
            s = _glass_centre(s, float(op["width_m"]), door_ivs)
        prov = Provenance(
            sheet_id=sheet_id,
            revision=0,
            method="plan_gap_tag" if row is not None else "plan_gap_schedule_width",
            confidence=(0.85 if row is not None else 0.8 if len(tags) == 1 else 0.7)
            - (0.05 if less_doors else 0.0),
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
                + (
                    f"; scheduled {width:.3f} m is the glass less the "
                    f"{len(in_glass)} door{'s' if len(in_glass) > 1 else ''} drawn in it "
                    "(matched after taking the doors out, confidence lowered)"
                    if less_doors
                    else f"; includes the {len(in_glass)} door"
                    f"{'s' if len(in_glass) > 1 else ''} drawn in it"
                    if in_glass
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
        for j, (d, (ds, dw)) in enumerate(zip(in_glass, door_ivs)):
            _glass_door(
                f"{oid}-D{j + 1}", d, ds, dw, wall, sp, sized, sheet_id, review, counts,
                constructions, centers, tags[0],
            )  # fmt: skip
    _wall_tag_openings(
        lid, wall_tags, plan_walls_by_id or {}, lines, spaces, dict(sized), sheet_id,
        review, constructions, centers, counts, len(plan_openings),
    )  # fmt: skip
    _thin_band_review(
        lid, wall_tags, plan_walls_by_id or {}, lines, spaces, dict(sized), sheet_id, review,
        counts, len(plan_openings) + len(wall_tags),
    )  # fmt: skip
    return counts


def _door_interval(d: dict, ls) -> Tuple[float, float]:
    """(centre along the wall, width) of a door drawn inside a storefront run."""
    (ax, ay), (bx, by) = d["a_m"], d["b_m"]
    return ls.project(Point((ax + bx) / 2, -(ay + by) / 2)), float(d["width_m"])


def _glass_centre(s: float, run_w: float, doors: List[Tuple[float, float]]) -> float:
    """Centre of the glass left in a run [s - w/2, s + w/2] once its doors are cut out."""
    lo, hi = s - run_w / 2, s + run_w / 2
    pieces, cur = [], lo
    for ds, dw in sorted(doors):
        a, b = max(lo, ds - dw / 2), min(hi, ds + dw / 2)
        if a > cur:
            pieces.append((cur, a))
        cur = max(cur, b)
    if hi > cur:
        pieces.append((cur, hi))
    total = sum(b - a for a, b in pieces)
    if total <= 0:
        return s
    return sum((a + b) / 2 * (b - a) for a, b in pieces) / total


def _glass_door(
    oid, d, ds, dw, wall, sp, sized, sheet_id, review, counts, constructions, centers, glass_tag
):
    """A door drawn inside a storefront whose schedule row leaves it out (#793):
    modelled as a door when every scheduled door of its width agrees on height,
    otherwise sent to review like any unsized gap. Never invented."""
    from building_model import Provenance, ReviewItem, SpaceOpening

    cands = [
        (t, e)
        for t, e in sized
        if e["category"] == "door" and abs(e["width_m"] - dw) <= WIDTH_TOL_M
    ]
    heights = {round(e["height_m"], 3) for _t, e in cands}
    if len(heights) != 1:
        counts["unsized"] += 1
        why = (
            "no scheduled door is this wide"
            if not cands
            else "scheduled doors of this width disagree on height ("
            + ", ".join(t for t, _e in cands)
            + ")"
        )
        review.append(
            ReviewItem(
                id=f"rq-{oid}",
                kind="opening_unsized",
                target={
                    "kind": "wall",
                    "id": wall.id,
                    "field": "opening",
                    "gap": {
                        "opening_id": oid,
                        "space_id": wall.space_id,
                        "facade": wall.facade,
                        "s_center_m": round(ds, 4),
                        "width_m": round(dw, 4),
                        "drawn": "door",
                        "candidates": [t for t, _e in cands],
                        "sheet_id": sheet_id,
                    },
                },
                description=(
                    f"{oid}: {dw:.2f} m door drawn in storefront {glass_tag} on "
                    f"{wall.facade} wall {wall.id}; {why}; not modelled"
                ),
                confidence=0.5,
                provenance=Provenance(sheet_id, 0, "plan_walls_vector", 0.5),
                needs_review=True,
            )
        )
        return
    tags = [t for t, _e in cands]
    width, height = float(cands[0][1]["width_m"]), heights.pop()
    sp.openings.append(
        SpaceOpening(
            id=oid,
            tag=tags[0],
            category="door",
            width_m=width,
            height_m=height,
            host_facade=wall.facade,
            host_interval_m=[round(ds - width / 2, 4), round(ds + width / 2, 4)],
            s_center_m=round(ds, 4),
            area_m2=width * height,
            provenance=Provenance(
                sheet_id=sheet_id,
                revision=0,
                method="plan_glazing_door",
                confidence=0.75 if len(tags) == 1 else 0.7,
                note=(
                    f"door {dw:.3f} m drawn between jambs in storefront {glass_tag}, "
                    f"matched schedule {'/'.join(tags)} by width; the storefront row "
                    "is the glass alone"
                ),
            ),
            needs_review=False,
            construction_id=(
                _schedule_construction(constructions, "door", tags, cands, sheet_id, review, oid)
                if constructions is not None
                else ""
            ),
        )
    )
    counts["modelled"] += 1
    if centers is not None:
        (ax, ay), (bx, by) = d["a_m"], d["b_m"]
        centers[oid] = ((ax + bx) / 2, -(ay + by) / 2)


# A scheduled window whose width spans the whole wall it is tagged on is a
# storefront drawn as a plain wall band (#793). The schedule row's width may be
# the rough opening between the corners, so up to one wall thickness short.
WALL_TAG_CONFIDENCE = 0.7


def _wall_type_legend(files, status, sheets_dir, sched):
    """Wall-type legend read off every vector sheet (``wall_types``, #747)."""
    from wall_types import read_legend

    def sheets():
        for f in files:
            if status[f].stages["ingest"]["kind"] == "raster_only":
                continue
            try:
                yield status[f].number or f, json.loads((sheets_dir / f).read_text())
            except (OSError, ValueError, TypeError):
                continue

    if sheets_dir is None:
        return {}, {}
    return read_legend(sheets(), exclude=set(sched or ()))


def _assign_wall_types(lid, plan, env_walls, legend, constructions, review, sheet_id) -> int:
    """Envelope walls tagged with a legend wall type get its construction (#747).

    A tag sits on a plan wall (``wall_tags``, or any of an opening's
    ``tags_near`` that is not a door/window mark); the tag's foot on that wall maps to the
    envelope wall within ``EXTERIOR_TOL_M`` of it. The construction is the legend
    description with no U: the cited library fills it from Table 5.5. One
    envelope wall tagged with two different types goes to review and gets
    neither. Returns the number of envelope walls assigned.
    """
    from building_model import Construction, Provenance, ReviewItem
    from wall_types import LEGEND_CONFIDENCE

    if not legend or not env_walls:
        return 0
    by_id = {w["id"]: w for w in plan.get("walls", [])}
    hits = [
        (t.get("tag_text") or "", t.get("wall"), t.get("point_m"))
        for t in plan.get("wall_tags", [])
    ]
    hits += [
        (t, (o.get("walls") or [None])[0], o.get("a_m"))
        for o in plan.get("openings", [])
        for t in ([x["tag"] for x in o.get("tags_near") or []] or [o.get("tag_text") or ""])
    ]
    lines = [(w, LineString([w.from_m, w.to_m])) for w in env_walls]
    tags_on: Dict[str, set] = {}
    for tag, wid, at in hits:
        pw = by_id.get(wid)
        if tag not in legend or pw is None or not at:
            continue
        # the tag's foot on its plan wall decides which envelope edge it names
        (ax, ay), (bx, by) = pw["a_m"], pw["b_m"]
        wl = LineString([(ax, -ay), (bx, -by)])  # y-up -> canonical y-down
        foot = wl.interpolate(wl.project(Point(at[0], -at[1])))
        env, ls = min(lines, key=lambda t: t[1].distance(foot))
        if ls.distance(foot) <= EXTERIOR_TOL_M:
            tags_on.setdefault(env.id, set()).add(tag)
    n = 0
    for env, _ls in lines:
        tags = tags_on.get(env.id)
        if not tags:
            continue
        if len(tags) > 1:
            review.append(
                ReviewItem(
                    id=f"rq-{env.id}-walltype",
                    kind="wall_type_ambiguous",
                    target={"kind": "wall", "id": env.id},
                    description=(
                        f"{env.id} ({env.facade}) is tagged with wall types "
                        f"{', '.join(sorted(tags))}; no wall type used"
                    ),
                    confidence=0.5,
                    provenance=Provenance(sheet_id, 0, "wall_type_legend", 0.5),
                    needs_review=True,
                )
            )
            continue
        (tag,) = tags
        row = legend[tag]
        cid = f"LEG-{tag}"
        if cid not in constructions:
            constructions[cid] = Construction(
                id=cid,
                name=f"{tag}: {row['description']}",
                u_value_w_m2k=None,
                provenance=Provenance(
                    row["sheet_id"],
                    0,
                    "wall_type_legend",
                    LEGEND_CONFIDENCE,
                    note=(
                        f"wall type {tag} on the legend of {row['sheet_id']}: "
                        f"{row['description']!r} ({row['construction_type']}, {row['why']}); "
                        + (
                            f"full legend text {row['full_text']!r}; "
                            if row.get("full_text") and row["full_text"] != row["description"]
                            else ""
                        )
                        + "U from the construction library"
                    ),
                ),
            )
        env.construction_id = cid
        n += 1
    return n


def _wall_tag_openings(
    lid, wall_tags, plan_walls, lines, spaces, sized, sheet_id, review, constructions,
    centers, counts, n_gaps,
) -> None:  # fmt: skip
    """Tags on exterior walls with no opening drawn (``plan_walls`` ``wall_tags``).

    A scheduled window as wide as the wall (within ``WIDTH_TOL_M`` over, one
    wall thickness under) is modelled on it at the scheduled size. Any other
    scheduled door or window tag on such a wall goes to review as a possible
    opening the plan does not draw, with what a review edit needs to add it
    (#798). Unscheduled tags and interior walls are left alone.
    """
    from building_model import Provenance, ReviewItem, SpaceOpening

    for k, wt in enumerate(wall_tags):
        tag = wt.get("tag_text") or ""
        row = sized.get(tag)
        pw = plan_walls.get(wt.get("wall"))
        if row is None or pw is None or not lines:
            continue
        (ax, ay), (bx, by) = pw["a_m"], pw["b_m"]
        mid = Point((ax + bx) / 2, -(ay + by) / 2)  # y-up -> canonical y-down
        wall, ls = min(lines, key=lambda t: t[1].distance(mid))
        if ls.distance(mid) > EXTERIOR_TOL_M:
            continue
        length, thick = float(pw["length_m"]), float(pw.get("thickness_m") or 0)
        width, height = float(row["width_m"]), float(row["height_m"])
        full = row["category"] == "window" and (
            length - thick - WIDTH_TOL_M <= width <= length + WIDTH_TOL_M
        )
        oid = f"{lid}-OP{n_gaps + k + 1}"
        s = ls.project(mid)
        if not full:
            counts["unsized"] += 1
            why = (
                f"scheduled {row['category']} {tag} ({width:.2f} m) is tagged on this "
                f"{length:.2f} m wall but no opening is drawn; may be "
                + ("glazing" if row["category"] == "window" else "a door")
            )
            review.append(
                ReviewItem(
                    id=f"rq-{oid}",
                    kind="opening_unsized",
                    target={
                        "kind": "wall",
                        "id": wall.id,
                        "field": "opening",
                        "gap": {
                            "opening_id": oid,
                            "space_id": wall.space_id if wall.space_id in spaces else "",
                            "facade": wall.facade,
                            "s_center_m": round(s, 4),
                            "width_m": round(min(width, length), 4),
                            "drawn": "",
                            "candidates": [tag],
                            "sheet_id": sheet_id,
                        },
                    },
                    description=f"{oid}: {wall.facade} wall {wall.id}: {why}; not modelled",
                    confidence=0.5,
                    provenance=Provenance(sheet_id, 0, "plan_wall_tag", 0.5),
                    needs_review=True,
                )
            )
            continue
        sp = spaces.get(wall.space_id)
        if sp is None:
            continue
        prov = Provenance(
            sheet_id=sheet_id,
            revision=0,
            method="plan_wall_tag",
            confidence=WALL_TAG_CONFIDENCE,
            note=(
                f"storefront: window {tag} scheduled {width:.3f} m wide, tagged on a "
                f"{length:.3f} m wall with no opening drawn ({wt.get('tag_dist_m', 0):.2f} m away)"
            ),
        )
        sp.openings.append(
            SpaceOpening(
                id=oid,
                tag=tag,
                category="window",
                width_m=width,
                height_m=height,
                host_facade=wall.facade,
                host_interval_m=[round(s - width / 2, 4), round(s + width / 2, 4)],
                s_center_m=round(s, 4),
                area_m2=width * height,
                provenance=prov,
                needs_review=False,
                construction_id=(
                    _schedule_construction(
                        constructions, "window", [tag], [(tag, row)], sheet_id, review, oid
                    )
                    if constructions is not None
                    else ""
                ),
            )
        )
        counts["exterior"] += 1
        counts["modelled"] += 1
        if centers is not None:
            centers[oid] = (mid.x, mid.y)


def _thin_band_review(
    lid, wall_tags, plan_walls, lines, spaces, sized, sheet_id, review, counts, n_before,
) -> None:  # fmt: skip
    """Exterior plan walls flagged ``maybe_glazing`` go to review (#793).

    A thin wall band between thicker walls, with no opening drawn and no
    scheduled tag on it, may be storefront glazing. Nothing is modelled from
    the band alone (no schedule row says how tall or which product), so the
    wall stays opaque and a review item carries the gap a review edit needs to
    add it. A band with a scheduled tag is left to ``_wall_tag_openings``.
    """
    from building_model import Provenance, ReviewItem

    tagged = {wt.get("wall") for wt in wall_tags if (wt.get("tag_text") or "") in sized}
    flagged = [w for w in plan_walls.values() if w.get("maybe_glazing")]
    for k, pw in enumerate(sorted(flagged, key=lambda w: w["id"])):
        if pw["id"] in tagged or not lines:
            continue
        (ax, ay), (bx, by) = pw["a_m"], pw["b_m"]
        mid = Point((ax + bx) / 2, -(ay + by) / 2)  # y-up -> canonical y-down
        wall, ls = min(lines, key=lambda t: t[1].distance(mid))
        if ls.distance(mid) > EXTERIOR_TOL_M:
            continue  # interior: does not touch the envelope
        length, thick = float(pw["length_m"]), float(pw.get("thickness_m") or 0)
        oid = f"{lid}-OP{n_before + k + 1}"
        counts["unsized"] += 1
        review.append(
            ReviewItem(
                id=f"rq-{oid}",
                kind="opening_unsized",
                target={
                    "kind": "wall",
                    "id": wall.id,
                    "field": "opening",
                    "gap": {
                        "opening_id": oid,
                        "space_id": wall.space_id if wall.space_id in spaces else "",
                        "facade": wall.facade,
                        "s_center_m": round(ls.project(mid), 4),
                        "width_m": round(length, 4),
                        "drawn": "thin_band",
                        "candidates": [],
                        "sheet_id": sheet_id,
                    },
                },
                description=(
                    f"{oid}: {wall.facade} wall {wall.id}: a {length:.2f} m stretch drawn "
                    f"{thick:.2f} m thick between thicker walls, no opening or scheduled tag; "
                    "may be storefront glazing; modelled opaque"
                ),
                confidence=0.4,
                provenance=Provenance(sheet_id, 0, "plan_walls_vector", 0.4),
                needs_review=True,
            )
        )


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

    # #818: a facade drawn on several sheets (or in parts) is joined as a whole.
    # Each read matches the plan openings it covers; a plan opening goes to
    # review only when no read of its facade shows it, and one shown by two
    # reads that disagree goes to review once, naming both sheets.
    def covers(er, s):
        sp = er.span_m
        return sp is None or sp[0] - JOIN_TOL_M <= s <= sp[1] + JOIN_TOL_M

    per_read = []  # (er, prov, stats, pairs)
    shown: dict = {}  # plan opening id -> [(er, eo, prov)]
    for er, prov in reads:
        stats = {"matched": 0, "unmatched_elevation": 0, "unmatched_plan": 0}
        pairs = {}
        per_read.append((er, prov, stats, pairs))
        cands = [(op, s) for op, s in by_fac.get(er.facade, []) if covers(er, s)]
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
            seen = shown.setdefault(op.id, [])
            seen.append((er, eo, prov))
            op.history.append(
                Provenance(
                    sheet_id=er.sheet_id,
                    revision=0,
                    method="elevation_join",
                    confidence=prov.confidence,
                    note=(
                        f"{eo.id}: sill {0.0 if eo.kind == 'door' else eo.sill_m:.2f} m "
                        f"read off the {er.facade} elevation "
                        f"({(er.registration or {}).get('method', '')} registration)"
                        + ("" if len(seen) == 1 else f"; sill kept from {seen[0][0].sheet_id}")
                    ),
                )
            )
            if len(seen) > 1:
                continue  # sill and size already taken from the first sheet showing it
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
    # an opening drawn on two sheets: one item when they disagree
    for op_id, seen in shown.items():
        (er1, eo1, prov1), rest = seen[0], seen[1:]
        for er2, eo2, _p in rest:
            why = []
            if eo1.kind != "door" and abs(eo1.sill_m - eo2.sill_m) > JOIN_HEIGHT_TOL_M:
                why.append(f"sill {eo1.sill_m:.2f} m vs {eo2.sill_m:.2f} m")
            if abs(eo1.width_m - eo2.width_m) > JOIN_WIDTH_TOL_M:
                why.append(f"width {eo1.width_m:.2f} m vs {eo2.width_m:.2f} m")
            if abs(eo1.height_m - eo2.height_m) > JOIN_HEIGHT_TOL_M:
                why.append(f"height {eo1.height_m:.2f} m vs {eo2.height_m:.2f} m")
            if not why:
                continue
            op = next(o for o, _s in by_fac.get(er1.facade, []) if o.id == op_id)
            review.append(
                ReviewItem(
                    id=f"rq-elev-{op_id}-{er1.sheet_id}-{er2.sheet_id}",
                    kind="elevation_conflict",  # two elevations draw it differently
                    target={
                        "kind": "opening",
                        "id": op_id,
                        "sheet": er1.sheet_id,
                        "ends": [elev_end(er1, eo1), elev_end(er2, eo2), plan_end_of(op)],
                    },  # fmt: skip
                    description=(
                        f"{op_id} ({op.tag}) is drawn on elevations {er1.sheet_id} and "
                        f"{er2.sheet_id} with {'; '.join(why)}; kept {er1.sheet_id}"
                    ),
                    confidence=0.6,
                    provenance=prov1,
                )
            )
            break  # once per opening
    # plan openings no elevation of their facade shows
    for fac, ops in by_fac.items():
        on_fac = [t for t in per_read if t[0].facade == fac]
        if not on_fac:
            continue
        for op, s in ops:
            if op.id in shown:
                continue
            cov = [t for t in on_fac if covers(t[0], s)]
            if cov:
                for t in cov:
                    t[2]["unmatched_plan"] += 1
                er, prov = cov[0][0], cov[0][1]
                names = " or ".join(t[0].sheet_id for t in cov)
                where = f"elevation {names}"
            else:
                er, prov = on_fac[0][0], on_fac[0][1]
                spans = ", ".join(
                    f"{t[0].sheet_id} {t[0].span_m[0]:.2f}-{t[0].span_m[1]:.2f} m" for t in on_fac
                )
                where = f"any {fac} elevation (they cover {spans})"
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
                        f"{op.id} ({op.tag}, {op.category} at {s:.2f} m along the {fac} "
                        f"facade) is not drawn on {where}; sill unknown"
                    ),
                    confidence=0.6,
                    provenance=prov,
                )
            )
    for er, prov, stats, pairs in per_read:
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
