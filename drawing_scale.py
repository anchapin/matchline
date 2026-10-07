"""Drawing scale from the sheet itself (#739).

Synthetic sheets use a fixed ``PX_PER_M``; real sheets state their scale only as
annotation. This module reads it from an ingested sheet (``pdf_ingest``) and
gives ``m_per_pt`` (real metres per sheet point), and ``m_per_px`` once the
raster resolution is known. Three sources, each with provenance:

* **scale notes**: architectural (``1/8" = 1'-0"``), engineering
  (``1" = 20'``) and metric (``1:100``); ``NTS`` / ``NOT TO SCALE`` marks a
  sheet that must not produce areas;
* **graphic scale bars**: a row of numeric labels starting at 0 with a unit
  word nearby, fitted linearly against their positions;
* **dimension strings**: feet-inches or metric lengths centred on a dimension
  line, measured along that line.

The note wins when there is one scale on the sheet; bars and dimensions
cross-check it. A disagreement beyond :data:`AGREE_TOL` (1%) goes to review.
A sheet with several scales (one per view) gets a ``views`` list and no single
sheet scale; :func:`scale_at` picks the view scale for a point. No scale found
means ``m_per_pt`` is ``None`` with the reason, never a guess.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

INCH_M = 0.0254
PT_M = INCH_M / 72.0  # one sheet point on paper, in metres
AGREE_TOL = 0.01  # sources agree within 1%
MIN_DIMENSIONS = 3  # dimension strings needed before they can stand alone

Box = Tuple[float, float, float, float]

_FRACTIONS = {
    "½": " 1/2",
    "¼": " 1/4",
    "¾": " 3/4",
    "⅛": " 1/8",
    "⅜": " 3/8",
    "⅝": " 5/8",
    "⅞": " 7/8",
}


def normalize(s: str) -> str:
    """ASCII quotes and fractions, single spaces, upper case."""
    for a in "\u2018\u2019\u2032\u00b4`":
        s = s.replace(a, "'")
    for a in "\u201c\u201d\u2033":
        s = s.replace(a, '"')
    s = s.replace("''", '"')
    for k, v in _FRACTIONS.items():
        s = s.replace(k, v)
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", s).strip().upper()


# ----------------------------------------------------------------------- lengths

_NUM = r"(\d+(?:\.\d+)?)"
_FRAC = r"(\d+)/(\d+)"
_FT_IN = re.compile(
    rf"^{_NUM}\s*'\s*(?:-\s*|\s)?(?:(\d+)(?:\s*-?\s*{_FRAC}|\s+{_FRAC})?|{_FRAC})?\s*\"?$"
)
_IN = re.compile(rf"^(?:(\d+)(?:\s*-\s*|\s+){_FRAC}|{_FRAC}|{_NUM})\s*\"$")
_METRIC = re.compile(rf"^{_NUM}\s*(MM|CM|M)$")


def parse_length_m(s: str) -> Optional[float]:
    """A written length in metres: ``12'-6"``, ``12'-6 1/2"``, ``12' 6"``,
    ``3'``, ``6"``, ``1/8"``, ``1 1/2"``, ``3600 mm``, ``3.6 m``. None if it
    is not a length. Bare numbers are not lengths (units are ambiguous)."""
    t = normalize(s)
    m = _FT_IN.match(t)
    if m:
        ft = float(m.group(1))
        inch = 0.0
        if m.group(2):
            inch = float(m.group(2))
            if m.group(3):
                inch += float(m.group(3)) / float(m.group(4))
            elif m.group(5):
                inch += float(m.group(5)) / float(m.group(6))
        elif m.group(7):
            inch = float(m.group(7)) / float(m.group(8))
        if inch >= 12:
            return None
        return (ft * 12 + inch) * INCH_M
    m = _IN.match(t)
    if m:
        if m.group(1):
            inch = float(m.group(1)) + float(m.group(2)) / float(m.group(3))
        elif m.group(4):
            inch = float(m.group(4)) / float(m.group(5))
        else:
            inch = float(m.group(6))
        return inch * INCH_M
    m = _METRIC.match(t)
    if m:
        return float(m.group(1)) * {"MM": 1e-3, "CM": 1e-2, "M": 1.0}[m.group(2)]
    return None


# ------------------------------------------------------------------- scale notes

_NTS = re.compile(r"\bN\.?\s?T\.?\s?S\.?(?![A-Z])|\bNOT\s+TO\s+SCALE\b")
_RATIO = re.compile(r"(?<![\d/])1\s*:\s*(\d+(?:\.\d+)?)(?![\d:])")
_EQ = re.compile(r"([\d\s/.]+\s*\")\s*=\s*([\d\s'\"\-/.]+)")


def parse_scale_note(s: str) -> Optional[object]:
    """``"NTS"`` for not-to-scale, a ratio (real / paper, e.g. 96.0 for
    ``1/8" = 1'-0"``) for a scale note, or None."""
    t = normalize(s)
    if _NTS.search(t):
        return "NTS"
    m = _EQ.search(t)
    if m:
        paper = parse_length_m(m.group(1).strip())
        real = parse_length_m(m.group(2).strip())
        if paper and real and real > paper:
            return real / paper
    m = _RATIO.search(t)
    if m and ("SCALE" in t or t.startswith("1")):
        r = float(m.group(1))
        if r > 1:
            return r
    return None


# ------------------------------------------------------------------- data access


def _texts(sheet) -> List[Tuple[str, Box]]:
    items = sheet["text"] if isinstance(sheet, dict) else sheet.text
    out = []
    for t in items:
        if isinstance(t, dict):
            out.append((t["text"], tuple(t["bbox"])))
        else:
            out.append((t.text, tuple(t.bbox)))
    return out


def _segments(sheet) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Straight segments of every path, as point pairs in sheet points."""
    prims = sheet["primitives"] if isinstance(sheet, dict) else sheet.primitives
    segs = []
    for p in prims:
        seq = p["segments"] if isinstance(p, dict) else p.segments
        cur = start = None
        for kind, pts in seq:
            if kind == "M":
                cur = start = tuple(pts[0])
            elif kind == "L" and cur is not None:
                nxt = tuple(pts[0])
                segs.append((cur, nxt))
                cur = nxt
            elif kind == "C":
                cur = tuple(pts[-1])
            elif kind == "Z" and cur is not None and start is not None and cur != start:
                segs.append((cur, start))
                cur = start
    return segs


