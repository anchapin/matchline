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
# A partial elevation (#818): one facade drawn in parts, on two sheets or as
# "SOUTH ELEVATION - EAST HALF" and "- WEST HALF". The named end anchors the
# part when no grid is shared with the plan.
PART_END_RE = re.compile(r"\b(NORTH|SOUTH|EAST|WEST|LEFT|RIGHT)\s+(?:HALF|END|PART|PORTION|WING)\b")
PARTIAL_RE = re.compile(r"\bPART(?:IAL)?\b|\bHALF\b|\b\d+\s+OF\s+\d+\b|\bMATCH\s*LINE\b")

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
    partial: bool = False
    span_m: Optional[List[float]] = None  # [s0, s1] of facade this sheet covers

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
    t = PART_END_RE.sub(" ", (title or "").upper())  # "EAST HALF" names no facade
    hits = {m.group(1).lower() for m in FACADE_RE.finditer(t)}
    return hits.pop() if len(hits) == 1 else None


def partial_from_title(title: str) -> Tuple[bool, Optional[str]]:
    """``(True, "east")`` for "SOUTH ELEVATION - EAST HALF"; ``(True, None)`` for
    "SOUTH ELEVATION (PART 1 OF 2)"; ``(False, None)`` for a whole facade.
    LEFT / RIGHT are returned as drawn ("left", "right")."""
    t = (title or "").upper()
    m = PART_END_RE.search(t)
    if m:
        return True, m.group(1).lower()
    return bool(PARTIAL_RE.search(t)), None


def _end_s(facade: Facade, end: str, mirrored: bool) -> Optional[float]:
    """The facade ``s`` of a named end: 0 at the reference corner (west end of
    north/south, north end of east/west), ``length_m`` at the other."""
    if end in ("left", "right"):  # as drawn: s grows rightward unless mirrored
        return 0.0 if (end == "left") != mirrored else facade.length_m
    zero = "west" if facade.name in ("north", "south") else "north"
    far = "east" if facade.name in ("north", "south") else "south"
    return 0.0 if end == zero else facade.length_m if end == far else None


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
    partial: Optional[bool] = None,
) -> ElevationRead:
    """Read one elevation sheet. ``plan_grid_s``: ``{label: s metres}`` along
    this facade from the plan; ``elev_grid``: the sheet's ``grid_detect.GridSet``.

    ``partial`` (default: from the title) marks a sheet that draws only part of
    its facade (#818). A part is registered by shared grid labels, else by the
    end its title names ("EAST HALF"); one with neither is not read. Its
    ``span_m`` is the stretch of facade it covers, and its outline is not
    checked against the whole facade length."""
    name = facade_from_title(title)
    er = ElevationRead(sheet_file, sheet_id, name, False, m_per_pt=m_per_pt)
    titled, end = partial_from_title(title)
    er.partial = titled if partial is None else bool(partial)
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
    if er.partial and abs(er.outline_width_m - fac.length_m) <= LENGTH_TOL * fac.length_m:
        er.partial = False  # drawn full length: a whole facade, e.g. a second copy

    cand = []
    for r in rects:
        w, h = (r[2] - r[0]) * m_per_pt, (r[3] - r[1]) * m_per_pt
        if r == out or not _inside(r, out):
            continue
        if WIN_W_M[0] <= w <= WIN_W_M[1] and WIN_H_M[0] <= h <= WIN_H_M[1]:
            cand.append(r)
    cand = [r for r in cand if not any(o != r and _inside(r, o) for o in cand)]

    reg = _register(
        er, sheet_id, fac, out, base, m_per_pt, plan_grid_s, elev_grid, revision,
        end if er.partial else None,
    )  # fmt: skip
    if reg is None:
        er.review = [r for r in er.review if r["kind"] != "elevation_grid_unmatched"]
        er.reason = (
            "partial elevation shares fewer than two grid labels with the plan and its "
            'title names no end (e.g. "EAST HALF"), so it can\'t be placed on the facade'
        )
        return er
    sa, _z = reg.to_facade(out[0], base)
    sb, _z = reg.to_facade(out[2], base)
    er.span_m = [round(min(sa, sb), 4), round(max(sa, sb), 4)]
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
    tol = LENGTH_TOL * fac.length_m
    if er.partial:
        if er.span_m[0] < -tol or er.span_m[1] > fac.length_m + tol:
            er.review.append(
                {
                    "kind": "elevation_length_mismatch",
                    "reason": (
                        f"partial {name} elevation runs from {er.span_m[0]:.2f} to "
                        f"{er.span_m[1]:.2f} m, past the {fac.length_m:.2f} m plan facade"
                    ),
                }
            )
    elif off > LENGTH_TOL:
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


