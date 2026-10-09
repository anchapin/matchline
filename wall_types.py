"""Wall-type legend on a drawing set (#747): tag -> assembly description.

Drawing sets name exterior wall assemblies on a wall-type legend (``W1  8" CMU
W/ 2" RIGID INSUL``) and tag each wall on the plan with the same mark. The
legend is read from the sheets' vector text: a tag-shaped span (``TAG_RE``,
same marks as door/window tags) followed on its own text row by a description
the construction library classifies as exactly one exterior-wall class, plus
any lines the description wraps onto (#828). A row
whose words name no class, or more than one, is not a legend row; nothing is
guessed. The description becomes an unset-U construction, and the cited
library fills its U from ASHRAE 90.1 Table 5.5 once a climate zone is known.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

from construction_library import classify
from plan_walls import TAG_RE, _get

# "W1 - 8" CMU ..." written as one span
INLINE_RE = re.compile(r"^([A-Z]{1,3}-?\d{1,3}[A-Z]?)\s*[-:.\u2013]?\s+(\S.*)$")
FIRST_GAP_PT = 144.0  # tag to description, a legend table column apart
WORD_GAP_PT = 72.0  # between the description's own spans
ROW_TOL = 0.6  # row match: centre lines within this fraction of the tag height
LINE_GAP = 1.6  # a wrapped line sits within this many line heights of the one above
ALIGN_TOL_PT = 12.0  # wrapped lines start this close to the description's left edge
MAX_LINES = 8
LEGEND_CONFIDENCE = 0.8


def _tag_of(text: str):
    t = text.strip()
    if TAG_RE.match(t):
        return t, ""
    m = INLINE_RE.match(t)
    if m and TAG_RE.match(m.group(1)):
        return m.group(1), m.group(2).strip()
    return None, ""


def _row_text(spans, first, gap=WORD_GAP_PT):
    """Spans chained left to right from ``first`` with gaps under ``gap``.

    Tag-shaped words (``R-19``) are kept: on a wrapped line they are text, and
    a real next row is caught by its tag in the tag column."""
    parts, edge = [first[0]], first[1][2]
    for txt, bb in sorted((sp for sp in spans if sp[1][0] > first[1][0]), key=lambda sp: sp[1][0]):
        if bb[0] - edge > gap:
            break
        parts.append(txt)
        edge = max(edge, bb[2])
    return " ".join(parts)


def _continuation(spans, used, tag_bb, lo, hi, y_prev, h):
    """Lines wrapped under a legend description (#828), top to bottom.

    A line continues the description when it starts in the description's
    column (``lo``..``hi``) and sits within ``LINE_GAP`` line heights of the
    line above. Nothing in the tag's own column may share the
    line: that is the next legend row (or another block) starting.
    """
    out = []
    for _ in range(MAX_LINES - 1):
        below = [
            sp for j, sp in enumerate(spans)
            if j not in used and 0.5 * h < (sp[1][1] + sp[1][3]) / 2 - y_prev <= LINE_GAP * h
        ]  # fmt: skip
        if not below:
            break
        yc = min((sp[1][1] + sp[1][3]) / 2 for sp in below)
        line = [sp for sp in below if abs((sp[1][1] + sp[1][3]) / 2 - yc) <= ROW_TOL * h]
        if any(sp[1][0] < lo and sp[1][2] > tag_bb[0] - 1.0 for sp in line):
            break  # text in the tag column: the next row or another block
        starts = [sp for sp in line if lo <= sp[1][0] <= hi]
        if not starts:
            break
        first = min(starts, key=lambda sp: sp[1][0])
        out.append(_row_text(line, first))
        used.update(i for i, sp in enumerate(spans) if sp in line)
        y_prev = yc
    return out


def _rows(sheet) -> List[Tuple[str, str, str]]:
    """``(tag, description, full text)`` for each legend row naming one class.

    ``description`` is the text the class came from: the first line when it
    names a class, else the first line joined with its wrapped lines. The
    first line wins so a later line (``MTL STUD FURRING`` behind a CMU wall)
    cannot reclassify the wall; ``full text`` keeps every line for the record.
    """
    spans = []
    for t in _get(sheet, "text") or []:
        s = str(_get(t, "text") or "").strip()
        if s:
            spans.append((s, tuple(float(v) for v in _get(t, "bbox"))))
    spans.sort(key=lambda sp: (sp[1][1], sp[1][0]))  # top to bottom
    out, wrapped = [], set()
    for i, (s, bb) in enumerate(spans):
        x0, y0, x1, y1 = bb
        tag, desc = _tag_of(s)
        if tag is None or i in wrapped:
            continue  # a tag-shaped word on a wrapped line is not a row
        yc, h = (y0 + y1) / 2, max(y1 - y0, 1.0)
        used = {i}
        if desc:
            lo, hi = x0 + 1.0, x1  # wrapped lines sit indented past the tag
        else:
            row = sorted(
                (sp for j, sp in enumerate(spans)
                 if j != i and abs((sp[1][1] + sp[1][3]) / 2 - yc) <= ROW_TOL * h
                 and sp[1][0] >= x1 - 1.0),
                key=lambda sp: sp[1][0],
            )  # fmt: skip
            parts, edge, gap, first_x = [], x1, FIRST_GAP_PT, None
            for txt, rb in row:
                if rb[0] - edge > gap or _tag_of(txt)[0] is not None:
                    break
                parts.append(txt)
                used.add(spans.index((txt, rb)))
                first_x = rb[0] if first_x is None else first_x
                edge, gap = max(edge, rb[2]), WORD_GAP_PT
            desc = " ".join(parts)
            if first_x is None:
                continue
            tol = max(ALIGN_TOL_PT, h)
            lo, hi = first_x - tol, first_x + 3 * tol
        if not desc:
            continue
        more = _continuation(spans, used, bb, lo, hi, yc, h)
        wrapped |= used
        full = " ".join([desc] + more)
        if classify(desc, "ExteriorWall")[0] is not None:
            out.append((tag, desc, full))
        elif more and classify(full, "ExteriorWall")[0] is not None:
            out.append((tag, full, full))
    return out


def legend_rows(sheet) -> List[Tuple[str, str]]:
    """``(tag, description)`` rows on one sheet that name one wall class."""
    return [(t, d) for t, d, _f in _rows(sheet)]


def read_legend(
    sheets: Iterable[Tuple[str, object]], exclude: Iterable[str] = ()
) -> Tuple[Dict[str, dict], Dict[str, List[dict]]]:
    """Wall-type legend over ``(sheet_id, sheet)`` pairs.

    Returns ``(legend, conflicts)``. ``legend[tag]`` is ``{description,
    sheet_id, construction_type, why}`` from the first sheet stating it; a tag
    whose rows name different wall classes goes to ``conflicts`` instead.
    Tags in ``exclude`` (the door/window schedule's) are never wall types.
    """
    skip = set(exclude)
    seen: Dict[str, List[dict]] = {}
    for sid, sheet in sheets:
        for tag, desc, full in _rows(sheet):
            if tag in skip:
                continue
            ctype, why = classify(desc, "ExteriorWall")
            seen.setdefault(tag, []).append(
                {
                    "description": desc,
                    "full_text": full,
                    "sheet_id": sid,
                    "construction_type": ctype,
                    "why": why,
                }
            )
    legend, conflicts = {}, {}
    for tag, rows in seen.items():
        if len({r["construction_type"] for r in rows}) == 1:
            legend[tag] = rows[0]
        else:
            conflicts[tag] = rows
    return legend, conflicts
