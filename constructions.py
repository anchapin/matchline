"""Per-space exterior wall U-value rollup (roadmap item 6).

Different wall types cover different parts of the envelope, so the U-value
that matters for a space's loads is area-weighted over that space's own
segments: U_space = sum(U_i * A_i) / sum(A_i).

The rollup is deliberately strict about what it leaves out. A segment with
no construction, an unknown construction id, a construction with no U-value,
or no area does not enter the sum; it is reported, never filled in. The
validation check ``wall_construction_coverage`` recomputes the same number
independently and errors if the stored value drifts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WallURollup:
    space_id: str
    u_value_w_m2k: Optional[float]  # None when no segment contributes
    area_m2: float  # wall area that entered the weighting
    segments: List[str] = field(default_factory=list)  # contributing ids
    left_out: Dict[str, str] = field(default_factory=dict)  # segment id -> reason


def segment_area(wall) -> Optional[float]:
    """Gross segment area: its own area, else length x height, else None."""
    if wall.area_m2 is not None:
        return wall.area_m2
    if wall.length_m is not None and wall.height_m is not None:
        return wall.length_m * wall.height_m
    return None


def wall_u_rollup(model) -> Dict[str, WallURollup]:
    """Area-weighted wall U per space, for every space that owns a segment."""
    out: Dict[str, WallURollup] = {}
    for w in model.envelope:
        sid = getattr(w, "space_id", "") or ""
        if not sid:
            continue
        r = out.setdefault(sid, WallURollup(sid, None, 0.0))
        cid = getattr(w, "construction_id", "") or ""
        a = segment_area(w)
        if not cid:
            r.left_out[w.id] = "no construction"
            continue
        c = model.constructions.get(cid)
        if c is None:
            r.left_out[w.id] = f"construction {cid} not defined"
            continue
        if c.u_value_w_m2k is None or c.u_value_w_m2k <= 0:
            r.left_out[w.id] = f"construction {cid} has no U-value"
            continue
        if not a or a <= 0:
            r.left_out[w.id] = "no wall area"
            continue
        ua = (r.u_value_w_m2k or 0.0) * r.area_m2 + c.u_value_w_m2k * a
        r.area_m2 += a
        r.u_value_w_m2k = ua / r.area_m2
        r.segments.append(w.id)
    return out


def apply_wall_u_rollup(model) -> Dict[str, WallURollup]:
    """Write ``Space.wall_u_value_w_m2k`` from the rollup; returns it.

    Spaces with no contributing segment are set to None, so a stale value
    from an earlier run cannot survive a construction being removed.
    """
    roll = wall_u_rollup(model)
    for sid, sp in model.spaces.items():
        r = roll.get(sid)
        sp.wall_u_value_w_m2k = r.u_value_w_m2k if r else None
    return roll
