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

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from building_model import BuildingModel, Provenance, Space, space_from_dict

SOURCE = "space_merge"
METHOD_CLOSET = "space_merge:closet_door"
METHOD_SHAFT = "space_merge:shaft_shared_wall"
# A merge a reviewer directed by a review edit (#799), not the automatic rule.
METHOD_REVIEW = "space_merge:review_edit"
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
        target={"kind": "space", "id": sp.id},
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


def check_merge(model: BuildingModel, source_id: str, target_id: str) -> None:
    """Raise ValueError when ``source_id`` cannot merge into ``target_id``.

    Used to validate a review edit before anything changes (#799): the
    source must still be in the model, the target a different space on the
    same level, and the two outlines must join into one polygon.
    """
    src = model.spaces.get(source_id)
    if src is None:
        raise ValueError(f"space {source_id!r} is not in the model (already merged?)")
    tgt = model.spaces.get(target_id)
    if tgt is None:
        raise ValueError(f"room {target_id!r} is not a space id in the model")
    if target_id == source_id:
        raise ValueError(f"space {source_id!r} cannot merge into itself")
    if tgt.level_id != src.level_id:
        raise ValueError(
            f"room {target_id!r} is on level {tgt.level_id!r}; {source_id!r} is on {src.level_id!r}"
        )
    if _joined(_poly(tgt), _poly(src)) is None:
        raise ValueError(f"could not join its outline to {target_id}")


def merge_into(model: BuildingModel, source_id: str, target_id: str) -> dict:
    """Merge ``source_id`` into ``target_id`` by review decision (#799).

    Same absorb path the automatic rule uses, but the target is the
    reviewer's choice. Returns the snapshot ``un_merge`` restores; the
    caller keeps it on the review item so a revert can undo the merge.
    Empty dict when the merge cannot happen (the caller validated first).
    """
    src = model.spaces.get(source_id)
    tgt = model.spaces.get(target_id)
    if src is None or tgt is None:
        return {}
    p, q = _poly(src), _poly(tgt)
    if p is None or q is None:
        return {}
    joined = _joined(q, p)
    if joined is None:
        return {}
    snap = {
        "src": asdict(src),
        "tgt": asdict(tgt),
        "zones": {zid: list(z.space_ids) for zid, z in model.zones.items()},
        "segs": [seg.id for seg in model.envelope if getattr(seg, "space_id", "") == source_id],
    }
    rec = MergeRecord(source_id, target_id, METHOD_REVIEW, src.poly_type, round(p.area, 6))
    _absorb(model, src, tgt, joined, rec)
    return snap


def un_merge(model: BuildingModel, snap: dict) -> None:
    """Undo a review merge, restoring the snapshot ``merge_into`` took (#799).

    Puts the absorbed space back with its own polygon, openings, lighting,
    HVAC and daylight, restores the absorbing space's pre-merge state, and
    rewinds the zone membership and envelope segment moves.
    """
    if not snap or "src" not in snap or "tgt" not in snap:
        return
    tgt = space_from_dict(snap["tgt"])
    src = space_from_dict(snap["src"])
    model.spaces[tgt.id] = tgt
    model.spaces[src.id] = src
    for zid, ids in (snap.get("zones") or {}).items():
        z = model.zones.get(zid)
        if z is not None:
            z.space_ids = list(ids)
    for seg_id in snap.get("segs") or []:
        for seg in model.envelope:
            if seg.id == seg_id:
                seg.space_id = src.id


def merge_closets_and_shafts(
    model: BuildingModel,
    doors: Optional[Iterable[dict]] = None,
    skip_ids: Iterable[str] = (),
) -> MergeResult:
    """Apply the closet/shaft rule to ``model`` in place and report what happened.

    Closets go first so a shaft beside a closet sees the room that absorbed
    it. ``doors`` optionally adds ``{"id", "level_id", "plan_center_m"}``
    records to those found on space openings. Running it twice is a no-op
    for merged spaces; kept spaces are flagged again only on the first run.
    ``skip_ids`` are never merged away (spaces matchline itself authored and
    exported, #588); they can still absorb other spaces.
    """
    result = MergeResult()
    door_list = _collect_doors(model, doors)
    skip = set(skip_ids)
    flagged = {
        it.description.split(" kept as")[0].split(" ", 1)[-1]
        for it in model.review_queue
        if it.kind == "space_merge"
    }
    for sp in sorted(
        (s for s in model.spaces.values() if s.poly_type == "closet"), key=lambda s: s.id
    ):
        if sp.id in flagged or sp.id in skip:
            continue
        _merge_closet(model, sp, door_list, result)
    for sp in sorted(
        (s for s in model.spaces.values() if s.poly_type == "shaft"), key=lambda s: s.id
    ):
        if sp.id in flagged or sp.id in skip:
            continue
        _merge_shaft(model, sp, result)
    return result


