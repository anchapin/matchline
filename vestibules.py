"""Vestibule detection (#747).

Alex's rule (2026-10-11, live, answering the parked question): vestibules
are spaces with an exterior door that has a small floor area and lead to
a lobby or main entrance.

A room space meets the rule when all three hold:

1. it has an exterior door: a door hosted on an envelope wall whose
   facade Tier 1 stated (``SpaceOpening.host_facade``); interior doors
   host on partitions that are not in the envelope, so they stay empty;
2. its floor area is at or under ``VESTIBULE_MAX_AREA_M2`` (Fo's default,
   flagged for Alex as a review item);
3. it connects to a lobby or main entrance: it touches another room
   space whose NAME is a lobby-type name (LOBBY / RECEPTION / ENTRY /
   FOYER / WAITING, the same pattern ``space_use_defaults`` maps to the
   lobby space type), or the exterior door's far side
   (``adjacent_space_id``) is such a space. VESTIBULE itself is not in
   the neighbor pattern, so two adjacent vestibules never mark each
   other.

Detection never reclassifies a space: the Section 3.2 category still
comes from the HVAC capacity rules alone (Alex's conditioning rule,
2026-10-09). The semi-exterior reports themselves still come from
``Space.conditioning`` only, so exports are unchanged by this slice; the
mark is what a construction stage reads when envelope requirements for
unconditioned vestibules are applied downstream.
"""

from __future__ import annotations

import re
from typing import Optional

VESTIBULE_MAX_AREA_M2 = 15.0  # Fo's default for Alex's "small floor area"
NEIGHBOR_TOL_M = 0.05  # polygons this close share a wall

# Same lobby names ``space_use_defaults`` maps to the lobby space type,
# minus VESTIBULE (see module docstring).
_LOBBY_NAME = re.compile(r"\b(LOBBY|RECEPTION|ENTRY|FOYER|WAITING)\b")


def _is_lobby_type(space) -> bool:
    return bool(_LOBBY_NAME.search(re.sub(r"[^A-Za-z0-9]+", " ", (space.name or "").upper())))


def _polygon(space):
    from shapely.geometry import Polygon as SPolygon

    return SPolygon([(p[0], p[1]) for p in space.polygon_m])


def touches(a, b) -> bool:
    """True when two spaces' footprints are within ``NEIGHBOR_TOL_M``."""
    try:
        return _polygon(a).distance(_polygon(b)) <= NEIGHBOR_TOL_M
    except Exception:  # empty or degenerate footprint: not a neighbor call
        return False


def detect_vestibules(model, max_area_m2: float = VESTIBULE_MAX_AREA_M2) -> list:
    """Pure: one record per space that meets Alex's rule, nothing mutated."""
    out = []
    rooms = [sp for sp in model.spaces.values() if sp.poly_type == "room"]
    for sp in rooms:
        if sp.area_m2 is None or sp.area_m2 > max_area_m2:
            continue
        door = next(
            (o for o in sp.openings if o.category == "door" and (o.host_facade or "").strip()),
            None,
        )
        if door is None:
            continue
        far = model.spaces.get(door.adjacent_space_id or "") if door.adjacent_space_id else None
        lobby: Optional[object] = far if far is not None and _is_lobby_type(far) else None
        if lobby is None:
            lobby = next(
                (o for o in rooms if o.id != sp.id and touches(sp, o) and _is_lobby_type(o)),
                None,
            )
        if lobby is None:
            continue
        out.append(
            {
                "space_id": sp.id,
                "area_m2": sp.area_m2,
                "door_tag": door.tag,
                "lobby_neighbor": lobby.id,
            }
        )
    return out
