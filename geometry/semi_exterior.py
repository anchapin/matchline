"""Semi-exterior building envelope between spaces (#747 slice 5).

ASHRAE 90.1-2019 Section 3.2 / Figure 5.5.2: the envelope between a
conditioned space and a semiheated or unconditioned space is
SEMI-EXTERIOR building envelope. Each space's category comes from
``Space.conditioning`` (``space_conditioning.classify_space`` via
``zone_capacity.classify_spaces``).

A boundary is reported when the categories settle it as semi-exterior, or
when it would be semi-exterior if an unsettled side (``"review"`` or no
category at all) turned out one way: those go to review, never defaulted.
Two spaces with no category at all are not reported, so a set with no HVAC
data stays silent. Semiheated against unconditioned is the semiheated
envelope, not semi-exterior, and is not reported here.

Floor/ceiling boundaries between spaces on adjacent levels are
semi-exterior envelope too (#900); ``semi_exterior_floor_boundaries``
records those the same way, with ``area_m2`` and ``plane: "floor"``.
Wall records carry ``plane: "wall"`` so the construction stage can tell
them apart: only wall records take the Table 5.5 Semiheated wall, so
exports are unchanged by the floor records.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

SEMI_SIDES = ("semiheated", "unconditioned")
SETTLED = ("conditioned",) + SEMI_SIDES


def _category(space) -> Optional[str]:
    c = getattr(space, "conditioning", None)
    return c.get("category") if isinstance(c, dict) else None


def boundary_kind(cat_a: Optional[str], cat_b: Optional[str]) -> Tuple[Optional[str], str]:
    """``("semi_exterior" | "review" | None, reason)`` for one shared boundary.

    ``None`` categories mean no zone was found for the space; ``"review"``
    means its category was not settled. ``None`` kind means not semi-exterior
    (or not knowable from either side)."""
    if cat_a is None and cat_b is None:
        return None, "neither space has a Section 3.2 category"
    pair = {cat_a, cat_b}
    if "conditioned" in pair:
        other = cat_b if cat_a == "conditioned" else cat_a
        if cat_a == cat_b:
            return None, "both conditioned"
        if other in SEMI_SIDES:
            return "semi_exterior", f"conditioned against {other}"
        return "review", f"conditioned against a space whose category is {other or 'unknown'}"
    unsettled = [c for c in (cat_a, cat_b) if c not in SETTLED]
    settled = [c for c in (cat_a, cat_b) if c in SETTLED]
    if unsettled and settled:
        # semi-exterior if the unsettled side is conditioned
        return "review", (
            f"{settled[0]} against a space whose category is "
            f"{unsettled[0] or 'unknown'}; semi-exterior if that space is conditioned"
        )
    if len(unsettled) == 2:
        return "review", "neither space's category is settled"
    return None, f"{cat_a} against {cat_b}"


def _wall_pair_records(spaces: Dict[str, object], verdict) -> List[dict]:
    """Shared wall records for the pairs ``verdict(a, b)`` reports.

    ``verdict`` returns ``(kind, reason)`` for a pair to report or ``None``
    to skip it. Geometry shared by every wall-record caller: the pair's
    shared segments, minus any that sit on the level's outer boundary
    (those are exterior walls, not interior pairs). One record per space
    pair, sorted within the level by id.
    """
    from shapely.geometry import Point
    from shapely.ops import unary_union

    from bem.bem_space_split import MIN_PIECE_M, TOL_M, _lines, _poly

    by_level: Dict[str, list] = {}
    for sid, sp in spaces.items():
        if len(getattr(sp, "polygon_m", None) or []) >= 3:
            by_level.setdefault(sp.level_id, []).append((sid, sp))
    out: List[dict] = []
    for _lid, items in sorted(by_level.items()):
        items.sort(key=lambda t: t[0])
        polys = {sid: _poly([tuple(p[:2]) for p in sp.polygon_m]) for sid, sp in items}
        outer = unary_union(list(polys.values())).boundary
        for i, (sa, a) in enumerate(items):
            for sb, b in items[i + 1 :]:
                got = verdict(a, b)
                if got is None:
                    continue
                kind, reason = got
                pa, pb = polys[sa], polys[sb]
                if not pa.buffer(TOL_M).intersects(pb):
                    continue
                segs = []
                for line in _lines(pa.boundary.intersection(pb.buffer(TOL_M))):
                    cs = list(line.simplify(0.01).coords)
                    for q0, q1 in zip(cs, cs[1:]):
                        if math.dist(q0, q1) < MIN_PIECE_M:
                            continue
                        mid = ((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0)
                        if outer.distance(Point(mid)) <= TOL_M:
                            continue
                        segs.append(
                            [[round(q0[0], 4), round(q0[1], 4)], [round(q1[0], 4), round(q1[1], 4)]]
                        )
                if not segs:
                    continue
                out.append(
                    {
                        "spaces": [sa, sb],
                        "categories": [_category(a), _category(b)],
                        "kind": kind,
                        "reason": reason,
                        "plane": "wall",
                        "length_m": round(sum(math.dist(*s) for s in segs), 4),
                        "segments": segs,
                    }
                )
    return out


def semi_exterior_boundaries(spaces: Dict[str, object]) -> List[dict]:
    """Semi-exterior and to-review boundaries between spaces on each level.

    Boundaries on the outside of the level's spaces (their union's boundary,
    courtyards included) are exterior walls and are left out. One record per
    space pair: ``{"spaces": [a, b], "categories": [..], "kind",
    "reason", "length_m", "segments": [[[x0, y0], [x1, y1]], ...]}``,
    sorted by pair."""

    def verdict(a, b):
        kind, reason = boundary_kind(_category(a), _category(b))
        return (kind, reason) if kind else None

    return _wall_pair_records(spaces, verdict)


def vestibule_interior_boundaries(spaces: Dict[str, object]) -> List[dict]:
    """Interior surfaces of unconditioned vestibules (5.5.2 vestibule sentence).

    ASHRAE 90.1-2019 5.5.2: "The interior surfaces and exterior surfaces of
    unconditioned vestibules shall comply with the building envelope
    requirements for a semiheated space." A partition between an
    unconditioned vestibule and a settled semiheated or unconditioned
    neighbor is not semi-exterior envelope (``boundary_kind`` returns
    None), but the vestibule sentence still governs it, so it gets a
    ``kind: "vestibule"`` record and the construction stage points it at
    the Semiheated column. Pairs the ordinary machinery already reports are
    not duplicated: against a conditioned space the pair is a semi-exterior
    record (slice 5), and an unsettled neighbor is already a review record.
    The vestibule's own category must be settled unconditioned, so nothing
    is reported on an unsettled side; a vestibule next to a vestibule is
    one record, both sides meeting the rule. Same shape as the
    semi-exterior wall records, sorted by pair.
    """

    def verdict(a, b):
        va, vb = getattr(a, "is_vestibule", False), getattr(b, "is_vestibule", False)
        if not (va or vb):
            return None
        vest, other = (a, b) if va else (b, a)
        cat_v, cat_o = _category(vest), _category(other)
        if cat_v != "unconditioned" or cat_o == "conditioned" or cat_o not in SETTLED:
            return None
        return (
            "vestibule",
            f"interior surface of unconditioned vestibule {vest.id} against "
            f"{other.id} ({cat_o}); 5.5.2 vestibule sentence",
        )

    return _wall_pair_records(spaces, verdict)


def semi_exterior_floor_boundaries(spaces: Dict[str, object], levels: List[object]) -> List[dict]:
    """Semi-exterior and to-review floor/ceiling boundaries between levels (#900).

    A floor/ceiling between a conditioned space and a semiheated or
    unconditioned space on the level above or below is semi-exterior
    envelope too (90.1-2019 Section 3.2 / Figure 5.5.2). Only levels next to
    each other in elevation are compared, and only real footprint overlap
    counts: below ``TOL_M ** 2`` the overlap is drafting noise, not a
    boundary. One record per space pair, same ``kind``/``reason`` as the
    wall boundaries plus ``area_m2`` and ``plane: "floor"``, sorted by
    pair. No construction is attached here, so exports are unchanged.
    """
    from bem.bem_space_split import TOL_M, _poly

    elev = {}
    for lv in levels or []:
        elev[getattr(lv, "id", "")] = float(getattr(lv, "elevation_z_m", 0.0) or 0.0)
    by_level: Dict[str, list] = {}
    for sid, sp in spaces.items():
        if len(getattr(sp, "polygon_m", None) or []) >= 3:
            by_level.setdefault(sp.level_id, []).append((sid, sp))
    ordered = sorted(elev, key=lambda lid: (elev[lid], lid))
    min_area = TOL_M * TOL_M
    out: List[dict] = []
    for lower, upper in zip(ordered, ordered[1:]):
        if lower not in by_level or upper not in by_level:
            continue  # an empty level between two floors breaks adjacency
        for sa, a in by_level[lower]:
            ca = _category(a)
            pa = _poly([tuple(p[:2]) for p in a.polygon_m])
            for sb, b in by_level[upper]:
                cb = _category(b)
                kind, reason = boundary_kind(ca, cb)
                if kind is None:
                    continue
                area = pa.intersection(_poly([tuple(p[:2]) for p in b.polygon_m])).area
                if area <= min_area:
                    continue
                pair = sorted((sa, sb))
                cats = [ca, cb] if sa == pair[0] else [cb, ca]
                out.append(
                    {
                        "spaces": pair,
                        "categories": cats,
                        "kind": kind,
                        "reason": reason,
                        "plane": "floor",
                        "area_m2": round(area, 4),
                    }
                )
    out.sort(key=lambda r: r["spaces"])
    return out
