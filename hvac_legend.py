"""Mechanical symbol legend on a vector sheet (#744).

Real mechanical sets draw diffusers, grilles, VAV boxes and thermostats in
each firm's own style, and almost always carry a symbol legend: a title
("SYMBOL LEGEND", "HVAC LEGEND", "SYMBOLS") over rows of a drawn symbol with
its description to the right. ``read_legend`` finds those rows and maps each
description to one of the HVAC detector classes (``synth.mech.MECH_CLASSES``)
from its words only, never from the symbol's shape. A row whose description
names none of them (a damper, a smoke detector, "SUPPLY GRILLE", which could
be either an outlet or a grille) keeps ``cls=None`` and goes to review.

The symbol's box (``symbol_bbox_pt``, sheet points, y down) is what a later
slice cuts as a one-shot template for the NCC/WiSARD cascade in
``hvac_trace``; this module only reads the legend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# a legend title is the whole text item, optionally led by the discipline
# (a bare singular "SYMBOL" is the legend's own column header, not a title)
LEGEND_TITLE_RX = re.compile(
    r"^(MECHANICAL\s+|HVAC\s+|M\s+)?(SYMBOLS|SYMBOLS?\s+LEGEND|LEGEND(\s+OF\s+SYMBOLS)?)$"
)
# description -> class, first match wins; order matters (a "RETURN AIR
# DIFFUSER" is a return, so grille is tried before diffuser)
CLASS_RX = [
    ("sensor", re.compile(r"\bTHERMOSTAT|\bT-?STAT\b|\b(TEMPERATURE|SPACE|ROOM|ZONE)\s+SENSOR")),
    # an "air terminal" alone is the diffuser or grille (IfcAirTerminal); only a
    # terminal unit is the box
    ("vav", re.compile(r"\bVAV\b|\bVARIABLE\s+AIR\s+VOLUME|\bTERMINAL\s+UNIT")),
    ("ahu", re.compile(r"\bAHU\b|\bAIR\s+HANDL|\bRTU\b|\bROOFTOP\s+UNIT")),
    (
        "grille",
        re.compile(
            r"\b(RETURN|EXHAUST|TRANSFER)\b.*\b(GRILLE|REGISTER|DIFFUSER|INLET)"
            r"|\b(GRILLE|REGISTER)\b.*\b(RETURN|EXHAUST|TRANSFER)\b"
        ),
    ),
    ("diffuser", re.compile(r"\bDIFFUSER\b")),
]
# rows sit below the title, within this window of its left edge
WINDOW_LEFT_PT = 30.0
WINDOW_RIGHT_PT = 450.0
MAX_DEPTH_PT = 500.0
# a gap this many row pitches tall ends the legend
END_GAP_PITCHES = 3.0
# a primitive wider or taller than this is a frame or a rule, not a symbol
MAX_SYMBOL_PT = 80.0
# a vertical line at least this share of a row pitch tall is a rule
RULE_PITCH_SHARE = 0.8
CONFIDENCE = 0.7


@dataclass
class LegendRow:
    description: str
    cls: Optional[str]
    symbol_bbox_pt: List[float] = field(default_factory=list)  # x0, y0, x1, y1, y down


@dataclass
class Legend:
    title: str
    bbox_pt: List[float]
    rows: List[LegendRow] = field(default_factory=list)

    @property
    def unmapped(self) -> List[LegendRow]:
        return [r for r in self.rows if r.cls is None]


def classify(description: str) -> Optional[str]:
    """HVAC detector class a legend description names, or None."""
    u = " ".join(description.upper().split())
    for cls, rx in CLASS_RX:
        if rx.search(u):
            return cls
    return None


def _prim_bbox(prim: dict) -> Optional[List[float]]:
    if len(prim.get("bbox") or []) == 4:
        return [float(v) for v in prim["bbox"]]
    xs, ys = [], []
    for _op, coords in prim.get("segments") or []:
        for p in coords or []:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _lines(texts: list) -> list:
    """Text items grouped into lines: [(cy, x0, joined text)], top to bottom."""
    items = sorted(texts, key=lambda t: ((t["bbox"][1] + t["bbox"][3]) / 2, t["bbox"][0]))
    out: list = []
    for t in items:
        cy = (t["bbox"][1] + t["bbox"][3]) / 2
        hh = max((t["bbox"][3] - t["bbox"][1]) / 2, 1.0)
        if out and abs(cy - out[-1][0]) <= hh:
            out[-1][1].append(t)
        else:
            out.append([cy, [t]])
    return [
        (
            cy,
            min(t["bbox"][0] for t in ts),
            " ".join(t["text"].strip() for t in sorted(ts, key=lambda t: t["bbox"][0])),
        )
        for cy, ts in out
    ]


def read_legend(sheet: dict) -> List[Legend]:
    """Every symbol legend on a vector sheet, with its rows classified."""
    texts = [t for t in sheet.get("text") or [] if (t.get("text") or "").strip()]
    boxes = [b for b in (_prim_bbox(p) for p in sheet.get("primitives") or []) if b]
    boxes = [b for b in boxes if b[2] - b[0] <= MAX_SYMBOL_PT and b[3] - b[1] <= MAX_SYMBOL_PT]
    out: List[Legend] = []
    for title in texts:
        name = " ".join(title["text"].upper().split())
        if not LEGEND_TITLE_RX.match(name):
            continue
        tx0, _ty0, _tx1, ty1 = title["bbox"]
        left, right = tx0 - WINDOW_LEFT_PT, tx0 + WINDOW_RIGHT_PT
        below = [
            t
            for t in texts
            if t is not title
            and left <= t["bbox"][0] <= right
            and ty1 < (t["bbox"][1] + t["bbox"][3]) / 2 <= ty1 + MAX_DEPTH_PT
        ]
        lines = _lines(below)
        if not lines:
            continue
        gaps = sorted(b[0] - a[0] for a, b in zip(lines, lines[1:]))
        pitch = gaps[len(gaps) // 2] if gaps else 20.0
        kept = [lines[0]]
        for a, b in zip(lines, lines[1:]):
            if b[0] - a[0] > END_GAP_PITCHES * pitch:
                break
            kept.append(b)
        band = max(0.5 * pitch, 6.0)
        # a vertical line a row pitch tall is a ruled legend's cell divider or
        # border, not part of a symbol
        rule = RULE_PITCH_SHARE * pitch
        leg = Legend(title=title["text"].strip(), bbox_pt=list(title["bbox"]))
        for cy, dx0, desc in kept:
            sym = [
                b
                for b in boxes
                if left <= b[0]
                and b[2] <= dx0 - 1.0
                and abs((b[1] + b[3]) / 2 - cy) <= band
                and not (b[2] - b[0] <= 0.5 and b[3] - b[1] >= rule)
            ]
            if not sym:
                # a wrapped description line continues the row above it; a line
                # with no symbol before any row is a column header
                if leg.rows:
                    leg.rows[-1].description += " " + desc
                    leg.rows[-1].cls = classify(leg.rows[-1].description)
                continue
            bb = [
                min(b[0] for b in sym),
                min(b[1] for b in sym),
                max(b[2] for b in sym),
                max(b[3] for b in sym),
            ]
            leg.rows.append(LegendRow(desc, classify(desc), [round(v, 2) for v in bb]))
        if leg.rows:
            out.append(leg)
    return out


# --- one-shot templates from the legend's own symbols ---------------------

# white margin kept around a symbol when it is cut as a template (sheet pt)
TEMPLATE_PAD_PT = 2.0
# a template with fewer ink pixels than this is a speck, not a symbol
MIN_TEMPLATE_INK_PX = 12
INK_LEVEL = 128


@dataclass
class LegendTemplate:
    cls: str
    description: str
    image: "object"  # numpy array, grayscale, the sheet raster's own pixels
    symbol_bbox_px: List[float]  # where it was cut (the legend row itself)


def legend_templates(gray, legends: List[Legend], px_per_pt: float) -> List[LegendTemplate]:
    """Cut each mapped legend row's symbol out of the sheet raster as a
    one-shot template for ``hvac_trace.detect_legend_symbols``.

    ``gray`` is the sheet's own raster (``pdf_ingest`` ``raster_file``, y down)
    and ``px_per_pt`` its scale, so the template is the firm's symbol at the
    size the sheet draws it. Unmapped rows and specks are skipped."""
    import numpy as np

    out: List[LegendTemplate] = []
    H, W = gray.shape
    pad = TEMPLATE_PAD_PT
    for leg in legends:
        for r in leg.rows:
            if r.cls is None or len(r.symbol_bbox_pt) != 4:
                continue
            x0, y0, x1, y1 = r.symbol_bbox_pt
            c0 = max(int(np.floor((x0 - pad) * px_per_pt)), 0)
            r0 = max(int(np.floor((y0 - pad) * px_per_pt)), 0)
            c1 = min(int(np.ceil((x1 + pad) * px_per_pt)), W)
            r1 = min(int(np.ceil((y1 + pad) * px_per_pt)), H)
            if c1 - c0 < 3 or r1 - r0 < 3:
                continue
            img = np.asarray(gray[r0:r1, c0:c1], dtype=np.float64)
            if int((img < INK_LEVEL).sum()) < MIN_TEMPLATE_INK_PX:
                continue
            out.append(LegendTemplate(r.cls, r.description, img, [c0, r0, c1, r1]))
    return out
