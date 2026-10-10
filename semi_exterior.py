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

Output only: nothing reads it yet, so constructions and the gbXML are
unchanged (the gbXML still writes these as interior walls).
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


def semi_exterior_boundaries(spaces: Dict[str, object]) -> List[dict]:
    """Semi-exterior and to-review boundaries between spaces on each level.

    Boundaries on the outside of the level's spaces (their union's boundary,
    courtyards included) are exterior walls and are left out. One record per
    space pair: ``{"spaces": [a, b], "categories": [..], "kind",
    "reason", "length_m", "segments": [[[x0, y0], [x1, y1]], ...]}``,
    sorted by pair."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    from bem_space_split import MIN_PIECE_M, TOL_M, _lines, _poly

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
            ca = _category(a)
            for sb, b in items[i + 1 :]:
                cb = _category(b)
                kind, reason = boundary_kind(ca, cb)
                if kind is None:
                    continue
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
                        "categories": [ca, cb],
                        "kind": kind,
                        "reason": reason,
                        "length_m": round(sum(math.dist(*s) for s in segs), 4),
                        "segments": segs,
                    }
                )
    return out
