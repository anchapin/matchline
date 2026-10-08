"""Read windows off vector elevation sheets in a drawing set (#810, first slice).

Input: one ingested elevation sheet (``pdf_ingest`` JSON dict), its scale, the
facade it shows, and the plan footprint the run already built. Output: an
:class:`ElevationRead` with the facade outline, the windows (and doors) drawn on
it in sheet points and in facade coordinates (``s`` metres along the facade from
its reference corner, ``z`` metres up from the base of the facade outline), and
how the sheet was registered to the plan.

What is read in this slice:

* the facade named in the sheet title (``NORTH ELEVATION``); a sheet of
  several elevations, or one naming no facade, is not read yet;
* the facade outline: the largest closed axis-aligned rectangle at least
  ``OUTLINE_MIN_M`` wide that is not the sheet border; its base is ``z = 0``;
* openings: closed axis-aligned rectangles inside the outline within the
  window size range; one whose base sits on the outline base is a door, and a
  rectangle inside another opening (glass inside a frame) is dropped.

Registration uses the shared column grid when the elevation and the plan carry
at least two of the same grid labels (``registration.register_elevation_grid``),
otherwise the outline's ends against the plan footprint
(``register_elevation_geometric``, lower confidence). Joining windows to plan
openings is slice 2 of #810.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

from registration import (
    Facade,
    FacadeRegistration,
    register_elevation_geometric,
    register_elevation_grid,
)

FACADE_RE = re.compile(r"\b(NORTH|SOUTH|EAST|WEST)\b[^A-Z]*\bELEVATION\b")
OUTLINE_MIN_M = 2.0
WIN_W_M = (0.3, 6.0)
WIN_H_M = (0.3, 4.0)
DOOR_BASE_TOL_M = 0.05  # an opening this close to the outline base is a door
BORDER_FRAC = 0.8  # a rectangle covering this much of the sheet is its border
LENGTH_TOL = 0.05  # outline vs plan facade length, relative

Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1 in sheet points (y down)


@dataclass
class ElevationOpening:
    id: str
    kind: str  # window | door
    bbox_pt: Rect
    s0_m: float
    s1_m: float
    sill_m: float
    head_m: float

    @property
    def width_m(self) -> float:
        return round(self.s1_m - self.s0_m, 4)

    @property
    def height_m(self) -> float:
        return round(self.head_m - self.sill_m, 4)


@dataclass
class ElevationRead:
    sheet: str
    sheet_id: str
    facade: Optional[str]
    ok: bool
    reason: str = ""
    m_per_pt: Optional[float] = None
    outline_pt: Optional[Rect] = None
    outline_width_m: Optional[float] = None
    facade_length_m: Optional[float] = None
    openings: List[ElevationOpening] = field(default_factory=list)
    registration: Optional[dict] = None
    review: List[dict] = field(default_factory=list)

    @property
    def windows(self) -> List[ElevationOpening]:
        return [o for o in self.openings if o.kind == "window"]

    def to_dict(self) -> dict:
        d = asdict(self)
        for o, od in zip(self.openings, d["openings"]):
            od["width_m"], od["height_m"] = o.width_m, o.height_m
        d["schema"] = "matchline.elevation/1"
        return d


def facade_from_title(title: str) -> Optional[str]:
    """``north`` for "NORTH ELEVATION"; None for none or several facades."""
    hits = {m.group(1).lower() for m in FACADE_RE.finditer((title or "").upper())}
    return hits.pop() if len(hits) == 1 else None


def plan_facade(name: str, bbox_m: Rect) -> Facade:
    """The facade of a plan footprint, canonical metres (x right, y down)."""
    x0, y0, x1, y1 = bbox_m
    if name == "south":
        return Facade("south", (x0, y1), x1 - x0, y1, "x")
    if name == "north":
        return Facade("north", (x0, y0), x1 - x0, y0, "x")
    if name == "east":
        return Facade("east", (x1, y0), y1 - y0, x1, "y")
    return Facade("west", (x0, y0), y1 - y0, x0, "y")


def _get(o, k):
    return o[k] if isinstance(o, dict) else getattr(o, k)


def _rects(sheet) -> List[Rect]:
    """Closed axis-aligned rectangles among the sheet's vector paths."""
    out = []
    for p in _get(sheet, "primitives"):
        segs = _get(p, "segments")
        if any(s[0] not in ("M", "L", "Z") for s in segs):
            continue
        pts = [tuple(s[1][0]) for s in segs if s[0] in ("M", "L")]
        if len(pts) == 5 and _close(pts[0], pts[-1]):
            pts = pts[:4]
        closed = any(s[0] == "Z" for s in segs) or len(pts) == 4
        if len(pts) != 4 or not closed:
            continue
        ok = all(
            abs(a[0] - b[0]) < 0.5 or abs(a[1] - b[1]) < 0.5 for a, b in zip(pts, pts[1:] + pts[:1])
        )
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        if ok and max(xs) - min(xs) > 1 and max(ys) - min(ys) > 1:
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return sorted(set(out))


def _close(a, b, tol=0.5) -> bool:
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _inside(a: Rect, b: Rect, tol: float = 0.5) -> bool:
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] <= b[3] + tol