def _px_per_pt(sheet) -> Optional[float]:
    return sheet.get("px_per_pt") if isinstance(sheet, dict) else sheet.px_per_pt


def _centre(b: Box) -> Tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


# ---------------------------------------------------------------------- sources


@dataclass
class Evidence:
    source: str  # note | bar | dimension
    text: str
    bbox: Box
    ratio: Optional[float]  # real / paper; None for NTS
    nts: bool = False


def find_notes(sheet) -> List[Evidence]:
    out = []
    for s, b in _texts(sheet):
        r = parse_scale_note(s)
        if r == "NTS":
            out.append(Evidence("note", s, b, None, nts=True))
        elif r is not None:
            out.append(Evidence("note", s, b, float(r)))
    return out


_BAR_NUM = re.compile(r"^(\d+(?:\.\d+)?)\s*('|M|FT|FEET)?$")
_FEET_WORDS = {"FEET", "FT", "FOOT", "'", "SCALE: FEET", "FEET SCALE"}
_METRE_WORDS = {"M", "METERS", "METRES", "METER", "METRE"}


def find_scale_bars(sheet) -> List[Evidence]:
    """Rows of numeric labels starting at 0, linear in x, with a unit."""
    texts = _texts(sheet)
    nums = []
    for s, b in texts:
        m = _BAR_NUM.match(normalize(s))
        if m:
            nums.append((float(m.group(1)), m.group(2), b))
    out = []
    used = set()
    for i, (v, u, b) in enumerate(nums):
        if v != 0 or i in used:
            continue
        h = b[3] - b[1]
        cy = _centre(b)[1]
        row = [(v, u, b, i)]
        for j, (v2, u2, b2) in enumerate(nums):
            if j != i and abs(_centre(b2)[1] - cy) <= max(1.0, 0.3 * h) and v2 > 0:
                if abs((b2[3] - b2[1]) - h) <= 0.3 * h + 0.5:
                    row.append((v2, u2, b2, j))
        if len(row) < 3:
            continue
        row.sort(key=lambda r: _centre(r[2])[0])
        vals = [r[0] for r in row]
        if vals != sorted(vals) or len(set(vals)) != len(vals):
            continue
        xs = [_centre(r[2])[0] for r in row]
        n = len(xs)
        mx, mv = sum(xs) / n, sum(vals) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            continue
        slope = sum((x - mx) * (v - mv) for x, v in zip(xs, vals)) / sxx  # units per pt
        resid = max(abs(mv + slope * (x - mx) - v) for x, v in zip(xs, vals))
        if slope <= 0 or resid > 0.02 * (vals[-1] - vals[0]):
            continue
        unit = _bar_unit([r[1] for r in row], row, texts)
        if unit is None:
            continue
        real_m_per_pt = slope * (0.3048 if unit == "ft" else 1.0)
        span = (row[0][2][0], min(r[2][1] for r in row), row[-1][2][2], max(r[2][3] for r in row))
        label = " ".join(f"{v:g}" for v in vals) + f" ({unit})"
        out.append(Evidence("bar", label, span, real_m_per_pt / PT_M))
        used.update(r[3] for r in row)
    return out