# --------------------------------------------------------------- split (#1002)
# A reviewer can split one space along a straight cut they draw in the viewer.
# The cut's endpoints must sit on the space's boundary (within SPLIT_TOL_M) and
# the cut must produce exactly two usable pieces; the larger piece keeps the
# space's identity (id, number, zone and lighting assignments), the smaller one
# becomes a new unlabeled space the pipeline treats like any other. Openings
# whose absolute centre can be located (facade openings against the envelope
# walls, skylights with a plan centre) move to the piece that contains them;
# everything else stays with the kept piece.

SPLIT_TOL_M = 0.05  # how far a cut endpoint may sit off the boundary
MIN_SPLIT_AREA_M2 = 0.5  # a piece smaller than this is not a usable room


def parse_split(raw: str) -> dict:
    """Parse ``a=x,y;b=x,y`` into ``{"a": [x, y], "b": [x, y]}``; ValueError
    on a malformed cut. No geometry checks here."""
    m = re.fullmatch(r"\s*a\s*=\s*([^;]+)\s*;\s*b\s*=\s*(.+)\s*", raw or "")
    if not m:
        raise ValueError("a split edit takes the form a=x,y;b=x,y")

    def _pt(side, text):
        xy = [v.strip() for v in text.split(",")]
        if len(xy) != 2:
            raise ValueError(f"split endpoint {side} {text!r} is not x,y")
        try:
            return [float(xy[0]), float(xy[1])]
        except ValueError:
            raise ValueError(f"split endpoint {side} {text!r} is not x,y") from None

    return {"a": _pt("a", m.group(1)), "b": _pt("b", m.group(2))}


def cuts_equal(cut: dict, parsed: dict) -> bool:
    """True when a stored split cut is the same cut, to a millimetre."""
    for side in ("a", "b"):
        for c, q in zip(cut.get(side) or [], parsed.get(side) or []):
            if abs(float(c) - float(q)) > 1e-3:
                return False
        if len(cut.get(side) or []) != len(parsed.get(side) or []):
            return False
    return True


def validate_split(model: BuildingModel, source_id: str, raw: str) -> dict:
    """Parse and check a viewer split cut WITHOUT changing the model.

    ``raw`` is the decisions-file value form ``a=x,y;b=x,y``. Returns the cut
    as ``{"a": [x, y], "b": [x, y]}`` or raises ValueError saying what is
    wrong: the same checks split_space applies before it cuts.
    """
    cut = parse_split(raw)
    a, b = cut["a"], cut["b"]
    sp = model.spaces.get(source_id)
    if sp is None:
        raise ValueError(f"space {source_id!r} is not in the model")
    if not sp.polygon_m or len(sp.polygon_m) < 3:
        raise ValueError(f"space {source_id!r} has no usable polygon to split")
    poly = _poly(sp)
    if poly is None:
        raise ValueError(f"space {source_id!r} has no usable polygon to split")
    for pt in (a, b):
        if poly.boundary.distance(Point(pt[0], pt[1])) > SPLIT_TOL_M:
            raise ValueError(f"split endpoint {pt} is not on the boundary")
    from shapely.geometry import GeometryCollection, LineString
    from shapely.ops import split as shp_split

    line = LineString([(a[0], a[1]), (b[0], b[1])])
    if line.length < 1e-9:
        raise ValueError("the split cut has no length")
    parts = shp_split(poly, line)
    if isinstance(parts, GeometryCollection):
        parts = list(parts.geoms)
    else:
        parts = [parts]
    pieces = [
        g
        for g in parts
        if isinstance(g, Polygon) and not g.is_empty and g.area >= MIN_SPLIT_AREA_M2
    ]
    if len(pieces) != 2:
        raise ValueError(f"the cut makes {len(pieces)} usable piece(s); a split needs exactly two")
    return {"a": a, "b": b}