def _register(er, sheet_id, fac, out, base, m_per_pt, plan_grid_s, elev_grid, revision, end=None):
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
    if not er.partial:
        return register_elevation_geometric(sheet_id, fac, out[0], px_per_m, base, revision)
    reg = register_elevation_geometric(sheet_id, fac, out[0], px_per_m, base, revision)
    mirrored = reg.a_s < 0
    s_end = _end_s(fac, end, mirrored) if end else None
    if s_end is None:
        return None
    # the drawn edge at that end: left edge is s=0 unless mirrored
    at_zero = s_end == 0.0
    u_edge = out[0] if at_zero != mirrored else out[2]
    reg.b_s = s_end - reg.a_s * u_edge
    note = f"partial elevation: its {end} end placed at s={s_end:.2f} m"
    if reg.provenance is not None:
        reg.provenance.note = f"{reg.provenance.note}; {note}"
    return reg


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


# --- level marks (#810, second slice: storey height) ------------------------
# "FIRST FLOOR / EL. 100'-0\"", "LEVEL 2 EL +3.600", "ROOF EL. 112'-6\"". Only
# floors and the roof count toward storey height; T.O. PLATE, PARAPET and GRADE
# are read but never become a storey.
LEVEL_VALUE_RE = re.compile(
    r"\bEL(?:EV(?:ATION)?)?\.?\s*:?\s*"
    r"(?:(?P<ft>[+-]?\d+)\s*'\s*-?\s*(?P<inch>\d+(?:\.\d+)?)?(?:\s+(?P<num>\d+)/(?P<den>\d+))?\s*\"?"
    r"|(?P<m>[+-]?\d+\.\d{1,3})\s*M?\b)"
)
LEVEL_NAME_RE = re.compile(
    r"\b(?:(?:GROUND|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|1ST|2ND|3RD|[4-9]TH)\s+(?:FLOOR|LEVEL)"
    r"|LEVEL\s*\d+|MEZZANINE|PENTHOUSE(?!\s+ROOF)|ROOF|T\.?\s*O\.?\s*\w+|PARAPET|GRADE|BASEMENT)\b"
)
STOREY_NAME_RE = re.compile(r"FLOOR|LEVEL|ROOF|BASEMENT|MEZZANINE|PENTHOUSE")
NOT_STOREY_RE = re.compile(r"^T\.?\s*O\b|PARAPET|GRADE")
MARK_NAME_DIST_PT = 24.0  # a name this close to its value (above or beside) labels it
MARK_Z_TOL_M = 0.3  # drawn height vs stated value, after the common offset
STOREY_AGREE_M = 0.05


@dataclass
class LevelMark:
    name: Optional[str]
    value_m: float  # as stated (project datum)
    z_drawn_m: float  # where the mark sits above the outline base
    text: str


def _value_m(m: "re.Match") -> float:
    if m.group("m") is not None:
        return float(m.group("m"))
    ft = float(m.group("ft"))
    inch = float(m.group("inch") or 0)
    if m.group("num"):
        inch += float(m.group("num")) / float(m.group("den"))
    sign = -1.0 if ft < 0 or m.group("ft").startswith("-") else 1.0
    return sign * (abs(ft) * 12 + inch) * 0.0254


def _norm(t: str) -> str:
    """Curly and prime quote marks (PDF text often carries them) to ' and "."""
    return (
        t.replace("\u2019", "'")
        .replace("\u2032", "'")
        .replace("\u201d", '"')
        .replace("\u2033", '"')
    )