def _bar_unit(suffixes, row, texts) -> Optional[str]:
    for u in suffixes:
        if u in ("'", "FT", "FEET"):
            return "ft"
        if u == "M":
            return "m"
    x0 = min(r[2][0] for r in row)
    x1 = max(r[2][2] for r in row)
    y0 = min(r[2][1] for r in row)
    y1 = max(r[2][3] for r in row)
    h = y1 - y0
    for s, b in texts:
        t = normalize(s)
        if t not in _FEET_WORDS and t not in _METRE_WORDS:
            continue
        cx, cy = _centre(b)
        near_row = x0 - 6 * h <= cx <= x1 + 6 * h and y0 - 4 * h <= cy <= y1 + 4 * h
        if near_row:
            return "ft" if t in _FEET_WORDS else "m"
    return None


def find_dimensions(sheet) -> List[Evidence]:
    """Lengths written on a dimension line, measured along that line."""
    texts = _texts(sheet)
    segs = _segments(sheet)
    horiz, vert = [], []
    for (x0, y0), (x1, y1) in segs:
        if abs(y1 - y0) <= 0.01 * abs(x1 - x0) and abs(x1 - x0) > 0:
            horiz.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) <= 0.01 * abs(y1 - y0) and abs(y1 - y0) > 0:
            vert.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    out = []
    for s, b in texts:
        real = parse_length_m(s)
        if not real:
            continue
        w, h = b[2] - b[0], b[3] - b[1]
        cx, cy = _centre(b)
        if w >= h:  # horizontal text: dimension line below or through it
            run = _run_through(horiz, cy, cx, w, h)
        else:  # rotated text: vertical dimension line
            run = _run_through(vert, cx, cy, h, w)
        if run is None:
            continue
        out.append(Evidence("dimension", s, b, real / (run * PT_M)))
    return out


def _run_through(lines, c_perp, c_along, t_len, t_thick) -> Optional[float]:
    """Length of the collinear line run nearest the text (within 1.5 text
    heights across it) that passes the text centre, bridging the gap the
    text itself leaves in the line."""
    near = [ln for ln in lines if abs(ln[0] - c_perp) <= 1.5 * t_thick + 1.0]
    best = None
    for pos in sorted({round(ln[0], 1) for ln in near}, key=lambda p: abs(p - c_perp)):
        ivs = sorted((a, b) for p, a, b in near if abs(p - pos) <= 0.5)
        merged = []
        for a, b in ivs:
            if merged and a - merged[-1][1] <= t_len + 4 * t_thick:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            if a <= c_along <= b and (b - a) >= 0.8 * t_len:
                best = b - a
                break
        if best is not None:
            return best
    return None


# ----------------------------------------------------------------------- result


@dataclass
class SheetScale:
    ratio: Optional[float]  # real / paper, e.g. 96 for 1/8" = 1'-0"
    m_per_pt: Optional[float]
    m_per_px: Optional[float]
    source: Optional[str]  # note | bar | dimensions
    confidence: float
    needs_review: bool
    nts: bool
    reason: str
    views: List[Dict[str, object]] = field(default_factory=list)
    evidence: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = "matchline.sheet_scale/1"
        return d


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= AGREE_TOL * max(a, b)


def _distinct(ratios: List[float]) -> List[float]:
    out: List[float] = []
    for r in ratios:
        if not any(_agree(r, o) for o in out):
            out.append(r)
    return out