def split_space(model: BuildingModel, source_id: str, a, b) -> dict:
    """Split ``source_id`` along segment ``a``-``b``; returns what ``un_split`` restores.

    ``a`` and ``b`` are [x, y] points in the model frame (metres, y down), each
    on (or within SPLIT_TOL_M of) the space's boundary. Raises ValueError, and
    changes nothing, when the cut is not exactly two usable pieces.
    """
    from shapely.geometry import GeometryCollection, LineString
    from shapely.ops import split as shp_split

    sp = model.spaces.get(source_id)
    if sp is None:
        raise ValueError(f"space {source_id!r} is not in the model")
    if not sp.polygon_m or len(sp.polygon_m) < 3:
        raise ValueError(f"space {source_id!r} has no usable polygon to split")
    poly = _poly(sp)
    if poly is None:
        raise ValueError(f"space {source_id!r} has no usable polygon to split")
    for pt in (a, b):
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            raise ValueError("split cut endpoints must each be an [x, y] point")
        try:
            q = Point(float(pt[0]), float(pt[1]))
        except (TypeError, ValueError):
            raise ValueError("split cut endpoints must each be an [x, y] point") from None
        if poly.boundary.distance(q) > SPLIT_TOL_M:
            raise ValueError(f"split endpoint {list(q.coords)[0]} is not on the boundary")
    line = LineString([(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))])
    if line.length < 1e-9:
        raise ValueError("the split cut has no length")
    parts = shp_split(poly, line)
    if isinstance(parts, GeometryCollection):
        parts = list(parts.geoms)
    else:
        parts = [parts]
    pieces = [
        g
        for g in parts
        if isinstance(g, Polygon) and not g.is_empty and g.area >= MIN_SPLIT_AREA_M2
    ]
    if len(pieces) != 2:
        raise ValueError(f"the cut makes {len(pieces)} usable piece(s); a split needs exactly two")
    pieces.sort(key=lambda g: g.area, reverse=True)
    kept, new = orient(pieces[0], 1.0), orient(pieces[1], 1.0)

    snap = {
        "kept_id": sp.id,
        "polygon_m": [list(map(float, v)) for v in sp.polygon_m],
        "new_id": _new_split_id(model, sp.level_id),
        "cut": {"a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])]},
        "moved": [],
    }
    nsp = Space(
        id=snap["new_id"],
        level_id=sp.level_id,
        name=sp.name,
        number="",
        polygon_m=_ring(new),
        label_confidence=0.95,
        poly_type=sp.poly_type,
        poly_type_confidence=sp.poly_type_confidence,
    )
    centers = _opening_centers(model, sp)
    for op in list(sp.openings):
        c = centers.get(op.id)
        if c is not None:
            kp, np_ = _contains(kept, c), _contains(new, c)
            if np_ and not kp:
                sp.openings.remove(op)
                nsp.openings.append(op)
                snap["moved"].append(op.id)
    sp.polygon_m = _ring(kept)
    _recompute_area(sp)
    _recompute_area(nsp)
    model.spaces[nsp.id] = nsp
    return snap


def un_split(model: BuildingModel, snap: dict) -> None:
    """Put a split space back the way the pipeline left it (revert path)."""
    sp = model.spaces.get(snap.get("kept_id") or "")
    if sp is None:
        return
    sp.polygon_m = snap["polygon_m"]
    _recompute_area(sp)
    nsp = model.spaces.pop(snap["new_id"], None)
    if nsp is not None:
        for op in list(nsp.openings):
            nsp.openings.remove(op)
            sp.openings.append(op)


def _contains(poly: Polygon, c) -> bool:
    return poly.covers(Point(c[0], c[1]))


def _recompute_area(sp: Space) -> None:
    p = _poly(sp)
    sp.area_m2 = round(p.area, 4) if p is not None else None


def _new_split_id(model: BuildingModel, level_id: str) -> str:
    k = 1
    while f"{level_id}-SPLIT-{k}" in model.spaces:
        k += 1
    return f"{level_id}-SPLIT-{k}"


def _opening_centers(model: BuildingModel, sp: Space) -> dict:
    """Absolute [x, y] centre per opening id, where the model locates one.

    Facade openings: host_interval_m along the facade axis from the axis
    minimum of that level+facade's envelope walls (the same placement the
    review viewer draws); skylights: plan_center_m. Openings the model cannot
    place get no entry and stay with the kept piece.
    """

    facades = {o.host_facade for o in sp.openings if o.host_facade in FACADE_AXES}
    mins, plane, n = {}, {}, {}
    for w in getattr(model, "envelope", []):
        host = model.spaces.get(w.space_id)
        if w.facade not in facades or host is None or host.level_id != sp.level_id:
            continue
        axis = FACADE_AXES[w.facade]
        lo = min(w.from_m[axis], w.to_m[axis])
        cur = mins.get(w.facade)
        if cur is None or lo < cur:
            mins[w.facade] = lo
        plane[w.facade] = plane.get(w.facade, 0.0) + (w.from_m[1 - axis] + w.to_m[1 - axis]) / 2
        n[w.facade] = n.get(w.facade, 0) + 1
    for f in plane:
        plane[f] = plane[f] / n[f]
    centers = {}
    for o in sp.openings:
        if o.category == "skylight" and o.plan_center_m:
            centers[o.id] = [float(o.plan_center_m[0]), float(o.plan_center_m[1])]
            continue
        axis = FACADE_AXES.get(o.host_facade)
        if axis is None or not o.host_interval_m:
            continue
        lo = mins.get(o.host_facade)
        if lo is None:
            continue
        s0, s1 = float(o.host_interval_m[0]), float(o.host_interval_m[1])
        if o.s_center_m is not None:
            cross = float(o.s_center_m)
        else:
            cross = plane.get(o.host_facade)
        if cross is None:
            continue
        c = [0.0, 0.0]
        c[axis] = lo + (s0 + s1) / 2
        c[1 - axis] = cross
        centers[o.id] = c
    return centers


FACADE_AXES = {"south": 0, "north": 0, "east": 1, "west": 1}
