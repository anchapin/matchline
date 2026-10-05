"""Closed wall loops no IfcSpace claims (#581).

Shafts and closets are often not modelled as IfcSpaces, so their area drops
out of the floor-area balance and the shaft rule never sees them. After wall
ends are joined onto neighbour centrelines (#575), the wall centrelines on a
level close into loops. A loop whose inside (the loop shrunk by half the
level's typical wall thickness) is almost uncovered by IfcSpace footprints is
reported as a review item with its polygon, area and a candidate class:
"shaft" when an unfilled slab opening sits inside it, otherwise "unknown"
(it may also be a courtyard or a modelling gap). No space is created:
matchline never guesses.

Idea from the Pascal editor IFC converter (room-first.ts materializeRooms,
commit 67f8041; MIT, Copyright (c) 2026 Pascal Group Inc.), which turns such
loops into rooms; matchline only flags them. No code ported.
"""

from __future__ import annotations

import re
import statistics

from building_model import Provenance

MIN_LOOP_AREA_M2 = 0.25  # smaller faces are join slivers, not rooms
UNCLAIMED_MAX_COVER = 0.10  # a loop is unclaimed when spaces cover less than this
SHAFT_VOID_COVER = 0.5  # share of a slab opening that must lie inside the loop
_EXTEND_M = 0.002  # so T ends that sit a hair off the run still node
REVIEW_CONFIDENCE = 0.5
METHOD = "ifc_import:tier1:wall_loop"
ENVELOPE_METHOD = "ifc_import:tier0:envelope"
KIND = "unclaimed_wall_loop"


def unclaimed_loops(segments, space_polys, void_polys=(), half_thickness=0.0):
    """Unclaimed loops on one level, as dicts, in a stable order.

    segments: [((x0, y0), (x1, y1)), ...] wall centrelines.
    space_polys: [[(x, y), ...], ...] IfcSpace footprints on the level.
    void_polys: [[(x, y), ...], ...] unfilled slab openings on the level.
    """
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union

    lines = []
    for (x0, y0), (x1, y1) in segments:
        ln = LineString([(x0, y0), (x1, y1)])
        if ln.length < 1e-6:
            continue
        dx, dy = (x1 - x0) / ln.length, (y1 - y0) / ln.length
        lines.append(
            LineString(
                [
                    (x0 - dx * _EXTEND_M, y0 - dy * _EXTEND_M),
                    (x1 + dx * _EXTEND_M, y1 + dy * _EXTEND_M),
                ]
            )
        )
    if len(lines) < 3:
        return []
    spaces = []
    for pts in space_polys:
        if len(pts) >= 3:
            pg = Polygon(pts)
            if not pg.is_valid:
                pg = pg.buffer(0)
            if not pg.is_empty:
                spaces.append(pg)
    covered = unary_union(spaces) if spaces else None
    voids = [Polygon(v) for v in void_polys if len(v) >= 3]

    out = []
    for face in polygonize(unary_union(lines)):
        if face.area < MIN_LOOP_AREA_M2:
            continue
        inner = face.buffer(-half_thickness, join_style=2) if half_thickness > 0 else face
        if inner.is_empty or inner.area <= 0:
            inner = face
        cover = covered.intersection(inner).area / inner.area if covered is not None else 0.0
        if cover >= UNCLAIMED_MAX_COVER:
            continue
        cls = "unknown"
        for v in voids:
            if v.area > 0 and v.intersection(face).area >= SHAFT_VOID_COVER * v.area:
                cls = "shaft"
                break
        ring = [(round(x, 4), round(y, 4)) for x, y in list(face.exterior.coords)[:-1]]
        out.append(
            {
                "polygon_m": ring,
                "area_m2": round(face.area, 4),
                "inner_area_m2": round(inner.area, 4),
                "space_cover": round(cover, 4),
                "candidate": cls,
            }
        )
    out.sort(key=lambda r: (min(p[0] for p in r["polygon_m"]), min(p[1] for p in r["polygon_m"])))
    return out


def flag_unclaimed_wall_loops(model, thickness_by_gid, voids_by_level, sheet="", revision=0):
    """Review item per unclaimed loop on every level. Returns (count, total centreline m2)."""
    by_level = {}
    for w in model.envelope:
        p = w.provenance
        if p is None or p.method != ENVELOPE_METHOD or len(w.from_m) < 2 or len(w.to_m) < 2:
            continue
        lvl = w.id.split("-EW")[0] if "-EW" in w.id else ""
        tok = (p.note or "").split(" ", 1)[0]
        gid = tok[len("GlobalId=") :] if tok.startswith("GlobalId=") else ""
        by_level.setdefault(lvl, []).append(
            ((tuple(w.from_m), tuple(w.to_m)), thickness_by_gid.get(gid))
        )
    n, total = 0, 0.0
    for lvl in sorted(by_level):
        segs = [s for s, _ in by_level[lvl]]
        ts = [t for _, t in by_level[lvl] if t and t > 0]
        half = 0.5 * statistics.median(ts) if ts else 0.0
        spaces = [
            sp.polygon_m
            for sp in model.spaces.values()
            if sp.level_id == lvl and sp.polygon_m and len(sp.polygon_m) >= 3
        ]
        for r in unclaimed_loops(segs, spaces, voids_by_level.get(lvl, ()), half):
            n += 1
            total += r["area_m2"]
            what = (
                "an unfilled slab opening sits inside it: likely an unmodelled shaft"
                if r["candidate"] == "shaft"
                else "could be an unmodelled shaft or closet, a courtyard, or a modelling gap"
            )
            model.flag_for_review(
                KIND,
                f"{lvl}: closed wall loop of {r['area_m2']:.2f} m^2 at wall centrelines "
                f"(about {r['inner_area_m2']:.2f} m^2 inside the walls) that no IfcSpace "
                f"claims; {what}. Not added as a space.",
                REVIEW_CONFIDENCE,
                Provenance(
                    sheet_id=sheet,
                    revision=revision,
                    method=METHOD,
                    confidence=REVIEW_CONFIDENCE,
                    note=(
                        f"level={lvl} area_m2={r['area_m2']:.4f} "
                        f"inner_area_m2={r['inner_area_m2']:.4f} "
                        f"space_cover={r['space_cover']:.4f} candidate={r['candidate']} "
                        f"polygon_m={r['polygon_m']}"
                    ),
                ),
            )
    return n, round(total, 4)


_AREA_RE = re.compile(r"\barea_m2=([0-9.]+)")


def unclaimed_loop_area(model):
    """(count, total m2) of open unclaimed-loop review items on ``model``."""
    n, total = 0, 0.0
    for it in getattr(model, "review_queue", []) or []:
        if it.kind != KIND or it.status == "rejected":
            continue
        m = _AREA_RE.search((it.provenance.note if it.provenance else "") or "")
        if m:
            n += 1
            total += float(m.group(1))
    return n, total
