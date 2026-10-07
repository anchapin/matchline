"""Sheet index for an ingested drawing set (#738).

Reads each sheet's title block (sheet number, title, revision, date) and the
cover sheet's drawing index, then classifies every sheet by discipline (from the
sheet-number prefix, US National CAD Standard), type (floor plan, reflected
ceiling plan, elevation, ...) and level, and pairs plans across disciplines by
level (A-101 <-> M-101). Every field carries provenance: which text box it came
from, or which rule produced it.

Enlarged plans never feed takeoff (their area is already on a floor plan).
Partial plans are flagged: stitching them at match lines is a follow-up.

Input is a sheet from ``pdf_ingest`` (the ``Sheet`` object or its JSON dict).
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = "matchline.sheet_index/1"

Box = Tuple[float, float, float, float]

# US National CAD Standard discipline designators (first letter).
DISCIPLINES = {
    "G": "general",
    "H": "hazardous materials",
    "V": "survey/mapping",
    "B": "geotechnical",
    "C": "civil",
    "L": "landscape",
    "S": "structural",
    "A": "architectural",
    "I": "interiors",
    "Q": "equipment",
    "F": "fire protection",
    "P": "plumbing",
    "D": "process",
    "M": "mechanical",
    "E": "electrical",
    "W": "distributed energy",
    "T": "telecommunications",
    "R": "resource",
    "X": "other disciplines",
    "Z": "contractor/shop drawings",
    "O": "operations",
}

# NCS sheet-type digit (first digit of the sheet sequence).
NCS_TYPE_DIGIT = {
    "0": "general",
    "1": "plan",
    "2": "elevation",
    "3": "section",
    "4": "enlarged_plan",
    "5": "detail",
    "6": "schedule",
    "9": "3d",
}

PLAN_TYPES = {"floor_plan", "partial_plan", "enlarged_plan", "rcp", "roof_plan", "plan"}

SHEET_NUMBER_RE = re.compile(r"^([A-Z]{1,2})[-.\s]?(\d{1,3}(?:\.\d{1,3})?)([A-Z]?)$")

# Order matters: the first match wins.
_TYPE_RULES: Sequence[Tuple[str, re.Pattern]] = [
    ("cover", re.compile(r"\b(COVER\s*SHEET|TITLE\s*SHEET|COVER)\b")),
    ("rcp", re.compile(r"\b(REFLECTED\s+CEILING\s+PLAN|RCP|CEILING\s+PLAN)\b")),
    ("enlarged_plan", re.compile(r"\b(ENLARGED|BLOW[- ]?UP)\b.*\bPLANS?\b")),
    (
        "partial_plan",
        re.compile(r"\bPARTIAL\b.*\bPLANS?\b|\bPLANS?\b.*\b(AREA|PART|SECTOR)\s+[A-Z0-9]\b"),
    ),
    ("roof_plan", re.compile(r"\bROOF\s+PLAN\b")),
    ("site_plan", re.compile(r"\bSITE\s+PLAN\b")),
    ("schedule", re.compile(r"\bSCHEDULES?\b")),
    ("elevation", re.compile(r"\bELEVATIONS?\b")),
    ("section", re.compile(r"\b(BUILDING|WALL)?\s*SECTIONS?\b")),
    ("detail", re.compile(r"\bDETAILS?\b")),
    ("diagram", re.compile(r"\b(DIAGRAMS?|RISERS?)\b")),
    ("general", re.compile(r"\b(LEGENDS?|SYMBOLS|ABBREVIATIONS|GENERAL\s+NOTES|NOTES)\b")),
    ("floor_plan", re.compile(r"\b(FLOOR\s+PLAN|PLAN)S?\b")),
]

_ORDINALS = {
    "GROUND": 1,
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "FOURTH": 4,
    "FIFTH": 5,
    "SIXTH": 6,
    "SEVENTH": 7,
    "EIGHTH": 8,
    "NINTH": 9,
    "TENTH": 10,
}
_STOREY = r"(?:FLOOR|LEVEL|STORY|STOREY)"
_LEVEL_RULES: Sequence[Tuple[re.Pattern, object]] = [
    (re.compile(r"\b(?:BASEMENT|CELLAR)\s*(\d)?\b"), lambda m: f"B{m.group(1) or 1}"),
    (re.compile(r"\bLOWER\s+LEVEL\b"), "B1"),
    (re.compile(r"\b(?:LEVEL|FLOOR|LVL)\s*[-#]?\s*B(\d)\b"), lambda m: f"B{m.group(1)}"),
    (re.compile(r"\bMEZZANINE\b"), "MEZZ"),
    (re.compile(r"\bPENTHOUSE\b"), "PH"),
    (
        re.compile(r"\b(" + "|".join(_ORDINALS) + r")\s+" + _STOREY + r"\b"),
        lambda m: f"L{_ORDINALS[m.group(1)]}",
    ),
    (
        re.compile(r"\b(\d{1,2})(?:ST|ND|RD|TH)\s+" + _STOREY + r"\b"),
        lambda m: f"L{int(m.group(1))}",
    ),
    (re.compile(r"\b(?:LEVEL|FLOOR|LVL)\s*[-#]?\s*(\d{1,2})\b"), lambda m: f"L{int(m.group(1))}"),
    (re.compile(r"\bROOF\b"), "ROOF"),
]

_DATE_RE = re.compile(
    r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s+\d{1,2},?\s+\d{4})\b"
)
_REV_RE = re.compile(r"\bREV(?:ISION)?(?:\.|:|#|\s)\s*(?:NO\.?\s*)?[:#]?\s*([A-Z0-9]{1,3})\b")

INDEX_HEADING_RE = re.compile(r"\b(DRAWING|SHEET)\s+(INDEX|LIST)\b|\bLIST\s+OF\s+DRAWINGS\b")
MIN_INDEX_ROWS = 3
TITLE_MATCH_RATIO = 0.85


# ------------------------------------------------------------------------ text


@dataclass
class Span:
    text: str
    bbox: Box

    @property
    def h(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def _norm(s: str) -> str:
    s = (
        s.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return re.sub(r"\s+", " ", s).strip().upper()


def _spans(sheet) -> List[Span]:
    items = sheet["text"] if isinstance(sheet, dict) else sheet.text
    out = []
    for t in items:
        txt, box = (t["text"], t["bbox"]) if isinstance(t, dict) else (t.text, t.bbox)
        if txt and txt.strip():
            out.append(Span(_norm(txt), tuple(float(v) for v in box)))
    return out


def _attr(sheet, name):
    return sheet.get(name) if isinstance(sheet, dict) else getattr(sheet, name)


def _prov(value, source: str, span: Optional[Span] = None, **extra) -> dict:
    d = {"value": value, "source": source}
    if span is not None:
        d["text"] = span.text
        d["bbox"] = [round(v, 2) for v in span.bbox]
    d.update(extra)
    return d


def _union(spans: Sequence[Span]) -> Box:
    return (
        min(s.bbox[0] for s in spans),
        min(s.bbox[1] for s in spans),
        max(s.bbox[2] for s in spans),
        max(s.bbox[3] for s in spans),
    )


# ---------------------------------------------------------------- sheet number


def parse_sheet_number(s: str) -> Optional[dict]:
    """``A-101`` -> discipline, NCS type digit, sequence; None if not a sheet number."""
    m = SHEET_NUMBER_RE.match(_norm(s))
    if not m:
        return None
    prefix, digits, suffix = m.groups()
    letter = prefix[0]
    if letter not in DISCIPLINES:
        return None
    seq = digits.replace(".", "")
    type_digit = digits[0] if (len(seq) >= 3 or "." in digits) else None
    return {
        "number": f"{prefix}-{digits}{suffix}",
        "prefix": prefix,
        "discipline": letter,
        "sequence": seq + suffix,
        "type_digit": type_digit,
    }


def _in_title_region(sp: Span, w: float, h: float) -> bool:
    """Title blocks run down the right edge or across the bottom."""
    return sp.bbox[0] >= 0.6 * w or sp.bbox[1] >= 0.75 * h


def _corner_dist(sp: Span, w: float, h: float) -> float:
    return ((w - sp.cx) ** 2 + (h - sp.cy) ** 2) ** 0.5 / max((w * w + h * h) ** 0.5, 1)


def find_sheet_number(spans: List[Span], w: float, h: float) -> Optional[Span]:
    cands = [s for s in spans if parse_sheet_number(s.text) and _in_title_region(s, w, h)]
    if not cands:
        return None
    # title-block numbers are the biggest text in the block; ties go to the corner
    return max(cands, key=lambda s: (round(s.h, 1), -_corner_dist(s, w, h)))


# ------------------------------------------------------------------- title


def _lines(spans: List[Span]) -> List[List[Span]]:
    """Group stacked spans of similar size into multi-line titles."""
    groups: List[List[Span]] = []
    for sp in sorted(spans, key=lambda s: (s.bbox[1], s.bbox[0])):
        for g in groups:
            last = g[-1]
            same_size = abs(sp.h - last.h) <= 0.25 * max(sp.h, last.h)
            gap = sp.bbox[1] - last.bbox[3]
            aligned = abs(sp.bbox[0] - last.bbox[0]) <= 2 * last.h or (
                sp.bbox[0] < last.bbox[2] and sp.bbox[2] > last.bbox[0]
            )
            if same_size and aligned and -0.2 * last.h <= gap <= 1.0 * last.h:
                g.append(sp)
                break
        else:
            groups.append([sp])
    return groups


def classify_title(title: str) -> Optional[str]:
    t = _norm(title)
    for name, rx in _TYPE_RULES:
        if rx.search(t):
            return name
    return None


def parse_level(title: str) -> Optional[str]:
    t = _norm(title)
    for rx, val in _LEVEL_RULES:
        m = rx.search(t)
        if m:
            return val(m) if callable(val) else val
    return None


def find_title(spans: List[Span], number: Optional[Span], w: float, h: float) -> Optional[dict]:
    region = [s for s in spans if s is not number and _in_title_region(s, w, h)]
    region = [s for s in region if not parse_sheet_number(s.text)]
    best = None
    for g in _lines(region):
        txt = " ".join(s.text for s in g)
        if not classify_title(txt) and not parse_level(txt):
            continue
        hgt = max(s.h for s in g)
        near = 0.0
        if number is not None:
            near = -(((g[0].cx - number.cx) ** 2 + (g[0].cy - number.cy) ** 2) ** 0.5)
        key = (round(hgt, 1), near)
        if best is None or key > best[0]:
            best = (key, txt, g)
    if best is None:
        return None
    _, txt, g = best
    return {"text": txt, "bbox": [round(v, 2) for v in _union(g)]}


def _find_label(
    spans: List[Span], rx: re.Pattern, w: float, h: float
) -> Optional[Tuple[str, Span]]:
    for s in sorted(spans, key=lambda s: _corner_dist(s, w, h)):
        if not _in_title_region(s, w, h):
            continue
        m = rx.search(s.text)
        if m:
            return m.group(1), s
    return None


# ------------------------------------------------------------- drawing index


def find_drawing_index(spans: List[Span], exclude: Optional[Span] = None) -> Dict[str, dict]:
    """Rows of ``<sheet number>  <title>`` on one sheet; empty unless 3+ rows."""
    rows: Dict[str, dict] = {}
    nums = [s for s in spans if s is not exclude and parse_sheet_number(s.text)]
    for n in nums:
        right = [
            s
            for s in spans
            if s is not n
            and s.bbox[0] > n.bbox[2]
            and abs(s.cy - n.cy) <= 0.6 * max(n.h, s.h)
            and s.bbox[0] - n.bbox[2] < 40 * max(n.h, 1)
            and not parse_sheet_number(s.text)
            and not _DATE_RE.fullmatch(s.text)
        ]
        if not right:
            continue
        right.sort(key=lambda s: s.bbox[0])
        title = " ".join(s.text for s in right)
        num = parse_sheet_number(n.text)["number"]
        rows[num] = {"title": title, "bbox": [round(v, 2) for v in _union([n] + right)]}
    has_heading = any(INDEX_HEADING_RE.search(s.text) for s in spans)
    if len(rows) < MIN_INDEX_ROWS and not (has_heading and rows):
        return {}
    return rows


# ------------------------------------------------------------------ results


@dataclass
class SheetEntry:
    file: Optional[str]
    page: Optional[int]
    number: Optional[dict] = None
    title: Optional[dict] = None
    discipline: Optional[dict] = None
    type: Optional[dict] = None
    level: Optional[dict] = None
    revision: Optional[dict] = None
    date: Optional[dict] = None
    use_for_takeoff: bool = False
    takeoff_note: str = ""
    needs_review: bool = False
    warnings: List[str] = field(default_factory=list)

    def value(self, name: str):
        f = getattr(self, name)
        return f["value"] if f else None


@dataclass
class SheetIndex:
    sheets: List[SheetEntry]
    drawing_index: Dict[str, dict]
    levels: Dict[str, Dict[str, List[str]]]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "sheets": [asdict(s) for s in self.sheets],
            "drawing_index": self.drawing_index,
            "levels": self.levels,
            "warnings": self.warnings,
        }

    def by_number(self, number: str) -> Optional[SheetEntry]:
        for s in self.sheets:
            if s.value("number") == number:
                return s
        return None


def read_title_block(sheet, file: Optional[str] = None) -> Tuple[SheetEntry, Dict[str, dict]]:
    """One sheet's title-block fields, plus any drawing index printed on it."""
    w, h = float(_attr(sheet, "width_pt")), float(_attr(sheet, "height_pt"))
    spans = _spans(sheet)
    e = SheetEntry(file=file, page=_attr(sheet, "page_number"))

    num_span = find_sheet_number(spans, w, h)
    parsed = parse_sheet_number(num_span.text) if num_span else None
    if parsed:
        e.number = _prov(parsed["number"], "title_block", num_span)
        letter = parsed["discipline"]
        e.discipline = _prov(
            DISCIPLINES[letter], "sheet_number_prefix", None, designator=parsed["prefix"]
        )
    else:
        e.warnings.append("no sheet number found in the title block")
        e.needs_review = True

    t = find_title(spans, num_span, w, h)
    if t:
        e.title = {
            "value": t["text"],
            "source": "title_block",
            "text": t["text"],
            "bbox": t["bbox"],
        }
        kind = classify_title(t["text"])
        if kind:
            if kind in ("floor_plan", "plan") and parsed and parsed["type_digit"] == "4":
                e.type = _prov("enlarged_plan", "title_block+ncs_type_digit", None)
            else:
                e.type = _prov(kind, "title_block", None, text=t["text"])
        lvl = parse_level(t["text"])
        if lvl:
            e.level = _prov(lvl, "title_block", None, text=t["text"])
    if e.type is None and parsed and parsed["type_digit"] in NCS_TYPE_DIGIT:
        e.type = _prov(
            NCS_TYPE_DIGIT[parsed["type_digit"]], "ncs_type_digit", None, digit=parsed["type_digit"]
        )

    rev = _find_label(spans, _REV_RE, w, h)
    if rev:
        e.revision = _prov(rev[0], "title_block", rev[1])
    date = _find_label(spans, _DATE_RE, w, h)
    if date:
        e.date = _prov(date[0], "title_block", date[1])

    idx = find_drawing_index(spans, exclude=num_span)
    # rows of sheet numbers on a plan are usually callouts or key-plan notes; only a
    # cover/general sheet, or any sheet with an index heading, carries the drawing index
    heading = any(INDEX_HEADING_RE.search(s.text) for s in spans)
    kind_now = e.type["value"] if e.type else None
    if idx and not heading and kind_now not in (None, "cover", "general"):
        idx = {}
    for row in idx.values():
        row["page"] = e.page
    if idx and e.type is None:
        e.type = _prov("cover", "drawing_index_present", None)
    return e, idx


