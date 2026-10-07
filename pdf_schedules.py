"""Schedule tables from vector PDF sheets (#746).

A door, window, lighting or mechanical schedule on a PDF sheet is a ruled
table: horizontal and vertical line segments plus positioned text. This module
finds those tables in an ingested sheet (``matchline.sheet/1``, y-down points),
rebuilds their cells (merged cells included), reads header and data rows, and
turns known schedule kinds into records:

* door / window  -> ``ScheduleEntry`` (tag, width_m, height_m), the same shape
  the takeoff join already uses
* lighting       -> ``ScheduleEntry`` with ``watts`` per fixture
* mechanical     -> equipment dicts (tag, kind, cfm_max, cfm_min, cfm, neck_size)

Rules:

* Only tables titled as a schedule are read (the title is a full-width top row
  or text just above the frame). A grid of lines without that title is a plan,
  a title block or a legend, and is left alone.
* Every cell keeps provenance: sheet, method ``pdf_ruled_table`` and its bbox
  in sheet pixels.
* A table that does not parse cleanly (a merged cell that is not a rectangle,
  text crossing a cell line, no header, a data row without a tag, a repeated
  tag) is reported with its reason and no rows. It is never half-read.
* Tags are normalised with the takeoff join's ``normalize_tag`` (#479).
* Values are never invented: a width that does not parse stays None.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from building_model import Provenance
from datasets_adapter import ScheduleEntry, normalize_tag
from drawing_scale import parse_length_m
from schedule_parse import ScheduleCell, ScheduleRow, ScheduleTable, _cell_provenance

SCHEMA = "matchline.pdf_schedules/1"
METHOD = "pdf_ruled_table"
CONFIDENCE = 0.9

AXIS_TOL_PT = 0.5  # a segment within this of horizontal/vertical is axis-aligned
SNAP_PT = 1.0  # line coordinates closer than this are the same line
MIN_ROW_LINES = 3  # top, header/data divider, bottom
MIN_COL_LINES = 3  # two columns at least
MAX_ROW_PT = 72.0  # a schedule row taller than 1 inch is a plan, not a table
MAX_ROWS = 400  # larger line grids are plans or hatching, not schedules
MAX_COLS = 60
TITLE_GAP_ROWS = 2.5  # title text may sit this many row heights above the frame
CROSS_TOL_PT = 1.5  # text may overhang a cell line by this much

_KINDS = [
    ("door", re.compile(r"\bDOOR")),
    ("window", re.compile(r"\bWINDOW|\bGLAZING")),
    ("lighting", re.compile(r"\bLIGHT|\bLUMINAIRE|\bFIXTURE")),
    (
        "mechanical",
        re.compile(
            r"\bVAV|\bAIR TERMINAL|\bTERMINAL UNIT|\bAIR HANDL|\bAHU|\bRTU|\bROOFTOP|\bDIFFUSER"
            r"|\bGRILLE|\bREGISTER|\bFAN|\bMECHANICAL|\bEQUIPMENT|\bFCU|\bFAN COIL|\bHEAT PUMP"
        ),
    ),
]
_EQUIP = [
    ("vav", re.compile(r"\bVAV|\bTERMINAL UNIT|\bAIR TERMINAL")),
    ("ahu", re.compile(r"\bAHU|\bAIR HANDL|\bRTU|\bROOFTOP")),
    ("terminal", re.compile(r"\bDIFFUSER|\bGRILLE|\bREGISTER")),
    ("fan", re.compile(r"\bFAN\b|\bEXHAUST FAN|\bEF\b")),
    ("fcu", re.compile(r"\bFCU|\bFAN COIL")),
]
_TAG_HDR = re.compile(r"^(MARK|TAG|SYMBOL|TYPE|NO\.?|NUMBER|ID|UNIT|DOOR|WINDOW|FIXTURE)\b")
_NUM = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)")


# ------------------------------------------------------------------ results


@dataclass
class PdfSchedule:
    """One titled table found on a sheet."""

    title: str
    kind: str  # door | window | lighting | mechanical | other
    status: str  # ok | unparsed
    reason: str = ""
    bbox_pt: List[float] = field(default_factory=list)  # [x0, y0, x1, y1], y down
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # full-width note rows
    entries: Dict[str, dict] = field(default_factory=dict)  # tag -> ScheduleEntry dict
    equipment: List[dict] = field(default_factory=list)
    table: Optional[ScheduleTable] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("table")
        return d


# ------------------------------------------------------------------ geometry


def _segments(sheet: dict) -> Tuple[list, list]:
    """Axis-aligned stroked segments: (y, x0, x1) horizontals, (x, y0, y1) verticals."""
    hs, vs = [], []
    for prim in sheet.get("primitives") or []:
        if not prim.get("stroked", True):
            continue
        pts: list = []
        start = None
        for op, coords in prim.get("segments") or []:
            if op == "M":
                start = coords[0]
                pts.append([tuple(coords[0])])
            elif op == "L" and pts:
                pts[-1].append(tuple(coords[0]))
            elif op == "Z" and pts and start is not None:
                pts[-1].append(tuple(start))
            elif op == "C":
                pts.append([tuple(coords[-1])])  # curves break the run
        for run in pts:
            for (x0, y0), (x1, y1) in zip(run, run[1:]):
                if abs(y1 - y0) <= AXIS_TOL_PT and abs(x1 - x0) > AXIS_TOL_PT:
                    hs.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
                elif abs(x1 - x0) <= AXIS_TOL_PT and abs(y1 - y0) > AXIS_TOL_PT:
                    vs.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    return _merge(hs), _merge(vs)


def _merge(segs: list) -> list:
    """Join collinear segments that touch or overlap (sort and sweep)."""
    out: list = []
    segs = sorted(segs)
    k = 0
    while k < len(segs):
        # one line: coordinates within SNAP_PT of the first
        m = k
        while m < len(segs) and segs[m][0] - segs[k][0] <= SNAP_PT:
            m += 1
        c = sum(sg[0] for sg in segs[k:m]) / (m - k)
        run = None
        for _, a, b in sorted(segs[k:m], key=lambda sg: sg[1]):
            if run and a <= run[1] + SNAP_PT:
                run[1] = max(run[1], b)
            else:
                if run:
                    out.append((c, run[0], run[1]))
                run = [a, b]
        out.append((c, run[0], run[1]))
        k = m
    return out


def _components(hs: list, vs: list) -> List[Tuple[list, list]]:
    """Groups of horizontals and verticals that touch each other."""
    n = len(hs) + len(vs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if hs and vs:
        from shapely.geometry import LineString
        from shapely.strtree import STRtree

        tree = STRtree([LineString([(x0, y), (x1, y)]) for y, x0, x1 in hs])
        for j, (x, y0, y1) in enumerate(vs):
            probe = LineString([(x, y0), (x, y1)]).buffer(SNAP_PT)
            for i in tree.query(probe, predicate="intersects"):
                parent[find(int(i))] = find(len(hs) + j)
    groups: Dict[int, Tuple[list, list]] = {}
    for i, h in enumerate(hs):
        groups.setdefault(find(i), ([], []))[0].append(h)
    for j, v in enumerate(vs):
        groups.setdefault(find(len(hs) + j), ([], []))[1].append(v)
    return [g for g in groups.values() if g[0] and g[1]]


def _levels(vals: List[float]) -> List[float]:
    out: List[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > SNAP_PT:
            out.append(v)
    return out


def _covers_h(hs: list, y: float, xa: float, xb: float) -> bool:
    """A horizontal line at y spans the open interval (xa, xb)."""
    m = (xa + xb) / 2
    return any(abs(h[0] - y) <= SNAP_PT and h[1] - SNAP_PT <= m <= h[2] + SNAP_PT for h in hs)


def _covers_v(vs: list, x: float, ya: float, yb: float) -> bool:
    m = (ya + yb) / 2
    return any(abs(v[0] - x) <= SNAP_PT and v[1] - SNAP_PT <= m <= v[2] + SNAP_PT for v in vs)


@dataclass
class _Cell:
    r0: int
    r1: int  # inclusive band range
    c0: int
    c1: int  # inclusive column range
    text: str = ""
    items: list = field(default_factory=list)


def _cells(hs, vs, ys, xs) -> Tuple[Optional[List[_Cell]], Optional[list], str]:
    """Merge the fine grid into cells; returns (cells, owner[r][c], reason)."""
    R, C = len(ys) - 1, len(xs) - 1
    parent = {(r, c): (r, c) for r in range(R) for c in range(C)}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for r in range(R):
        for c in range(C):
            if c + 1 < C and not _covers_v(vs, xs[c + 1], ys[r], ys[r + 1]):
                parent[find((r, c + 1))] = find((r, c))
            if r + 1 < R and not _covers_h(hs, ys[r + 1], xs[c], xs[c + 1]):
                parent[find((r + 1, c))] = find((r, c))
    groups: Dict[tuple, list] = {}
    for k in parent:
        groups.setdefault(find(k), []).append(k)
    cells: List[_Cell] = []
    owner = [[None] * C for _ in range(R)]
    for members in groups.values():
        rs = [m[0] for m in members]
        cs = [m[1] for m in members]
        cell = _Cell(min(rs), max(rs), min(cs), max(cs))
        if len(members) != (cell.r1 - cell.r0 + 1) * (cell.c1 - cell.c0 + 1):
            return None, None, "a merged cell is not a rectangle"
        for r, c in members:
            owner[r][c] = len(cells)
        cells.append(cell)
    return cells, owner, ""


def _locate(v: float, edges: List[float]) -> Optional[int]:
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return i
    return None


def _join_lines(items: list) -> str:
    """Text items of one cell, top-to-bottom then left-to-right, one string."""
    if not items:
        return ""
    items = sorted(items, key=lambda t: ((t["bbox"][1] + t["bbox"][3]) / 2, t["bbox"][0]))
    lines: List[list] = []
    for t in items:
        cy = (t["bbox"][1] + t["bbox"][3]) / 2
        hh = max(t["bbox"][3] - t["bbox"][1], 1.0)
        if lines and abs(cy - lines[-1][0]) <= 0.5 * hh:
            lines[-1][1].append(t)
        else:
            lines.append([cy, [t]])
    words = []
    for _, ts in lines:
        words.extend(t["text"].strip() for t in sorted(ts, key=lambda t: t["bbox"][0]))
    return re.sub(r"\s+", " ", " ".join(w for w in words if w)).strip()


# ------------------------------------------------------------------ reading


def _kind(title: str) -> str:
    t = title.upper()
    for k, rx in _KINDS:
        if rx.search(t):
            return k
    return "other"


def _title_above(texts: list, used: set, x0: float, x1: float, y0: float, row_h: float):
    """Nearest text line above the frame that overlaps it horizontally."""
    best = []
    for i, t in enumerate(texts):
        if i in used:
            continue
        bx0, by0, bx1, by1 = t["bbox"]
        if (
            by1 <= y0 + CROSS_TOL_PT
            and y0 - by1 <= TITLE_GAP_ROWS * row_h
            and bx1 > x0
            and bx0 < x1
        ):
            best.append((y0 - by1, i))
    if not best:
        return "", []
    gap = min(b[0] for b in best)
    pick = [i for g, i in best if g - gap <= 0.5 * row_h]
    return _join_lines([texts[i] for i in pick]), pick


def _read_table(sheet: dict, hs: list, vs: list, texts: list, used: set) -> Optional[PdfSchedule]:
    ys = _levels([h[0] for h in hs])
    xs = _levels([v[0] for v in vs])
    if len(ys) < MIN_ROW_LINES or len(xs) < MIN_COL_LINES:
        return None
    if len(ys) - 1 > MAX_ROWS or len(xs) - 1 > MAX_COLS:
        return None
    x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
    if max(b - a for a, b in zip(ys, ys[1:])) > MAX_ROW_PT:
        return None
    cells, owner, why = _cells(hs, vs, ys, xs)
    bbox = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]

    # text into cells
    inside = []
    for i, t in enumerate(texts):
        bx0, by0, bx1, by1 = t["bbox"]
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            inside.append(i)
    if not inside:
        return None
    row_h = min(b - a for a, b in zip(ys, ys[1:]))

    # title: full-width top band, or text above the frame
    title, title_rows = "", 0
    if cells is not None:
        while title_rows < len(ys) - 1:
            c = cells[owner[title_rows][0]]
            if c.c0 == 0 and c.c1 == len(xs) - 2 and c.r0 == title_rows:
                txt = [texts[i] for i in inside if _locate(_cy(texts[i]), ys) == title_rows]
                if txt and re.search(r"SCHEDULE", _join_lines(txt).upper()):
                    title = (title + " " + _join_lines(txt)).strip()
                    title_rows = c.r1 + 1
                    continue
            break
    above_idx: list = []
    if not title:
        title, above_idx = _title_above(texts, used, x0, x1, y0, row_h)
        if not re.search(r"SCHEDULE", title.upper()):
            return None  # an untitled grid: not a schedule
    used.update(inside)
    used.update(above_idx)
    sched = PdfSchedule(title=title, kind=_kind(title), status="unparsed", bbox_pt=bbox)
    if cells is None:
        sched.reason = why
        return sched

    for i in inside:
        t = texts[i]
        r = _locate(_cy(t), ys)
        c = _locate((t["bbox"][0] + t["bbox"][2]) / 2, xs)
        if r is None or c is None:
            continue
        cell = cells[owner[r][c]]
        if (
            t["bbox"][0] < xs[cell.c0] - CROSS_TOL_PT
            or t["bbox"][2] > xs[cell.c1 + 1] + CROSS_TOL_PT
        ):
            sched.reason = f"text {t['text']!r} crosses a cell line"
            return sched
        cell.items.append(t)
    for cell in cells:
        cell.text = _join_lines(cell.items)

    R, C = len(ys) - 1, len(xs) - 1
    # header: first band after the title, grown while a group header or a tall
    # header cell needs the band below it
    h0 = title_rows
    if h0 >= R:
        sched.reason = "no header row"
        return sched
    h1 = h0
    while True:
        grow = h1
        for c in range(C):
            for r in range(h0, h1 + 1):
                cell = cells[owner[r][c]]
                grow = max(grow, cell.r1)
                if cell.r1 == r and cell.c1 > cell.c0 and not (cell.c0 == 0 and cell.c1 == C - 1):
                    grow = max(grow, r + 1)  # group header: sub-headers follow
        if grow == h1 or grow >= R:
            break
        h1 = grow
    if h1 >= R - 1 and h1 == R - 1 and h0 == h1:
        sched.reason = "no data rows under the header"
        return sched
    headers = []
    for c in range(C):
        parts: List[str] = []
        for r in range(h0, h1 + 1):
            txt = cells[owner[r][c]].text
            if txt and (not parts or parts[-1] != txt):
                parts.append(txt)
        headers.append(" ".join(parts).upper())
    if not any(headers):
        sched.reason = "no header row"
        return sched

    # data rows
    tagc = next((c for c, h in enumerate(headers) if _TAG_HDR.match(h)), 0)
    rows: List[List[str]] = []
    row_bands: List[int] = []
    for r in range(h1 + 1, R):
        if cells[owner[r][0]].c1 == C - 1:  # full-width row: a note
            txt = cells[owner[r][0]].text
            if txt:
                sched.notes.append(txt)
            continue
        vals = []
        for c in range(C):
            cell = cells[owner[r][c]]
            # horizontally merged: the value belongs to its first column;
            # vertically merged: it applies to every row it spans
            vals.append(cell.text if c == cell.c0 else "")
        if not any(vals):
            continue
        rows.append(vals)
        row_bands.append(r)
    if not rows:
        sched.reason = "no data rows under the header"
        return sched
    tags = [normalize_tag(v[tagc]) for v in rows]
    if not all(tags):
        sched.reason = f"a data row has no {headers[tagc] or 'tag'}"
        return sched
    dup = sorted({t for t in tags if tags.count(t) > 1})
    if dup:
        sched.reason = f"tag {dup[0]} appears more than once"
        return sched

    sched.status = "ok"
    sched.headers = headers
    sched.rows = rows
    sched.table = _to_table(sheet, sched, cells, owner, ys, xs, row_bands, h0)
    _records(sched, tagc)
    return sched


def _cy(t) -> float:
    return (t["bbox"][1] + t["bbox"][3]) / 2


def _to_table(sheet, sched, cells, owner, ys, xs, row_bands, h0) -> ScheduleTable:
    sid = sheet.get("sheet_id") or f"page_{sheet.get('page_number', 0)}"
    k = float(sheet.get("px_per_pt") or 1.0)

    def prov(c: _Cell) -> Provenance:
        p = _cell_provenance(sid, 0, METHOD, CONFIDENCE)
        p.bbox = [
            round(xs[c.c0] * k, 1),
            round(ys[c.r0] * k, 1),
            round(xs[c.c1 + 1] * k, 1),
            round(ys[c.r1 + 1] * k, 1),
        ]
        return p

    rows = []
    for ri, (vals, r) in enumerate(zip(sched.rows, row_bands)):
        cs = [
            ScheduleCell(value=v, row=ri, col=c, provenance=prov(cells[owner[r][c]]))
            for c, v in enumerate(vals)
        ]
        rp = _cell_provenance(sid, 0, METHOD, CONFIDENCE)
        rp.bbox = [
            round(xs[0] * k, 1),
            round(ys[r] * k, 1),
            round(xs[-1] * k, 1),
            round(ys[r + 1] * k, 1),
        ]
        rows.append(ScheduleRow(cells=cs, row_index=ri, provenance=rp))
    tp = _cell_provenance(sid, 0, METHOD, CONFIDENCE)
    tp.bbox = [round(v * k, 1) for v in sched.bbox_pt]
    return ScheduleTable(
        name=sched.title, headers=sched.headers, rows=rows, source_entity=None, provenance=tp
    )


# ------------------------------------------------------------------ records


def _col(headers: List[str], *pats: str) -> Optional[int]:
    for p in pats:
        rx = re.compile(p)
        for i, h in enumerate(headers):
            if rx.search(h):
                return i
    return None


def _number(s: str) -> Optional[float]:
    m = _NUM.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def _size(s: str) -> Tuple[Optional[float], Optional[float]]:
    """``3'-0" x 7'-0"`` -> (0.9144, 2.1336); anything else -> (None, None)."""
    parts = re.split(r"\s*[xX×]\s*", (s or "").strip())
    if len(parts) == 2:
        return parse_length_m(parts[0]), parse_length_m(parts[1])
    return None, None


