"""Scheduled mechanical equipment located in rooms from the mechanical plans (#746).

A mechanical schedule names each box (``VAV-1``, ``AHU-2``) and the plan shows
where it is by the same tag written beside the symbol. This reads those tags
off each mechanical floor plan, registers the sheet to the architectural plan
of the same level, and puts each tag in the room that contains it.

Registration, in order:

* **shared column grid** (confidence 0.85): every grid line labelled on both
  sheets gives an offset along its axis; all must agree within ``REG_TOL_M``
  and there must be at least one in each direction.
* **same sheet frame** (confidence 0.6): no shared grid, but both sheets have
  the same page size and scale, so the drawing is taken to sit at the same
  place on both. Noted on each record so a reviewer can check it.

Nothing else registers: a mechanical plan at another scale with no shared grid
is reported, and its tags are not placed. A tag found in two rooms, or in no
room, goes to review with no room. A scheduled tag no plan shows is listed.

Every placement keeps the plan sheet and the tag's bbox in sheet points.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from datasets_adapter import normalize_tag

REG_TOL_M = 0.15  # grid offsets must agree within this
FRAME_TOL = 0.01  # same page size and scale within 1 %
GRID_CONFIDENCE = 0.85
FRAME_CONFIDENCE = 0.6


def _get(o, k):
    return o[k] if isinstance(o, dict) else getattr(o, k)


def find_tags(sheet, known) -> List[dict]:
    """Text spans on a sheet that are (or start with) a scheduled tag.

    ``known`` is the set of normalised schedule tags. ``VAV-1`` and
    ``VAV-1 450 CFM`` both match ``VAV-1``; ``VAV-10`` does not.
    """
    out = []
    for t in _get(sheet, "text") or []:
        s = str(_get(t, "text") or "").strip()
        if not s:
            continue
        tag = normalize_tag(s)
        if tag not in known:
            tag = normalize_tag(s.split()[0])
            if tag not in known:
                continue
        bb = [float(v) for v in _get(t, "bbox")]
        out.append({"tag": tag, "bbox_pt": [round(v, 2) for v in bb]})
    return out


def register(mech_sheet, arch_sheet, m_mech, m_arch, mech_grid=None, arch_grid=None):
    """``(dx_m, dy_m, method, confidence, note)`` taking mechanical sheet metres
    (x right, y down from the sheet top) to the architectural sheet's, or
    ``(None, None, None, 0, reason)`` when the sheets do not register."""
    if not m_mech or not m_arch:
        return None, None, None, 0.0, "a sheet has no scale"
    if mech_grid is not None and arch_grid is not None:
        offs: Dict[str, List[float]] = {"v": [], "h": []}
        for orient in ("v", "h"):
            a = arch_grid.by_label(orient)
            for lab, g in mech_grid.by_label(orient).items():
                if lab in a:
                    offs[orient].append(a[lab].coord_pt * m_arch - g.coord_pt * m_mech)
        if offs["v"] and offs["h"]:
            spread = max(max(o) - min(o) for o in offs.values())
            if spread > REG_TOL_M:
                return (
                    None, None, None, 0.0,
                    f"shared grid lines disagree by {spread:.2f} m",
                )  # fmt: skip
            dx = sum(offs["v"]) / len(offs["v"])
            dy = sum(offs["h"]) / len(offs["h"])
            n = len(offs["v"]) + len(offs["h"])
            return dx, dy, "grid", GRID_CONFIDENCE, f"{n} shared grid lines"
    same = (
        all(
            abs(float(_get(mech_sheet, k)) - float(_get(arch_sheet, k)))
            <= FRAME_TOL * float(_get(arch_sheet, k))
            for k in ("width_pt", "height_pt")
        )
        and abs(m_mech - m_arch) <= FRAME_TOL * m_arch
    )
    if same:
        return 0.0, 0.0, "frame", FRAME_CONFIDENCE, "no shared grid; same page size and scale"
    return None, None, None, 0.0, "no shared grid and a different page size or scale"


def to_canonical(bbox_pt, m_mech, dx, dy, arch_h_pt, m_arch) -> Tuple[float, float]:
    """Tag centre in the model's canonical metres (x right, y down, the plan's
    bottom edge at y = 0, as ``Space.polygon_m`` stores it)."""
    cx = (bbox_pt[0] + bbox_pt[2]) / 2 * m_mech + dx
    cy = (bbox_pt[1] + bbox_pt[3]) / 2 * m_mech + dy
    return round(cx, 4), round(cy - arch_h_pt * m_arch, 4)


def room_of(point, spaces) -> Optional[str]:
    """Id of the one space whose polygon contains ``point``, else None."""
    from shapely.geometry import Point, Polygon

    p = Point(point)
    hits = [sp.id for sp in spaces if len(sp.polygon_m) >= 3 and Polygon(sp.polygon_m).covers(p)]
    return hits[0] if len(hits) == 1 else None
