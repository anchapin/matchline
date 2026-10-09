"""Wall-type legend on a drawing set (#747): tag -> assembly description.

Drawing sets name exterior wall assemblies on a wall-type legend (``W1  8" CMU
W/ 2" RIGID INSUL``) and tag each wall on the plan with the same mark. The
legend is read from the sheets' vector text: a tag-shaped span (``TAG_RE``,
same marks as door/window tags) followed on its own text row by a description
the construction library classifies as exactly one exterior-wall class. A row
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
LEGEND_CONFIDENCE = 0.8


def _tag_of(text: str):
    t = text.strip()
    if TAG_RE.match(t):
        return t, ""
    m = INLINE_RE.match(t)
    if m and TAG_RE.match(m.group(1)):
        return m.group(1), m.group(2).strip()
    return None, ""


def legend_rows(sheet) -> List[Tuple[str, str]]:
    """``(tag, description)`` rows on one sheet that name one wall class."""
    spans = []
    for t in _get(sheet, "text") or []:
        s = str(_get(t, "text") or "").strip()
        if s:
            spans.append((s, tuple(float(v) for v in _get(t, "bbox"))))
    out = []
    for i, (s, (x0, y0, x1, y1)) in enumerate(spans):
        tag, desc = _tag_of(s)
        if tag is None:
            continue
        if not desc:
            yc, h = (y0 + y1) / 2, max(y1 - y0, 1.0)
            row = sorted(
                (sp for j, sp in enumerate(spans)
                 if j != i and abs((sp[1][1] + sp[1][3]) / 2 - yc) <= ROW_TOL * h
                 and sp[1][0] >= x1 - 1.0),
                key=lambda sp: sp[1][0],
            )  # fmt: skip
            parts, edge, gap = [], x1, FIRST_GAP_PT
            for txt, bb in row:
                if bb[0] - edge > gap or _tag_of(txt)[0] is not None:
                    break
                parts.append(txt)
                edge, gap = max(edge, bb[2]), WORD_GAP_PT
            desc = " ".join(parts)
        if desc and classify(desc, "ExteriorWall")[0] is not None:
            out.append((tag, desc))
    return out


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
        for tag, desc in legend_rows(sheet):
            if tag in skip:
                continue
            ctype, why = classify(desc, "ExteriorWall")
            seen.setdefault(tag, []).append(
                {"description": desc, "sheet_id": sid, "construction_type": ctype, "why": why}
            )
    legend, conflicts = {}, {}
    for tag, rows in seen.items():
        if len({r["construction_type"] for r in rows}) == 1:
            legend[tag] = rows[0]
        else:
            conflicts[tag] = rows
    return legend, conflicts