def _records(sched: PdfSchedule, tagc: int) -> None:
    H = sched.headers
    desc_c = _col(H, r"DESCRIPTION", r"\bTYPE\b", r"MANUFACTURER")
    for vals in sched.rows:
        tag = normalize_tag(vals[tagc])
        desc = vals[desc_c] if desc_c is not None and desc_c != tagc else ""
        if sched.kind in ("door", "window"):
            wc = _col(H, r"\bWIDTH\b", r"^W$", r"\bW\b")
            hc = _col(H, r"\bHEIGHT\b", r"^H$", r"\bH\b")
            w = parse_length_m(vals[wc]) if wc is not None else None
            h = parse_length_m(vals[hc]) if hc is not None else None
            if w is None and h is None:
                sc = _col(H, r"\bSIZE\b", r"\bOPENING\b")
                if sc is not None:
                    w, h = _size(vals[sc])
            e = ScheduleEntry(tag=tag, category=sched.kind, width_m=w, height_m=h)
            if w is None or h is None:
                e.note = "size not read from the schedule"
            sched.entries[tag] = asdict(e)
        elif sched.kind == "lighting":
            wc = _col(H, r"WATT", r"INPUT\s*W", r"\bLOAD\b", r"^W$", r"\bW\b")
            watts = _number(vals[wc]) if wc is not None else None
            lc = _col(H, r"\bLAMP", r"\bSOURCE\b")
            e = ScheduleEntry(
                tag=tag,
                category="lighting",
                width_m=None,
                height_m=None,
                watts=watts,
                description=desc,
                lamp_type=vals[lc] if lc is not None else "",
            )
            if watts is None:
                e.note = "watts not read from the schedule"
            sched.entries[tag] = asdict(e)
        elif sched.kind == "mechanical":
            kind = "other"
            for k, rx in _EQUIP:
                if rx.search(sched.title.upper()) or rx.search(tag):
                    kind = k
                    break
            mx = _col(H, r"MAX(IMUM)?\.?\s*(CFM|AIRFLOW)", r"(CFM|AIRFLOW)\s*MAX")
            mn = _col(H, r"MIN(IMUM)?\.?\s*(CFM|AIRFLOW)", r"(CFM|AIRFLOW)\s*MIN")
            cf = _col(H, r"\bCFM\b", r"AIRFLOW", r"\bL/S\b")
            nk = _col(H, r"\bNECK\b", r"\bINLET\b", r"\bSIZE\b")
            rec = {
                "tag": tag,
                "kind": kind,
                "cfm_max": _number(vals[mx]) if mx is not None else None,
                "cfm_min": _number(vals[mn]) if mn is not None else None,
                "cfm": _number(vals[cf]) if cf is not None and cf not in (mx, mn) else None,
                "neck_size": vals[nk] if nk is not None else "",
                "description": desc,
                "values": dict(zip(H, vals)),
            }
            sched.equipment.append(rec)