def level_marks(sheet, m_per_pt: Optional[float]) -> List[LevelMark]:
    """Level marks on an elevation sheet whose drawn heights agree with their values.

    A mark's value is checked against where it is drawn: every kept mark's
    ``value_m - z_drawn_m`` must agree with the others' within ``MARK_Z_TOL_M``,
    so a dimension string or a note that happens to say "EL." is dropped.
    Fewer than two agreeing marks returns [].
    """
    if not m_per_pt:
        return []
    W, H = float(_get(sheet, "width_pt")), float(_get(sheet, "height_pt"))
    rects = [r for r in _rects(sheet) if (r[2] - r[0]) * (r[3] - r[1]) < BORDER_FRAC * W * H]
    big = [r for r in rects if (r[2] - r[0]) * m_per_pt >= OUTLINE_MIN_M]
    if not big:
        return []
    out = max(big, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    base = out[3]
    spans = [(_norm(str(_get(t, "text"))), tuple(_get(t, "bbox"))) for t in _get(sheet, "text")]
    names = [(LEVEL_NAME_RE.search(s.upper()), b) for s, b in spans]
    names = [(m.group(0), b) for m, b in names if m]
    marks = []
    for s, b in spans:
        v = LEVEL_VALUE_RE.search(s.upper())
        if not v:
            continue
        own = LEVEL_NAME_RE.search(s.upper())
        name = own.group(0) if own else None
        if name is None:
            near = [
                (abs(nb[3] - b[1]) + abs(nb[0] - b[0]), n)
                for n, nb in names
                if nb != b
                and abs(nb[0] - b[0]) <= 3 * MARK_NAME_DIST_PT
                and -2 <= b[1] - nb[3] <= MARK_NAME_DIST_PT
            ]
            name = min(near)[1] if near else None
        # the mark's line is at the bottom of its value text (or its name above)
        marks.append(LevelMark(name, round(_value_m(v), 4), round((base - b[3]) * m_per_pt, 4), s))
    if len(marks) < 2:
        return []
    offs = sorted(mk.value_m - mk.z_drawn_m for mk in marks)
    mid = offs[len(offs) // 2]
    kept = [mk for mk in marks if abs(mk.value_m - mk.z_drawn_m - mid) <= MARK_Z_TOL_M]
    return sorted(kept, key=lambda mk: mk.value_m) if len(kept) >= 2 else []


def _storeys(marks: List[LevelMark]) -> List[LevelMark]:
    st = [
        mk
        for mk in marks
        if mk.name and STOREY_NAME_RE.search(mk.name) and not NOT_STOREY_RE.search(mk.name)
    ]
    return sorted({round(mk.value_m, 3): mk for mk in st}.values(), key=lambda mk: mk.value_m)


def storey_heights(marks: List[LevelMark]) -> List[float]:
    """Floor-to-floor (and top floor to roof) heights from named level marks."""
    st = _storeys(marks)
    return [round(b.value_m - a.value_m, 4) for a, b in zip(st, st[1:])]


_ORDINAL_WORDS = {
    "GROUND": 1, "FIRST": 1, "1ST": 1, "SECOND": 2, "2ND": 2, "THIRD": 3, "3RD": 3,
    "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
}  # fmt: skip


def storey_ordinal(name: Optional[str]) -> Optional[int]:
    """Which storey a level mark names: 1 for FIRST FLOOR / LEVEL 1, 0 for BASEMENT.

    ROOF and unnumbered names give None (#814).
    """
    n = (name or "").upper().strip()
    if "BASEMENT" in n:
        return 0
    m = re.search(r"LEVEL\s*(\d+)", n)
    if m:
        return int(m.group(1))
    m = re.match(r"([4-9])TH\b", n)
    if m:
        return int(m.group(1))
    w = n.split()[0] if n else ""
    return _ORDINAL_WORDS.get(w)


def _british(st: List[LevelMark]) -> bool:
    """GROUND FLOOR and FIRST FLOOR on one elevation: ground is 1, first is 2 (#822)."""
    words = {(mk.name or "").upper().split()[0] for mk in st if mk.name}
    return "GROUND" in words and bool(words & {"FIRST", "1ST"})


def storey_key(name: Optional[str], british: bool = False):
    """The plan level a storey mark names: an ordinal (``storey_ordinal``), ``"MEZZ"``
    or ``"PH"``; None for ROOF and unnumbered names. ``british`` shifts FIRST, SECOND,
    ... up one because GROUND is the first storey (#822)."""
    n = (name or "").upper().strip()
    if n.startswith("MEZZANINE"):
        return "MEZZ"
    if n.startswith("PENTHOUSE"):
        return "PH"
    o = storey_ordinal(n)
    if british and o is not None and not re.search(r"LEVEL\s*\d|GROUND|BASEMENT", n):
        o += 1
    return o


def storey_steps(marks: List[LevelMark]) -> List[tuple]:
    """``(key, height)`` per storey: the step from each storey mark to the next.

    The key is the lower mark's (``storey_key``), so FIRST FLOOR at 0 and SECOND
    FLOOR at 4.5 m give ``(1, 4.5)`` (#814); with GROUND FLOOR below FIRST FLOOR on
    the same elevation, GROUND is 1 and FIRST is 2 (#822).
    """
    st = _storeys(marks)
    br = _british(st)
    return [(storey_key(a.name, br), round(b.value_m - a.value_m, 4)) for a, b in zip(st, st[1:])]
