"""Inter-story surface matching and shaft stack consistency (#632).

The BEM writers export one storey, so nothing yet reconciles the horizontal
surfaces between storeys or the shafts that run through several of them. This
step does that from the model alone and changes nothing in it apart from
review items: the multi-storey writer consumes the result later.

For each pair of consecutive levels (ordered by elevation) every overlap of a
lower-level space with an upper-level space becomes one interior horizontal
surface carrying both space ids: the lower space's ceiling is the upper
space's floor. What is left of an upper space over no lower space is an
exposed floor (over outdoor air), and what is left of a lower space under no
upper space is a roof. The lowest level's floors are ground contact and the
top level's ceilings are roofs. Every space's floor pieces, and separately its
ceiling pieces, sum to its plan area, or the step raises.

Shafts and elevator cores on consecutive levels whose footprints overlap with
IoU >= ``SHAFT_IOU`` form one vertical stack. A shaft that only partly lines
up with a shaft on the next level, or that stops under or over an occupied
room on an adjacent level, goes to the review queue; geometry is never moved
or merged here. A level that starts below the top of the level under it is
flagged, not corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from building_model import Provenance

MIN_PIECE_M2 = 1e-6  # slivers below this are numerical noise, not surfaces
AREA_TOL_REL = 1e-6  # floor / ceiling pieces must sum to plan area to this
SHAFT_IOU = 0.8  # footprints at or above this IoU are the same shaft
SHAFT_COVER = 0.5  # share of a shaft footprint a room must cover to "cap" it
LEVEL_GAP_TOL_M = 1e-3
SHAFT_TYPES = ("shaft", "elevator_core")
REVIEW_CONF = 0.6

KINDS = ("interior", "exposed_floor", "roof", "ground")
_PREFIX = {"interior": "HI", "exposed_floor": "HX", "roof": "HR", "ground": "HG"}


class InterstoryError(ValueError):
    """Raised when the plan cannot be matched without guessing."""


@dataclass(frozen=True)
class HorizontalSurface:
    """One horizontal surface between (or bounding) storeys.

    ``lower_space_id`` is the space below the surface (its ceiling) and
    ``upper_space_id`` the space above it (its floor); either is None where
    the other side is outdoors or ground.
    """

    id: str
    kind: str
    z_m: float
    lower_space_id: Optional[str]
    upper_space_id: Optional[str]
    area_m2: float
    geom: BaseGeometry = field(compare=False, repr=False)


@dataclass(frozen=True)
class ShaftStack:
    id: str
    space_ids: Tuple[str, ...]
    level_ids: Tuple[str, ...]


@dataclass
class InterstoryResult:
    surfaces: List[HorizontalSurface] = field(default_factory=list)
    stacks: List[ShaftStack] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)

    def floors_of(self, space_id: str) -> List[HorizontalSurface]:
        return [s for s in self.surfaces if s.upper_space_id == space_id]

    def ceilings_of(self, space_id: str) -> List[HorizontalSurface]:
        return [s for s in self.surfaces if s.lower_space_id == space_id]


def _poly(space) -> Optional[Polygon]:
    ring = space.polygon_m or []
    if len(ring) < 3:
        return None
    g = Polygon([(float(p[0]), float(p[1])) for p in ring])
    if not g.is_valid:
        raise InterstoryError(f"space {space.id} has an invalid polygon")
    if g.area <= MIN_PIECE_M2:
        return None
    return g


def _parts(g: BaseGeometry) -> List[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        out = [g]
    else:
        out = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
    return [p for p in out if p.area > MIN_PIECE_M2]


def _level_spaces(model, levels) -> Dict[str, List[Tuple[str, Polygon, object]]]:
    by_level: Dict[str, List[Tuple[str, Polygon, object]]] = {lv.id: [] for lv in levels}
    for sid in sorted(model.spaces):
        sp = model.spaces[sid]
        if sp.level_id not in by_level:
            continue
        g = _poly(sp)
        if g is not None:
            by_level[sp.level_id].append((sid, g, sp))
    for lid, rows in by_level.items():
        for i, (a, ga, _) in enumerate(rows):
            for b, gb, _ in rows[i + 1 :]:
                ov = ga.intersection(gb).area
                if ov > AREA_TOL_REL * max(ga.area, gb.area) and ov > MIN_PIECE_M2:
                    raise InterstoryError(
                        f"spaces {a} and {b} on level {lid} overlap by {ov:.4f} m2"
                    )
    return by_level


def _iou(a: Polygon, b: Polygon) -> float:
    inter = a.intersection(b).area
    if inter <= 0.0:
        return 0.0
    return inter / a.union(b).area


def _prov(sp, note: str) -> Provenance:
    src = getattr(sp, "core_provenance", None)
    return Provenance(
        sheet_id=getattr(src, "sheet_id", "") or "model",
        revision=getattr(src, "revision", 0) or 0,
        method="interstory",
        confidence=REVIEW_CONF,
        note=note,
    )


def _check_conservation(result: InterstoryResult, by_level) -> None:
    for rows in by_level.values():
        for sid, g, _ in rows:
            for side, pieces in (
                ("floor", result.floors_of(sid)),
                ("ceiling", result.ceilings_of(sid)),
            ):
                total = sum(p.area_m2 for p in pieces)
                if abs(total - g.area) > AREA_TOL_REL * g.area + MIN_PIECE_M2 * (len(pieces) + 1):
                    raise InterstoryError(
                        f"{side} pieces of {sid} sum to {total:.6f} m2, plan area {g.area:.6f} m2"
                    )


def match_interstory(model, flag: bool = True) -> InterstoryResult:
    """Match horizontal surfaces between consecutive levels and stack shafts.

    Returns an :class:`InterstoryResult`. With ``flag`` (the default) the
    findings also go to ``model.review_queue`` as ``interstory`` items; the
    model is otherwise left untouched.
    """
    levels = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    if not levels:
        return InterstoryResult()
    by_level = _level_spaces(model, levels)
    result = InterstoryResult()
    counters: Dict[str, int] = {}

    def add(kind, z, lower, upper, geom):
        for part in _parts(geom):
            key = f"{_PREFIX[kind]}-{lower or 'OUT'}-{upper or 'OUT'}"
            n = counters.get(key, 0) + 1
            counters[key] = n
            result.surfaces.append(
                HorizontalSurface(
                    id=f"{key}-{n}",
                    kind=kind,
                    z_m=z,
                    lower_space_id=lower,
                    upper_space_id=upper,
                    area_m2=part.area,
                    geom=part,
                )
            )

    def note(sp, text):
        result.findings.append(text)
        if flag:
            model.flag_for_review("interstory", text, REVIEW_CONF, _prov(sp, text))

    first, last = levels[0], levels[-1]
    for sid, g, _ in by_level[first.id]:
        add("ground", first.elevation_z_m, None, sid, g)

    for lo, up in zip(levels, levels[1:]):
        top_lo = lo.elevation_z_m + lo.wall_height_m
        if up.elevation_z_m < top_lo - LEVEL_GAP_TOL_M and by_level[lo.id]:
            note(
                by_level[lo.id][0][2],
                f"level {up.id} starts at {up.elevation_z_m:.3f} m, below the top of "
                f"level {lo.id} ({top_lo:.3f} m); floor-to-floor not corrected",
            )
        lower_rows, upper_rows = by_level[lo.id], by_level[up.id]
        for lsid, lg, _ in lower_rows:
            for usid, ug, _ in upper_rows:
                if lg.intersects(ug):
                    add("interior", up.elevation_z_m, lsid, usid, lg.intersection(ug))
        upper_union = unary_union([g for _, g, _ in upper_rows]) if upper_rows else None
        lower_union = unary_union([g for _, g, _ in lower_rows]) if lower_rows else None
        for lsid, lg, _ in lower_rows:
            rest = lg.difference(upper_union) if upper_union is not None else lg
            add("roof", top_lo, lsid, None, rest)
        for usid, ug, _ in upper_rows:
            rest = ug.difference(lower_union) if lower_union is not None else ug
            add("exposed_floor", up.elevation_z_m, None, usid, rest)

    for sid, g, _ in by_level[last.id]:
        add("roof", last.elevation_z_m + last.wall_height_m, sid, None, g)

    _check_conservation(result, by_level)
    result.stacks = _shaft_stacks(levels, by_level, note)
    return result


def _shaft_stacks(levels, by_level, note) -> List[ShaftStack]:
    index = {lv.id: k for k, lv in enumerate(levels)}
    shafts = {
        sid: (lid, g, sp)
        for lid, rows in by_level.items()
        for sid, g, sp in rows
        if getattr(sp, "poly_type", "room") in SHAFT_TYPES
    }
    parent = {sid: sid for sid in shafts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for lo, up in zip(levels, levels[1:]):
        for a in sorted(s for s, v in shafts.items() if v[0] == lo.id):
            for b in sorted(s for s, v in shafts.items() if v[0] == up.id):
                iou = _iou(shafts[a][1], shafts[b][1])
                if iou >= SHAFT_IOU:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
                elif iou > 0.0:
                    note(
                        shafts[a][2],
                        f"shaft {a} ({lo.id}) and shaft {b} ({up.id}) overlap with IoU "
                        f"{iou:.2f}; not stacked",
                    )

    groups: Dict[str, List[str]] = {}
    for sid in sorted(shafts):
        groups.setdefault(find(sid), []).append(sid)

    stacks = []
    for root in sorted(groups):
        members = sorted(groups[root], key=lambda s: (index[shafts[s][0]], s))
        lids = tuple(shafts[s][0] for s in members)
        stacks.append(
            ShaftStack(id=f"SHAFT-{members[0]}", space_ids=tuple(members), level_ids=lids)
        )
        for end, step in ((members[-1], 1), (members[0], -1)):
            k = index[shafts[end][0]] + step
            if not 0 <= k < len(levels):
                continue
            fp = shafts[end][1]
            covering = [
                sid
                for sid, g, sp in by_level[levels[k].id]
                if getattr(sp, "poly_type", "room") not in SHAFT_TYPES
                and g.intersection(fp).area >= SHAFT_COVER * fp.area
            ]
            if covering:
                where = "above" if step == 1 else "below"
                note(
                    shafts[end][2],
                    f"shaft stack {stacks[-1].id} ends at {shafts[end][0]} under/over room "
                    f"{', '.join(covering)} on {levels[k].id} ({where}); check the shaft continues",
                )
    return stacks


__all__: Sequence[str] = (
    "HorizontalSurface",
    "InterstoryError",
    "InterstoryResult",
    "ShaftStack",
    "match_interstory",
)