# ------------------------------------------------------------------ public API


def extract_schedules(sheet: dict, sheet_id: Optional[str] = None) -> List[PdfSchedule]:
    """Every schedule-titled ruled table on one ingested sheet."""
    if sheet_id:
        sheet = {**sheet, "sheet_id": sheet_id}
    hs, vs = _segments(sheet)
    texts = [t for t in sheet.get("text") or [] if (t.get("text") or "").strip()]
    used: set = set()
    out: List[PdfSchedule] = []
    comps = sorted(
        _components(hs, vs), key=lambda g: (min(h[0] for h in g[0]), min(v[0] for v in g[1]))
    )
    for ch, cv in comps:
        s = _read_table(sheet, ch, cv, texts, used)
        if s is not None:
            out.append(s)
    return out


def schedules_for_sheets(sheets_dir, sheet_ids: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
    """Run ``extract_schedules`` on every ``sheet_NNN.json`` in a directory and
    write ``schedules_NNN.json`` beside each. Returns {sheet file: result}."""
    d = Path(sheets_dir)
    results: Dict[str, dict] = {}
    for p in sorted(d.glob("sheet_[0-9][0-9][0-9].json")):
        sheet = json.loads(p.read_text())
        found = extract_schedules(sheet, (sheet_ids or {}).get(p.name))
        res = {
            "schema": SCHEMA,
            "sheet": p.name,
            "schedules": [s.to_dict() for s in found],
        }
        (d / p.name.replace("sheet_", "schedules_")).write_text(json.dumps(res, indent=2))
        results[p.name] = res
    return results


__all__ = ["PdfSchedule", "extract_schedules", "schedules_for_sheets", "SCHEMA"]
