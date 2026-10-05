"""Fold closets and shafts into the room they belong to.

The rule (decided by the project owner, 2026-10-04):

* a **closet** merges into the adjacent space its door opens onto;
* a **shaft** merges into the adjacent space with the greatest percentage of
  shared wall area (shared wall area / the shaft's total wall area).

Nothing is guessed. A closet with no located door, a door that opens onto no
room or onto more than one, a shaft with no room neighbour or a tie for the
largest share all keep the space as it is and put an item on the review
queue naming why. Only spaces with ``poly_type == "room"`` receive merges, so
a closet off a shaft, or a shaft between two cores, stays put.

Doors are read from ``SpaceOpening`` records with ``category == "door"`` and
a known ``plan_center_m`` (plus any doors passed in explicitly). Doors with no
plan position (the drawing path today records count, not position) cannot
connect a closet to anything, so such closets are kept and flagged.

Interior faces of two rooms are usually a wall thickness apart, so
"adjacent" means within ``ADJ_TOL_M``. When the merged footprint would be two
pieces separated by that wall, the gap is closed (a morphological close with
square joins) and the absorbed wall strip area is recorded on the merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from building_model import BuildingModel, Provenance, Space

SOURCE = "space_merge"
METHOD_CLOSET = "space_merge:closet_door"
METHOD_SHAFT = "space_merge:shaft_shared_wall"
# Largest interior-face gap still read as one shared wall (partition
# thickness); also how far a door's plan centre may sit from a face.
ADJ_TOL_M = 0.35
DOOR_TOL_M = 0.35
# Two neighbours whose shared-wall shares differ by less than this are a tie.
TIE_FRAC = 0.005
TARGET_TYPES = frozenset({"room"})
REVIEW_CONFIDENCE = 0.5


@dataclass
class MergeRecord:
    source_id: str
    target_id: str
    rule: str  # METHOD_CLOSET | METHOD_SHAFT
    poly_type: str
    source_area_m2: float
    wall_strip_m2: float = 0.0
    door_ids: List[str] = field(default_factory=list)
    shared_wall_frac: Optional[float] = None
    shared_wall_m2: Optional[float] = None


@dataclass
class KeptRecord:
    space_id: str
    poly_type: str
    reason: str


@dataclass
class MergeResult:
    merged: List[MergeRecord] = field(default_factory=list)
    kept: List[KeptRecord] = field(default_factory=list)


def _poly(sp: Space) -> Optional[Polygon]:
    if not sp.polygon_m or len(sp.polygon_m) < 3:
        return None
    p = Polygon([tuple(v[:2]) for v in sp.polygon_m])
    if not p.is_valid:
        p = p.buffer(0)
    if not isinstance(p, Polygon) or p.is_empty:
        return None
    return p


def _wall_height(model: BuildingModel, sp: Space) -> Optional[float]:
    for lv in model.levels:
        if lv.id == sp.level_id and lv.wall_height_m:
            return float(lv.wall_height_m)
    if sp.volume_m3 and sp.area_m2:
        return sp.volume_m3 / sp.area_m2
    return None


def _collect_doors(model: BuildingModel, doors: Optional[Iterable[dict]]) -> List[dict]:
    out, seen = [], set()
    for sp in model.spaces.values():
        for o in sp.openings:
            if o.category != "door" or not o.plan_center_m:
                continue
            if o.id in seen:
                continue
            seen.add(o.id)
            out.append({"id": o.id, "level_id": sp.level_id, "xy": tuple(o.plan_center_m[:2])})
    for d in doors or ():
        did = d.get("id", "")
        if did in seen or not d.get("plan_center_m"):
            continue
        seen.add(did)
        out.append(
            {"id": did, "level_id": d.get("level_id", ""), "xy": tuple(d["plan_center_m"][:2])}
        )
    return out


def _neighbours(model: BuildingModel, sp: Space) -> List[Space]:
    return [
        o
        for o in model.spaces.values()
        if o.id != sp.id and o.level_id == sp.level_id and o.poly_type in TARGET_TYPES
    ]


def _flag(model: BuildingModel, sp: Space, reason: str, result: MergeResult) -> None:
    result.kept.append(KeptRecord(sp.id, sp.poly_type, reason))
    prov = sp.core_provenance
    model.flag_for_review(
        "space_merge",
        f"{sp.poly_type} {sp.id} kept as its own space: {reason}",
        REVIEW_CONFIDENCE,
        Provenance(
            sheet_id=prov.sheet_id if prov else "",
            revision=prov.revision if prov else 0,
            method=SOURCE,
            confidence=REVIEW_CONFIDENCE,
            note=reason,
        ),
    )


def _joined(a: Polygon, b: Polygon) -> Optional[Polygon]:
    u = unary_union([a, b])
    if isinstance(u, MultiPolygon) or not isinstance(u, Polygon):
        r = ADJ_TOL_M / 2.0
        u = u.buffer(r, join_style=2).buffer(-r, join_style=2)
    if not isinstance(u, Polygon) or u.is_empty or len(u.interiors) > 0:
        return None
    return u


def _ring(p: Polygon) -> list:
    p = p.simplify(1e-6, preserve_topology=True)
    pts = list(p.exterior.coords)[:-1]
    return [[round(x, 6), round(y, 6)] for x, y in pts]


def _absorb(
    model: BuildingModel, src: Space, tgt: Space, joined: Polygon, rec: MergeRecord
) -> None:
    old_area = tgt.area_m2 or 0.0
    h = _wall_height(model, tgt)
    new_area = joined.area
    rec.wall_strip_m2 = round(max(0.0, new_area - (_poly(tgt).area + _poly(src).area)), 6)
    tgt.polygon_m = _ring(joined)
    tgt.area_m2 = round(new_area, 6)
    if tgt.volume_m3 is not None and old_area > 0:
        tgt.volume_m3 = round(new_area * (tgt.volume_m3 / old_area), 6)
    elif tgt.volume_m3 is not None and h:
        tgt.volume_m3 = round(new_area * h, 6)

    tgt.openings.extend(src.openings)
    L, sl = tgt.lighting, src.lighting
    L.fixtures.extend(sl.fixtures)
    L.total_w = (L.total_w or 0.0) + (sl.total_w or 0.0)
    L.unmatched_tags.extend(t for t in sl.unmatched_tags if t not in L.unmatched_tags)
    if L.total_w and tgt.area_m2:
        L.lpd_w_m2 = L.total_w / tgt.area_m2
        L.lpd_w_ft2 = L.lpd_w_m2 / 10.7639
    H, sh = tgt.hvac, src.hvac
    for z in sh.zone_ids:
        if z not in H.zone_ids:
            H.zone_ids.append(z)
    H.diffusers.extend(sh.diffusers)
    H.sensors.extend(sh.sensors)
    H.terminal_units.extend(sh.terminal_units)
    D, sd = tgt.daylight, src.daylight
    D.primary.extend(sd.primary)
    D.secondary.extend(sd.secondary)
    D.toplit.extend(sd.toplit)
    D.unplaced_skylights.extend(sd.unplaced_skylights)
    zp = [Polygon(z.polygon_m) for z in D.toplit if len(z.polygon_m) >= 3]
    D.toplit_m2 = round(unary_union(zp).intersection(joined).area, 6) if zp else 0.0

    for z in model.zones.values():
        if src.id in z.space_ids:
            z.space_ids = [tgt.id if s == src.id else s for s in z.space_ids]
            z.space_ids = list(dict.fromkeys(z.space_ids))
    for seg in model.envelope:
        if getattr(seg, "space_id", "") == src.id:
            seg.space_id = tgt.id

    tgt.merged_from.append(src.id)
    tgt.merged_from.extend(src.merged_from)
    prov = src.core_provenance
    conf = src.poly_type_confidence if src.poly_type_confidence is not None else 1.0
    note = f"absorbed {src.poly_type} {src.id} ({rec.source_area_m2:.2f} m2"
    if rec.wall_strip_m2:
        note += f" + {rec.wall_strip_m2:.2f} m2 wall strip"
    if rec.door_ids:
        note += f"; door {', '.join(rec.door_ids)}"
    if rec.shared_wall_frac is not None:
        note += f"; {rec.shared_wall_frac:.1%} of its wall area shared"
    tgt.history.append(
        Provenance(
            sheet_id=prov.sheet_id if prov else "",
            revision=prov.revision if prov else 0,
            method=rec.rule,
            confidence=conf,
            note=note + ")",
        )
    )
    del model.spaces[src.id]


def _shared_wall_len(p: Polygon, q: Polygon) -> float:
    """Length of p's walls that face q across at most ADJ_TOL_M.

    Each edge of p gets a strip ADJ_TOL_M deep on its outward side; the part
    of q inside that strip, projected onto the edge, is the shared length.
    A neighbour that only touches p at a corner shares nothing.
    """
    p = orient(p, sign=1.0)
    pts = list(p.exterior.coords)
    total = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        nx, ny = uy, -ux  # outward normal of a CCW ring
        t = ADJ_TOL_M
        strip = Polygon(
            [(x0, y0), (x1, y1), (x1 + nx * t, y1 + ny * t), (x0 + nx * t, y0 + ny * t)]
        )
        inter = strip.intersection(q)
        if inter.is_empty or inter.area < 1e-9:
            continue
        proj = [
            (cx - x0) * ux + (cy - y0) * uy
            for g in getattr(inter, "geoms", [inter])
            for cx, cy in g.exterior.coords
        ]
        total += max(0.0, min(L, max(proj)) - max(0.0, min(proj)))
    return total


def _merge_closet(model, sp, doors, result) -> None:
    p = _poly(sp)
    if p is None:
        _flag(model, sp, "no usable polygon", result)
        return
    mine = [
        d
        for d in doors
        if d["level_id"] == sp.level_id and p.exterior.distance(Point(d["xy"])) <= DOOR_TOL_M
    ]
    if not mine:
        _flag(model, sp, "no door with a known plan position on its boundary", result)
        return
    targets: dict = {}
    for d in mine:
        pt = Point(d["xy"])
        hits = [
            n
            for n in _neighbours(model, sp)
            if (q := _poly(n)) is not None and q.distance(pt) <= DOOR_TOL_M
        ]
        if len(hits) > 1:
            names = ", ".join(sorted(n.id for n in hits))
            _flag(model, sp, f"door {d['id']} touches more than one room ({names})", result)
            return
        if hits:
            targets.setdefault(hits[0].id, []).append(d["id"])
    if not targets:
        _flag(model, sp, "its door opens onto no room (exterior or non-room space)", result)
        return
    if len(targets) > 1:
        _flag(
            model, sp, f"doors open onto more than one room ({', '.join(sorted(targets))})", result
        )
        return
    ((tid, dids),) = targets.items()
    tgt = model.spaces[tid]
    joined = _joined(_poly(tgt), p)
    if joined is None:
        _flag(model, sp, f"could not join its outline to {tid}", result)
        return
    rec = MergeRecord(
        sp.id, tid, METHOD_CLOSET, sp.poly_type, round(p.area, 6), door_ids=sorted(dids)
    )
    _absorb(model, sp, tgt, joined, rec)
    result.merged.append(rec)


def _merge_shaft(model, sp, result) -> None:
    p = _poly(sp)
    if p is None:
        _flag(model, sp, "no usable polygon", result)
        return
    h = _wall_height(model, sp) or 1.0
    wall_area = p.exterior.length * h
    shares = []
    for n in _neighbours(model, sp):
        q = _poly(n)
        if q is None:
            continue
        shared_len = _shared_wall_len(p, q)
        if shared_len > 1e-6:
            shares.append((shared_len * h / wall_area, shared_len * h, n))
    if not shares:
        _flag(model, sp, "no adjacent room shares a wall with it", result)
        return
    shares.sort(key=lambda t: (-t[0], t[2].id))
    if len(shares) > 1 and shares[0][0] - shares[1][0] < TIE_FRAC:
        _flag(
            model,
            sp,
            f"tie for largest shared wall area between {shares[0][2].id} and {shares[1][2].id} "
            f"({shares[0][0]:.1%} vs {shares[1][0]:.1%})",
            result,
        )
        return
    frac, area, tgt = shares[0]
    joined = _joined(_poly(tgt), p)
    if joined is None:
        _flag(model, sp, f"could not join its outline to {tgt.id}", result)
        return
    rec = MergeRecord(
        sp.id,
        tgt.id,
        METHOD_SHAFT,
        sp.poly_type,
        round(p.area, 6),
        shared_wall_frac=round(frac, 6),
        shared_wall_m2=round(area, 6),
    )
    _absorb(model, sp, tgt, joined, rec)
    result.merged.append(rec)


def merge_closets_and_shafts(
    model: BuildingModel, doors: Optional[Iterable[dict]] = None
) -> MergeResult:
    """Apply the closet/shaft rule to ``model`` in place and report what happened.

    Closets go first so a shaft beside a closet sees the room that absorbed
    it. ``doors`` optionally adds ``{"id", "level_id", "plan_center_m"}``
    records to those found on space openings. Running it twice is a no-op
    for merged spaces; kept spaces are flagged again only on the first run.
    """
    result = MergeResult()
    door_list = _collect_doors(model, doors)
    flagged = {
        it.description.split(" kept as")[0].split(" ", 1)[-1]
        for it in model.review_queue
        if it.kind == "space_merge"
    }
    for sp in sorted(
        (s for s in model.spaces.values() if s.poly_type == "closet"), key=lambda s: s.id
    ):
        if sp.id in flagged:
            continue
        _merge_closet(model, sp, door_list, result)
    for sp in sorted(
        (s for s in model.spaces.values() if s.poly_type == "shaft"), key=lambda s: s.id
    ):
        if sp.id in flagged:
            continue
        _merge_shaft(model, sp, result)
    return result
