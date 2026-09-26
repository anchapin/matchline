"""Non-room polygon classification for arch-plan room extraction.

Classifies polygons from the architectural plan as one of:
  room        -- standard labeled room with number
  shaft       -- vertical chase / utility shaft (small, sealed, often unnumbered)
  closet      -- storage / equipment closet (small, may have number)
  elevator_core -- elevator shaft / stairwell (rectangular, isolated, thick walls)
  unassigned  -- polygon that does not meet any classification criteria

Evidence used (all from the building dict, no ML):
  - area_m2
  - aspect_ratio (width / depth)
  - has_room_number (bool)
  - label text (name field)
  - door_count (from south_windows adjacency)
  - adjacency count (neighbors sharing a wall)

Call classify_polygons() after _build_spaces to annotate all spaces
with a poly_type field before any area rollups run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PolyClassEvidence:
    area_m2: float
    aspect_ratio: float
    has_room_number: bool
    label_text: str
    n_doors: int
    n_adjacent_spaces: int


@dataclass
class PolyClassification:
    poly_type: str  # "room" | "shaft" | "closet" | "elevator_core" | "unassigned"
    confidence: float
    reasons: list[str]
    evidence: PolyClassEvidence


def _compute_adjacency(rect_m: list, grids_h: dict) -> int:
    """Count how many grid intervals the room spans (proxy for wall-sharing neighbors)."""
    if not grids_h or len(grids_h) < 2:
        return 0
    y0, y1 = rect_m[1], rect_m[3]
    y_sorted = sorted(grids_h.values())
    count = sum(1 for y in y_sorted if y0 < y < y1)
    return max(1, count + 1)


def _aspect_ratio(rect_m: list) -> float:
    """Width / depth from [x0, y0, x1, y1] rect."""
    w = rect_m[2] - rect_m[0]
    h = rect_m[3] - rect_m[1]
    if min(w, h) < 1e-6:
        return 1.0
    return max(w, h) / min(w, h)


def _classify_from_evidence(ev: PolyClassEvidence) -> PolyClassification:
    reasons = []
    area = ev.area_m2
    label = ev.label_text.lower()

    if area < 2.0 and not ev.has_room_number:
        reasons.append("tiny area, no number → shaft")
        return PolyClassification("shaft", 0.95, reasons, ev)

    if 2.0 <= area < 8.0 and not ev.has_room_number:
        reasons.append("small unnumbered polygon → closet")
        return PolyClassification("closet", 0.90, reasons, ev)

    if ev.has_room_number and area >= 8.0:
        reasons.append("has number and area ≥ 8 m² → room")
        return PolyClassification("room", 0.95, reasons, ev)

    if ev.has_room_number and area < 8.0:
        reasons.append("has number but small → small_room")
        return PolyClassification("room", 0.80, reasons, ev)

    if "elevator" in label or "elev" in label:
        reasons.append("label mentions elevator")
        return PolyClassification("elevator_core", 0.90, reasons, ev)

    if "stair" in label or "stairwell" in label:
        reasons.append("label mentions stair")
        return PolyClassification("elevator_core", 0.90, reasons, ev)

    if "shaft" in label or "chase" in label:
        reasons.append("label mentions shaft/chase")
        return PolyClassification("shaft", 0.90, reasons, ev)

    if "closet" in label or "storage" in label or "utility" in label:
        reasons.append("label suggests closet/storage")
        return PolyClassification("closet", 0.85, reasons, ev)

    if ev.n_adjacent_spaces > 2 and area < 15:
        reasons.append(f"high_adjacency({ev.n_adjacent_spaces})")
        return PolyClassification("room", 0.75, reasons, ev)

    if ev.n_doors > 2 and area < 15:
        reasons.append(f"multi_door({ev.n_doors})")
        return PolyClassification("room", 0.85, reasons, ev)

    if ev.aspect_ratio > 5 and area < 20:
        reasons.append(f"very_high_aspect({ev.aspect_ratio:.1f})")
        return PolyClassification("shaft", 0.88, reasons, ev)

    if ev.aspect_ratio > 3 and area < 10:
        reasons.append(f"high_aspect({ev.aspect_ratio:.1f})")
        return PolyClassification("closet", 0.88, reasons, ev)

    if area < 4.0:
        reasons.append("small unclassified → closet (default)")
        return PolyClassification("closet", 0.70, reasons, ev)

    reasons.append("default unassigned")
    return PolyClassification("unassigned", 0.50, reasons, ev)


def classify_polygons(rooms: list, south_windows: list, grids_h: dict) -> dict:
    """Classify all polygons in the building dict.

    rooms: list of room dicts from the building generator
    south_windows: list of south-facade window dicts with room_number key
    grids_h: horizontal grid dict (label -> y_m) -- used for adjacency

    Returns {room_number: PolyClassification}.
    """
    window_by_room: dict[str, int] = {}
    for w in south_windows:
        rn = w.get("room_number")
        if rn:
            window_by_room[rn] = window_by_room.get(rn, 0) + 1

    result = {}
    for r in rooms:
        rn = r.get("number", "")
        rect = r.get("rect_m", [0, 0, 0, 0])
        area = abs(r.get("area_m2", 0.0))
        ar = _aspect_ratio(rect)
        has_num = bool(rn and str(rn).strip())
        label = r.get("name", "")
        n_doors = window_by_room.get(rn, 0)
        n_adj = _compute_adjacency(rect, grids_h)

        ev = PolyClassEvidence(
            area_m2=area,
            aspect_ratio=ar,
            has_room_number=has_num,
            label_text=label,
            n_doors=n_doors,
            n_adjacent_spaces=n_adj,
        )
        result[rn] = _classify_from_evidence(ev)
    return result
