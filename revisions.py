"""Revision and addenda tracking on a vector drawing set (#993).

Real sets change over time: a revision table in the title block lists the
issued revisions (mark, date, description), revision clouds circle what
changed on each sheet, and addenda re-issue sheets between bid dates.  This
module reads all three off already-ingested vector sheets and flags the
ambiguous marks a reviewer has to settle: a cloud whose mark no table entry
names, and a table entry whose mark this sheet claims (title block) without a
matching cloud on the sheet.  The aggregated picture (which revision touched
which sheets) lands in the drawing-set index via ``sheet_index``; the
ambiguities land in the review queue; ``takeoff_diff`` diffs two revisions'
takeoffs with counts per change class.

All geometry here is heuristic and never edits the model: it reads sheets and
produces facts and review items only.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

SCHEMA = "matchline.revisions/1"

# a revision table title, matched against the upper-cased whole text item
REV_TABLE_TITLE_RX = re.compile(
    r"^(REV(ISION)?S?\s*(SCHEDULE|HISTORY|INDEX|LIST)?|REVISION MARKS|REV\.?\s*HISTORY)$"
)
# a column header row, not data (MARK DATE DESCRIPTION / NO. BY DATE ...)
_TABLE_HEADER_RX = re.compile(
    r"^(MARK|NO\.?|#|REV)\b[,/\s]*(DATE|BY|DESCRIPTION|ISSUE|TITLE|REMARKS?)"
)
# a revision mark: "2", "B", "A1", "R2" with an optional trailing punctuation
MARK_RX = re.compile(r"^(?:MARK\s*)?([0-9]{1,2}|[A-Z]|[A-Z][0-9]{0,2})[.):\]]?$")
_DATE_RX = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s+\d{1,2},?\s*\d{2,4})\b",
    re.IGNORECASE,
)
ADDENDUM_RX = re.compile(r"\bADDEND(UM|A)\b")

# revision tables sit under their title in the title block column
MAX_TABLE_DEPTH_PT = 600.0
TABLE_COLUMN_SLACK_PT = 120.0
MAX_TABLE_ROWS = 50

# revision clouds: one closed path with many short segments (the scallop
# ring), or a chain of small primitives that closes back on itself
MIN_ARCS = 8
MIN_LINKS = 5
MAX_CLOUD_SIDE_PT = 500.0
MAX_SEG_PT = 60.0
LINK_GAP_PT = 4.0
CLOSE_PT = 4.0
MAX_CLOUD_ASPECT = 5.0
CHAIN_FILL_MAX = 0.6
MIN_CHAIN_POINTS = 3
MAX_PRIMS = 6000


def _box(item) -> Optional[Tuple[float, float, float, float]]:
    b = item.get("bbox") if isinstance(item, dict) else None
    if not b or len(b) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in b[:4])
    except (TypeError, ValueError):
        return None
    if x1 < x0 or y1 < y0:
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
    return (x0, y0, x1, y1)


def _lines(spans: List[Tuple[str, Tuple[float, float, float, float]]]) -> List[List]:
    """Group text items into reading-order lines by vertical overlap."""
    out: List[List] = []
    for text, box in sorted(spans, key=lambda s: (s[1][1], s[1][0])):
        cy = (box[1] + box[3]) / 2.0
        h = max(box[3] - box[1], 1.0)
        if out:
            last = out[-1]
            lbox = last[-1][1]
            lcy = (lbox[1] + lbox[3]) / 2.0
            if abs(cy - lcy) <= (h + (lbox[3] - lbox[1])) / 3.0:
                last.append((text, box))
                continue
        out.append([(text, box)])
    return out


def _line_text(line: List) -> Tuple[str, Tuple[float, float, float, float]]:
    parts = [t for t, _ in sorted(line, key=lambda s: s[1][0])]
    box = line[0][1]
    for _, b in line:
        box = (
            min(box[0], b[0]),
            min(box[1], b[1]),
            max(box[2], b[2]),
            max(box[3], b[3]),
        )
    return " ".join(parts), box


def read_revision_table(sheet: dict) -> List[dict]:
    """Revision table rows on one sheet, read from under the table title.

    Each row carries ``mark`` (the revision number or letter), an optional
    ``date`` and the remaining words as ``description``.  A sheet with no
    revision table returns ``[]``.
    """
    items = [t for t in (sheet.get("text") or []) if (t.get("text") or "").strip()]
    spans = [(t["text"].upper().strip(), _box(t)) for t in items]
    spans = [(t, b) for t, b in spans if b is not None]
    title = next((b for t, b in spans if REV_TABLE_TITLE_RX.match(t)), None)
    if title is None:
        return []
    rows: List[dict] = []
    for line in _lines(spans):
        text, box = _line_text(line)
        if not (box[1] > title[3] and box[1] - title[3] <= MAX_TABLE_DEPTH_PT):
            continue
        if not (
            box[0] >= title[0] - TABLE_COLUMN_SLACK_PT
            and box[0] <= title[2] + TABLE_COLUMN_SLACK_PT
        ):
            continue
        if _TABLE_HEADER_RX.match(text):
            continue
        tokens = text.split()
        m = MARK_RX.match(tokens[0]) if tokens else None
        if not m:
            continue
        mark = m.group(1)
        rest = tokens[1:]
        date = None
        joined = " ".join(rest)
        dm = _DATE_RX.search(joined)
        if dm:
            date = dm.group(1)
            rest_words = (joined[: dm.start()] + " " + joined[dm.end() :]).split()
        else:
            rest_words = rest
        rows.append(
            {
                "mark": mark,
                "date": date,
                "description": " ".join(rest_words),
                "bbox_pt": [round(v, 1) for v in box],
            }
        )
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _segs_points(
    segs: List,
) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]], int]:
    first = last = None
    n = 0
    for seg in segs or []:
        op = str(seg[0]).upper() if isinstance(seg, (list, tuple)) and seg else ""
        if op == "Z":
            continue
        pts = seg[1] if len(seg) > 1 else []
        for pt in pts or []:
            if len(pt) >= 2:
                try:
                    p = (float(pt[0]), float(pt[1]))
                except (TypeError, ValueError):
                    continue
                if first is None:
                    first = p
                last = p
                n += 1
    return first, last, n


def _closed(first, last) -> bool:
    if first is None or last is None:
        return False
    return abs(first[0] - last[0]) <= CLOSE_PT and abs(first[1] - last[1]) <= CLOSE_PT


def _aspect(b) -> float:
    w = max(b[2] - b[0], 1.0)
    h = max(b[3] - b[1], 1.0)
    return max(w, h) / min(w, h)


def find_revision_clouds(sheet: dict) -> List[dict]:
    """Revision clouds on one sheet, by shape only.

    A cloud is either one closed path primitive with at least ``MIN_ARCS``
    segments inside the size bound, or a chain of at least ``MIN_LINKS`` small
    primitives whose union box is small enough and not a straight sliver and
    stays ring-like.  Chain members are open arcs only: closed loops are
    detail geometry (schedule cells, furniture) and never chain.
    """
    prims = (sheet.get("primitives") or [])[:MAX_PRIMS]
    boxes = []
    for p in prims:
        b = _box(p)
        if b is not None:
            boxes.append((b, p.get("segments")))
    clouds: List[dict] = []
    used: set = set()
    info: List[tuple] = []
    for b, segs in boxes:
        info.append(_segs_points(segs))
    for i, (b, _) in enumerate(boxes):
        w, h = b[2] - b[0], b[3] - b[1]
        if max(w, h) > MAX_CLOUD_SIDE_PT:
            continue
        first, last, n = info[i]
        if n >= MIN_ARCS and _closed(first, last):
            clouds.append({"bbox_pt": [round(v, 1) for v in b], "segments": n})
            used.add(i)
    # (b) chains of small OPEN primitives: a real cloud drawn scallop by
    # scallop is a ring of open arcs.  Closed loops (schedule cells, furniture
    # boxes, plan outlines) and single straight segments (table rules, grid
    # lines) are detail geometry and never chain, however
    # tightly they pack; the union box must also stay ring-like, not solid.
    small = [
        (i, b)
        for i, (b, _) in enumerate(boxes)
        if (
            i not in used
            and _diag(b) <= MAX_SEG_PT
            and not _closed(info[i][0], info[i][1])
            and info[i][2] >= MIN_CHAIN_POINTS
        )
    ]
    parent = {i: i for i, _ in small}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a in range(len(small)):
        ia, ba = small[a]
        for c in range(a + 1, len(small)):
            ib, bb = small[c]
            gap_x = max(bb[0] - ba[2], ba[0] - bb[2], 0.0)
            gap_y = max(bb[1] - ba[3], ba[1] - bb[3], 0.0)
            if gap_x <= LINK_GAP_PT and gap_y <= LINK_GAP_PT:
                ra, rb = find(ia), find(ib)
                if ra != rb:
                    parent[rb] = ra
    groups: Dict[int, List] = {}
    for i, b in small:
        groups.setdefault(find(i), []).append(b)
    for members in groups.values():
        if len(members) < MIN_LINKS:
            continue
        u = (
            min(b[0] for b in members),
            min(b[1] for b in members),
            max(b[2] for b in members),
            max(b[3] for b in members),
        )
        if max(u[2] - u[0], u[3] - u[1]) > MAX_CLOUD_SIDE_PT:
            continue
        if _aspect(u) > MAX_CLOUD_ASPECT:
            continue
        uw, uh = u[2] - u[0], u[3] - u[1]
        filled = sum((b[2] - b[0]) * (b[3] - b[1]) for b in members)
        if filled > CHAIN_FILL_MAX * uw * uh:
            continue
        clouds.append({"bbox_pt": [round(v, 1) for v in u], "segments": len(members)})
    return clouds


def _diag(b) -> float:
    return ((b[2] - b[0]) ** 2 + (b[3] - b[1]) ** 2) ** 0.5


def _span_center(box) -> Tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _in(b, box) -> bool:
    cx, cy = _span_center(box)
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def analyze_sheet(sheet: dict, title_revision: Optional[str] = None) -> dict:
    """One sheet's revision picture: table rows, clouds, and their links.

    ``title_revision`` is the sheet's own title-block revision mark (the
    ``REV`` field), when the title block shows one.  Ambiguities:

    * ``cloud_without_table_entry``: a cloud whose mark no table entry names
      (or any cloud at all when the sheet has no revision table).
    * ``table_entry_without_cloud``: a table entry whose mark equals the
      sheet's title-block revision but no cloud on the sheet carries it.
      Historical rows the sheet does not claim are normal and are not flagged.
    """
    table = read_revision_table(sheet)
    clouds = find_revision_clouds(sheet)
    marks = {(r["mark"] or "").upper() for r in table}
    text_items = [(t.get("text") or "", _box(t)) for t in (sheet.get("text") or [])]
    for cloud in clouds:
        b = tuple(cloud["bbox_pt"])
        hit = None
        for t, box in text_items:
            if not box or not _in(b, box):
                continue
            for tok in t.upper().replace(")", " ").replace("(", " ").split():
                # any revision-mark-shaped token inside the cloud is the
                # cloud's mark; the ambiguity pass decides whether the table
                # carries it
                m = MARK_RX.match(tok)
                if m:
                    hit = m.group(1)
                    break
            if hit:
                break
        cloud["mark"] = hit
    matched = {(c.get("mark") or "").upper() for c in clouds if c.get("mark")}
    ambiguous: List[dict] = []
    for cloud in clouds:
        mk = (cloud.get("mark") or "").upper()
        # a cloud is ambiguous when no table entry names its mark, when its
        # mark text names a mark the table does not carry, or when there is no
        # table at all for it to belong to
        if not table or not mk or mk not in marks:
            ambiguous.append(
                {
                    "kind": "cloud_without_table_entry",
                    "bbox_pt": cloud["bbox_pt"],
                    "mark": cloud.get("mark"),
                }
            )
    tr = (title_revision or "").upper() or None
    if table and tr:
        # a row the sheet itself claims (title-block REV) with no cloud
        # carrying it is ambiguous; historical rows the sheet does not claim
        # are normal and stay unflagged
        for row in table:
            if row["mark"].upper() == tr and row["mark"].upper() not in matched:
                ambiguous.append(
                    {
                        "kind": "table_entry_without_cloud",
                        "bbox_pt": row["bbox_pt"],
                        "mark": row["mark"],
                    }
                )
    return {"table": table, "clouds": clouds, "ambiguous": ambiguous}


def index_revisions(sheet_marks: List[Tuple[Optional[str], bool]]) -> Dict[str, dict]:
    """Aggregate per-sheet revision marks into the drawing-set index.

    ``sheet_marks`` is one ``(mark, is_addenda)`` pair per sheet (``None`` when
    the title block shows no revision).  Returns the index ``revisions`` map:
    each mark with the sheets carrying it, plus the addenda-flagged sheets.
    """
    marks: Dict[str, dict] = {}
    addenda: List[str] = []
    for n, (mark, is_addenda) in enumerate(sheet_marks, start=1):
        sheet_no = f"sheet_{n:03d}"
        if mark:
            marks.setdefault(mark.upper(), {"sheets": []})["sheets"].append(sheet_no)
        if is_addenda:
            addenda.append(sheet_no)
    out = {k: {"sheets": sorted(v["sheets"])} for k, v in sorted(marks.items())}
    if addenda:
        out["__addenda_sheets__"] = {"sheets": addenda}
    return out