def read_elevation(
    sheet,
    sheet_file: str,
    sheet_id: str,
    title: str,
    m_per_pt: Optional[float],
    footprint_m: Rect,
    plan_grid_s: Optional[dict] = None,
    elev_grid=None,
    revision: int = 0,
) -> ElevationRead:
    """Read one elevation sheet. ``plan_grid_s``: ``{label: s metres}`` along
    this facade from the plan; ``elev_grid``: the sheet's ``grid_detect.GridSet``."""
    name = facade_from_title(title)
    er = ElevationRead(sheet_file, sheet_id, name, False, m_per_pt=m_per_pt)
    if not name:
        er.reason = f"title {title!r} names no single facade (one elevation per sheet for now)"
        return er
    if not m_per_pt:
        er.reason = "no drawing scale found"
        return er
    fac = plan_facade(name, footprint_m)
    er.facade_length_m = round(fac.length_m, 4)
    W, H = float(_get(sheet, "width_pt")), float(_get(sheet, "height_pt"))
    rects = [r for r in _rects(sheet) if (r[2] - r[0]) * (r[3] - r[1]) < BORDER_FRAC * W * H]
    big = [r for r in rects if (r[2] - r[0]) * m_per_pt >= OUTLINE_MIN_M]
    if not big:
        er.reason = "no facade outline (a closed rectangle at least 2 m wide)"
        return er
    out = max(big, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    er.outline_pt = out
    er.outline_width_m = round((out[2] - out[0]) * m_per_pt, 4)
    base = out[3]

    cand = []
    for r in rects:
        w, h = (r[2] - r[0]) * m_per_pt, (r[3] - r[1]) * m_per_pt
        if r == out or not _inside(r, out):
            continue
        if WIN_W_M[0] <= w <= WIN_W_M[1] and WIN_H_M[0] <= h <= WIN_H_M[1]:
            cand.append(r)
    cand = [r for r in cand if not any(o != r and _inside(r, o) for o in cand)]

    reg = _register(er, sheet_id, fac, out, base, m_per_pt, plan_grid_s, elev_grid, revision)
    er.registration = {
        "method": reg.method,
        "confidence": reg.confidence,
        "a_s": reg.a_s,
        "b_s": reg.b_s,
        "a_z": reg.a_z,
        "b_z": reg.b_z,
        "grid_labels_used": list(reg.grid_labels_used),
        "note": reg.provenance.note if reg.provenance else "",
    }
    nw = nd = 0
    for r in sorted(cand, key=lambda r: (r[0], r[1])):
        sa, z_top = reg.to_facade(r[0], r[1])
        sb, z_bot = reg.to_facade(r[2], r[3])
        door = (base - r[3]) * m_per_pt <= DOOR_BASE_TOL_M
        if door:
            nd += 1
        else:
            nw += 1
        er.openings.append(
            ElevationOpening(
                id=f"{sheet_id}-{'D' if door else 'W'}{nd if door else nw}",
                kind="door" if door else "window",
                bbox_pt=r,
                s0_m=round(min(sa, sb), 4),
                s1_m=round(max(sa, sb), 4),
                sill_m=round(min(z_top, z_bot), 4),
                head_m=round(max(z_top, z_bot), 4),
            )
        )
    off = abs(er.outline_width_m - fac.length_m) / max(fac.length_m, 1e-9)
    if off > LENGTH_TOL:
        er.review.append(
            {
                "kind": "elevation_length_mismatch",
                "reason": (
                    f"{name} facade outline is {er.outline_width_m:.2f} m wide on the "
                    f"elevation but {fac.length_m:.2f} m on the plan"
                ),
            }
        )
    er.ok = True
    return er


def _register(er, sheet_id, fac, out, base, m_per_pt, plan_grid_s, elev_grid, revision):
    px_per_m = 1.0 / m_per_pt
    if plan_grid_s and elev_grid is not None:
        from grid_detect import elevation_bubbles

        bubbles = elevation_bubbles(elev_grid)
        shared = {b["label"] for b in bubbles} & set(plan_grid_s)
        if len(shared) >= 2:
            return register_elevation_grid(
                sheet_id, fac, plan_grid_s, bubbles, base, px_per_m, revision
            )
        er.review.append(
            {
                "kind": "elevation_grid_unmatched",
                "reason": (
                    f"fewer than two grid labels shared with the plan ({sorted(shared)}); "
                    "registered from the facade outline instead"
                ),
            }
        )
    return register_elevation_geometric(sheet_id, fac, out[0], px_per_m, base, revision)


def facade_registration(d: dict, sheet_id: str, facade: str) -> FacadeRegistration:
    """Rebuild a :class:`FacadeRegistration` from ``ElevationRead.registration``."""
    return FacadeRegistration(
        sheet_id=sheet_id,
        facade=facade,
        method=d["method"],
        confidence=d["confidence"],
        a_s=d["a_s"],
        b_s=d["b_s"],
        a_z=d["a_z"],
        b_z=d["b_z"],
        grid_labels_used=list(d.get("grid_labels_used", [])),
    )
