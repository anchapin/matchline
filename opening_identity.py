"""Is this the same physical opening? Shared by link dedup and validation (#664).

Two ``SpaceOpening`` records are one logical opening when they sit on the same
facade with the same tag and category, at the same along-wall position (within
``CROSS_LEVEL_DEDUP_TOL_M``), with matching width, on the same or adjacent
levels, and (across levels) with absolute vertical extents that overlap or
touch. That last rule is what separates one two-storey window seen on two
adjacent elevation sheets from two punched windows stacked floor over floor:
the stacked pair is split by a spandrel, the tall window is continuous.

An opening with no along-wall position cannot be matched and is reported as
unplaced rather than guessed at.
"""

from __future__ import annotations

from typing import Optional

CROSS_LEVEL_DEDUP_TOL_M = 0.05  # along-wall centre and vertical touch tolerance
OPENING_DIM_TOL_M = 0.15  # width (and same-level height) agreement
UNTAGGED = "@untagged"
WALL_CATEGORIES = ("window", "door")


def opening_s(op) -> Optional[float]:
    """Along-facade centre of an opening, or None when it was never placed."""
    if op.s_center_m is not None:
        return float(op.s_center_m)
    iv = op.host_interval_m
    if iv and len(iv) == 2 and iv[0] is not None and iv[1] is not None:
        return (float(iv[0]) + float(iv[1])) / 2.0
    return None


def is_wall_opening(op) -> bool:
    return op.category in WALL_CATEGORIES and op.host_facade not in ("", "roof")


def level_table(model) -> dict:
    """level_id -> (rank by elevation, elevation_z_m)."""
    ordered = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    return {lv.id: (i, float(lv.elevation_z_m)) for i, lv in enumerate(ordered)}


def opening_z(op, elevation_z_m: float) -> Optional[tuple]:
    """Absolute [bottom, top] of a wall opening, or None without a sill."""
    if op.sill_m is None:
        return None
    top = op.head_m if op.head_m is not None else op.sill_m + (op.height_m or 0.0)
    return (elevation_z_m + float(op.sill_m), elevation_z_m + float(top))


def group_key(op) -> tuple:
    return (op.host_facade, op.tag or UNTAGGED, op.category)


def same_opening(a, la: str, b, lb: str, levels: dict) -> bool:
    """True when (a on level la) and (b on level lb) are one physical opening.

    Callers pass openings that already share ``group_key``.
    """
    sa, sb = opening_s(a), opening_s(b)
    if sa is None or sb is None or abs(sa - sb) > CROSS_LEVEL_DEDUP_TOL_M:
        return False
    if abs((a.width_m or 0.0) - (b.width_m or 0.0)) > OPENING_DIM_TOL_M:
        return False
    ra, za = levels.get(la, (0, 0.0))
    rb, zb = levels.get(lb, (0, 0.0))
    if la == lb:
        return abs((a.height_m or 0.0) - (b.height_m or 0.0)) <= OPENING_DIM_TOL_M
    if abs(ra - rb) != 1:
        return False
    ia, ib = opening_z(a, za), opening_z(b, zb)
    if ia is None or ib is None:
        return False
    gap = max(ia[0], ib[0]) - min(ia[1], ib[1])
    return gap <= CROSS_LEVEL_DEDUP_TOL_M