def _same_title(a: str, b: str) -> bool:
    """Same sheet title? Level and type must not contradict; an abbreviated title
    whose words all appear in the other one agrees."""
    la, lb = parse_level(a), parse_level(b)
    if la and lb and la != lb:
        return False
    ka, kb = classify_title(a), classify_title(b)
    if ka and kb and ka != kb:
        return False
    na, nb = re.sub(r"[^A-Z0-9 ]", " ", _norm(a)), re.sub(r"[^A-Z0-9 ]", " ", _norm(b))
    wa, wb = set(na.split()), set(nb.split())
    if wa and wb and (wa <= wb or wb <= wa):
        return True
    return difflib.SequenceMatcher(None, " ".join(na.split()), " ".join(nb.split())).ratio() >= (
        TITLE_MATCH_RATIO
    )


def build_index(sheets: Sequence, files: Optional[Sequence[str]] = None) -> SheetIndex:
    files = list(files) if files is not None else [None] * len(sheets)
    entries: List[SheetEntry] = []
    drawing_index: Dict[str, dict] = {}
    warnings: List[str] = []
    for sh, f in zip(sheets, files):
        e, idx = read_title_block(sh, f)
        entries.append(e)
        drawing_index.update(idx)

    # duplicate sheet numbers
    seen: Dict[str, List[SheetEntry]] = {}
    for e in entries:
        if e.number:
            seen.setdefault(e.value("number"), []).append(e)
    for num, es in seen.items():
        if len(es) > 1:
            pages = ", ".join(str(x.page) for x in es)
            warnings.append(f"sheet number {num} appears on pages {pages}")
            for x in es:
                x.needs_review = True
                x.warnings.append(f"duplicate sheet number {num}")

    # title block vs drawing index
    if drawing_index:
        for e in entries:
            num = e.value("number")
            if num is None or e.value("type") == "cover":
                continue
            row = drawing_index.get(num)
            if row is None:
                e.warnings.append("not listed in the drawing index")
                continue
            if e.title is None:
                e.title = {"value": row["title"], "source": "drawing_index", "bbox": row["bbox"]}
                kind, lvl = classify_title(row["title"]), parse_level(row["title"])
                if kind and (e.type is None or e.type["source"] == "ncs_type_digit"):
                    e.type = _prov(kind, "drawing_index", None, text=row["title"])
                if lvl and e.level is None:
                    e.level = _prov(lvl, "drawing_index", None, text=row["title"])
            elif _same_title(e.title["value"], row["title"]):
                lvl = parse_level(row["title"])
                if lvl and e.level is None:
                    e.level = _prov(lvl, "drawing_index", None, text=row["title"])
            else:
                msg = (
                    f"{num}: title block says '{e.title['value']}', "
                    f"drawing index says '{row['title']}'"
                )
                e.warnings.append(msg)
                e.needs_review = True
                warnings.append(msg)
        present = set(seen)
        for num in drawing_index:
            if num not in present:
                warnings.append(f"{num} is in the drawing index but not in this set")

    # a plan with no level borrows it from another discipline's sheet with the same sequence
    by_seq: Dict[str, str] = {}
    for e in entries:
        if e.number and e.level:
            seq = parse_sheet_number(e.value("number"))["sequence"]
            by_seq.setdefault(seq, e.level["value"])
    for e in entries:
        if e.number and e.level is None and e.value("type") in PLAN_TYPES:
            seq = parse_sheet_number(e.value("number"))["sequence"]
            if seq in by_seq:
                e.level = _prov(by_seq[seq], "paired_sheet_number", None, sequence=seq)

    # takeoff roles
    full_plans = {
        (e.value("discipline"), e.value("level"))
        for e in entries
        if e.value("type") in ("floor_plan", "plan") and e.level
    }
    for e in entries:
        kind = e.value("type")
        if kind in ("floor_plan", "plan"):
            if e.level:
                e.use_for_takeoff = True
            else:
                e.needs_review = True
                e.takeoff_note = "floor plan with no level"
        elif kind == "enlarged_plan":
            e.takeoff_note = "enlarged plan: its area is already on a floor plan"
        elif kind == "partial_plan":
            if (e.value("discipline"), e.value("level")) in full_plans:
                e.takeoff_note = "partial plan: a full plan for this level exists"
            else:
                e.needs_review = True
                e.takeoff_note = (
                    "partial plan: stitching partial plans at match lines is not supported yet"
                )

    levels: Dict[str, Dict[str, List[str]]] = {}
    for e in entries:
        if e.number and e.level and e.value("type") in PLAN_TYPES:
            d = levels.setdefault(e.level["value"], {})
            d.setdefault(e.discipline["designator"] if e.discipline else "?", []).append(
                e.value("number")
            )
    return SheetIndex(entries, drawing_index, dict(sorted(levels.items())), warnings)


def index_sheets(sheet_dir) -> SheetIndex:
    """Index every ``sheet_NNN.json`` in an ingest folder; writes ``sheet_index.json``."""
    d = Path(sheet_dir)
    paths = sorted(d.glob("sheet_*.json"))
    idx = build_index([json.loads(p.read_text()) for p in paths], [p.name for p in paths])
    (d / "sheet_index.json").write_text(json.dumps(idx.to_dict(), indent=2))
    return idx