def sheet_scale(sheet) -> SheetScale:
    """Scale for one ingested sheet (``Sheet`` or its JSON dict)."""
    notes = find_notes(sheet)
    bars = find_scale_bars(sheet)
    dims = find_dimensions(sheet)
    ev = [asdict(e) for e in notes + bars + dims]
    warnings: List[str] = []
    px_per_pt = _px_per_pt(sheet)

    note_ratios = [e.ratio for e in notes if e.ratio]
    nts = any(e.nts for e in notes)
    distinct_notes = _distinct(note_ratios)
    dim_ratios = [e.ratio for e in dims]
    dim_med = statistics.median(dim_ratios) if dim_ratios else None
    dim_agree = [r for r in dim_ratios if dim_med and _agree(r, dim_med)]

    def done(ratio, source, conf, review, reason, views=()):
        mpt = ratio * PT_M if ratio else None
        return SheetScale(
            ratio=round(ratio, 6) if ratio else None,
            m_per_pt=mpt,
            m_per_px=(mpt / px_per_pt) if (mpt and px_per_pt) else None,
            source=source,
            confidence=round(conf, 3),
            needs_review=review,
            nts=nts,
            reason=reason,
            views=list(views),
            evidence=ev,
            warnings=warnings,
        )

    if len(distinct_notes) > 1:
        views = [{"ratio": e.ratio, "bbox": e.bbox, "text": e.text} for e in notes if e.ratio]
        return done(
            None,
            "note",
            0.0,
            False,
            f"{len(distinct_notes)} different scales on the sheet; use the per-view scale",
            views,
        )

    if distinct_notes:
        primary, source, conf = distinct_notes[0], "note", 0.9
    elif bars:
        primary, source, conf = bars[0].ratio, "bar", 0.85
    elif len(dim_agree) >= MIN_DIMENSIONS and len(dim_agree) >= 0.6 * len(dim_ratios):
        primary = statistics.median(dim_agree)
        source, conf = "dimensions", 0.8 * len(dim_agree) / len(dim_ratios)
    elif nts:
        return done(None, None, 0.0, False, "sheet is marked not to scale; no areas from it")
    else:
        why = "no scale note, scale bar, or enough consistent dimensions found"
        if dim_ratios:
            why += f" ({len(dim_ratios)} dimension(s), {len(dim_agree)} consistent)"
        return done(None, None, 0.0, True, why)

    review = False
    checks = []
    if source != "bar":
        for b in bars:
            checks.append(("scale bar", b.ratio))
    if source != "dimensions" and dim_med is not None and len(dim_agree) >= 2:
        checks.append((f"{len(dim_agree)} dimensions", statistics.median(dim_agree)))
    for name, r in checks:
        if _agree(r, primary):
            conf = min(0.99, conf + 0.05)
        else:
            review = True
            warnings.append(
                f"{source} gives 1:{primary:.4g} but {name} give 1:{r:.4g} "
                f"({abs(r - primary) / primary:.1%} apart)"
            )
    if nts:
        review = True
        warnings.append("sheet carries both a scale and a NOT TO SCALE mark")
    return done(primary, source, conf, review, f"from {source}")


def scale_at(scale: SheetScale, x: float, y: float) -> Optional[float]:
    """``m_per_pt`` at a sheet point: the sheet scale, or on a multi-view sheet
    the scale note nearest below the point (view titles sit under views)."""
    if scale.m_per_pt:
        return scale.m_per_pt
    best, best_d = None, None
    for v in scale.views:
        b = v["bbox"]
        cx, cy = _centre(b)
        # notes below the point (larger y) are cheap, notes above it cost 4x
        d = abs(cx - x) + (cy - y if cy >= y else 4 * (y - cy))
        if best_d is None or d < best_d:
            best, best_d = v["ratio"], d
    return best * PT_M if best else None


def scale_sheets(sheet_dir) -> Dict[str, dict]:
    """Scale every ``sheet_NNN.json`` in an ingest folder; writes ``scale.json``."""
    d = Path(sheet_dir)
    out = {}
    for p in sorted(d.glob("sheet_*.json")):
        out[p.name] = sheet_scale(json.loads(p.read_text())).to_dict()
    (d / "scale.json").write_text(json.dumps(out, indent=2))
    return out
