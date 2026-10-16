"""Match lines, split sheets, and enlarged-plan deduplication (#994).

A split sheet covers part of a level and hands off to its neighbour at a
match line; an enlarged plan re-draws a region its base plan already covers.
Both mean a physical space can be taken off twice, or split in half across
two sheets, unless the set builder knows which sheets share content. This
module pairs match lines across sheets, links enlarged plans to their base
plan through the sheet index and a content-overlap check, and returns the
takeoff exclusions the set builder applies (an enlarged plan's area is
already on its base plan, and a partial plan's area is already on a full
plan for the same level).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sheet_index import _spans, parse_sheet_number

SCHEMA = "matchline.matchlines/1"

# "MATCH LINE A", "MATCHLINE 3", "MATCH LINE - AREA B", "MATCH LINE - PLAN C".
# The whole span must be a match-line label: a project name that merely
# contains the words ("MATCHLINE TEST CLINIC" in a title block) is not one,
# and neither is "BEYOND MATCH LINE" or "MATCHING LINEWORK". A keyless label
# is recorded but never paired: pairing needs the key both halves share.
_MATCH_LINE_RE = re.compile(
    r"^\s*MATCH\s*[-\s]?\s*LINE\s*"
    r"(?:[-.:]\s*)?(?:(?:AREA|PLAN)\s+)?([A-Za-z0-9.-]{1,6})?"
    r"(?:\s*\([^)]*\))?\s*$",
    re.IGNORECASE,
)


@dataclass
class MatchLine:
    """One match-line label found on a sheet."""

    file: str
    key: str  # the label's key, "" when the label carries none
    text: str  # normalized label text
    bbox: Tuple[float, float, float, float]  # sheet pt bbox


@dataclass
class DedupReport:
    """What the set builder needs to take every space off exactly once."""

    pairs: List[dict] = field(default_factory=list)
    unpaired: List[dict] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    links: List[dict] = field(default_factory=list)
    exclusions: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, **asdict(self)}


def find_match_lines(sheet, file: str) -> List[MatchLine]:
    """Every match-line label on ``sheet``, with its key when it has one."""
    out: List[MatchLine] = []
    for sp in _spans(sheet):
        m = _MATCH_LINE_RE.match(sp.text.strip())
        if not m:
            continue
        out.append(MatchLine(file=file, key=(m.group(1) or "").upper(), text=sp.text, bbox=sp.bbox))
    return out


def pair_match_lines(
    hits: Dict[str, List[MatchLine]], level_of: Dict[str, Optional[str]]
) -> DedupReport:
    """Group match-line labels by key and level, pairing edges not file sets.

    A match-line label is one edge of a split plan. A key on exactly two
    edges from two different sheets of one level is a split pair. A key on
    three or more sheets of one level where every sheet carries it once is
    a conflict (each space would be taken off more than once). When a
    sheet carries the same key on several edges (a three-sheet strip whose
    middle sheet repeats the key), edges pair across distinct sheets and
    any edge left without a counterpart is reported unpaired instead of
    vanishing into a file-set count (#1042). Labels carry no edge
    position, so pairing is by availability, not by which side of the
    sheet the label sits on; a key pairing the same two sheets on more
    than one edge is flagged for review, since a second shared edge and a
    repeated label are indistinguishable there. A key on one sheet only is
    unpaired: its neighbour sheet is missing from the set, so the region
    past the match line may not be taken off at all.
    """
    rep = DedupReport()
    groups: Dict[Tuple[str, Optional[str]], List[MatchLine]] = {}
    for file, lines in hits.items():
        for ln in lines:
            if not ln.key:
                rep.unpaired.append(
                    {"file": file, "key": "", "reason": "match line label carries no key"}
                )
                continue
            groups.setdefault((ln.key, level_of.get(file)), []).append(ln)
    for (key, level), group in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        files = sorted({ln.file for ln in group})
        if len(files) == 1:
            rep.unpaired.append(
                {
                    "file": files[0],
                    "key": key,
                    "level": level,
                    "reason": f"match line {key} has no counterpart sheet in the set",
                }
            )
            continue
        by_file: Dict[str, int] = {}
        for ln in group:
            by_file[ln.file] = by_file.get(ln.file, 0) + 1
        if len(files) > 2 and all(n == 1 for n in by_file.values()):
            rep.conflicts.append(
                {
                    "key": key,
                    "level": level,
                    "files": files,
                    "reason": f"match line {key} appears on {len(files)} sheets of one level",
                }
            )
            continue
        # Edge accounting (#1042): pair edges across distinct sheets, largest
        # count first, so a middle sheet's second edge pairs with its other
        # neighbour instead of disappearing into a file-set count.
        remaining = dict(by_file)
        seen_pairs: set = set()
        dup_edges = 0
        while True:
            avail = sorted(
                (f for f, n in remaining.items() if n > 0), key=lambda f: (-remaining[f], f)
            )
            if len(avail) < 2:
                break
            a, b = avail[0], avail[1]
            remaining[a] -= 1
            remaining[b] -= 1
            pair_key = (a, b) if a < b else (b, a)
            if pair_key in seen_pairs:
                dup_edges += 1
                continue
            seen_pairs.add(pair_key)
            rep.pairs.append({"key": key, "level": level, "files": [pair_key[0], pair_key[1]]})
        for f, n in sorted(remaining.items()):
            if n > 0:
                rep.unpaired.append(
                    {
                        "file": f,
                        "key": key,
                        "level": level,
                        "reason": (
                            f"match line {key} appears {by_file[f]} times on this sheet "
                            f"but only {by_file[f] - n} edge(s) have a counterpart sheet"
                        ),
                    }
                )
        if dup_edges:
            rep.conflicts.append(
                {
                    "key": key,
                    "level": level,
                    "files": files,
                    "reason": (
                        f"match line {key} pairs the same sheets on more than one edge; "
                        "a second shared edge and a repeated label are indistinguishable "
                        "without label positions"
                    ),
                }
            )
    return rep


def _geometry_bbox(res: dict) -> Optional[Tuple[float, float, float, float]]:
    """Real-meter bbox of a wall result's rooms and walls; None when empty."""
    pts: List[Tuple[float, float]] = []
    for r in res.get("rooms", []):
        for x, y in r.get("polygon_m", []):
            pts.append((x, y))
    for w in res.get("walls", []):
        pts.append(tuple(w["a_m"]))
        pts.append(tuple(w["b_m"]))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_area(b: Tuple[float, float, float, float]) -> float:
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _containment(
    inside: Tuple[float, float, float, float], outer: Tuple[float, float, float, float]
) -> float:
    """How much of ``inside`` lies within ``outer``, as a bbox overlap share."""
    if _bbox_area(inside) <= 0:
        return 0.0
    x0, y0 = max(inside[0], outer[0]), max(inside[1], outer[1])
    x1, y1 = min(inside[2], outer[2]), min(inside[3], outer[3])
    inter = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
    return min(inter / _bbox_area(inside), 1.0)


def link_enlarged_plans(
    entries: Dict[str, dict],
    walls: Dict[str, dict],
    scales: Dict[str, dict],
    sheets_dir,
    *,
    min_containment: float = 0.8,
    max_area_ratio: float = 0.6,
) -> DedupReport:
    """Match each enlarged plan to its base plan and record the exclusion.

    A title that names another sheet (``ENLARGED PLAN - AREA A (A-101)``)
    links directly. Without a title reference, the enlarged plan's own wall
    geometry is extracted at its scale and compared against every full plan:
    geometry mostly inside a much larger plan's extent is a crop of it. A
    plan whose content sits inside two bases equally is ambiguous; neither
    scale nor geometry leaves the link to review.
    """
    from plan_walls import extract_walls

    rep = DedupReport()
    # Canonical sheet numbers on both sides so a title reference links
    # regardless of the case the title block or the title text was drawn in
    # (#1042); parse_sheet_number normalises through its own uppercasing.
    by_number: Dict[str, str] = {}
    for f, e in entries.items():
        raw = (e.get("number") or {}).get("value")
        if raw:
            parsed = parse_sheet_number(raw)
            by_number[parsed["number"] if parsed else raw] = f
    plans = {
        f: e
        for f, e in entries.items()
        if (e.get("type") or {}).get("value") in ("floor_plan", "plan") and f in walls
    }
    for f, e in sorted(entries.items()):
        if (e.get("type") or {}).get("value") != "enlarged_plan":
            continue
        title = (e.get("title") or {}).get("value") or ""
        base = None
        basis = "none"
        for tok in re.findall(r"[A-Za-z]{1,2}-\d{1,3}(?:\.\d{1,3})?[A-Za-z]?", title):
            parsed = parse_sheet_number(tok)
            canon = parsed["number"] if parsed else tok
            if parsed and canon in by_number and by_number[canon] != f:
                cand = by_number[canon]
                if cand in plans:
                    base, basis = cand, "title_reference"
                    break
        containment = area_ratio = None
        if base is None:
            sc = scales.get(f, {})
            if not sc.get("m_per_pt"):
                rep.links.append(
                    {
                        "file": f,
                        "base": None,
                        "basis": "none",
                        "reason": "no title reference and no scale to compare content with",
                    }
                )
                continue
            sheet = json.loads((Path(sheets_dir) / f).read_text())
            res = extract_walls(sheet, sc["m_per_pt"])
            inner = _geometry_bbox(res.to_dict())
            if inner is None:
                rep.links.append(
                    {
                        "file": f,
                        "base": None,
                        "basis": "none",
                        "reason": "no title reference and no wall geometry to compare",
                    }
                )
                continue
            cands = []
            for pf, pe in sorted(plans.items()):
                outer = _geometry_bbox(walls.get(pf, {}))
                if outer is None:
                    continue
                c = _containment(inner, outer)
                if c >= min_containment and _bbox_area(inner) <= max_area_ratio * _bbox_area(outer):
                    cands.append((pf, round(c, 3), round(_bbox_area(outer), 2)))
            if len(cands) == 1:
                base, basis, containment = cands[0][0], "content_overlap", cands[0][1]
            elif len(cands) > 1:
                best = min(c[2] for c in cands)
                near = [c for c in cands if c[2] <= best * 1.1]
                if len(near) == 1:
                    base, basis, containment = near[0][0], "content_overlap", near[0][1]
                else:
                    rep.links.append(
                        {
                            "file": f,
                            "base": None,
                            "basis": "ambiguous",
                            "candidates": sorted(c[0] for c in cands),
                            "reason": "content fits more than one base plan; pick the base sheet",
                        }
                    )
                    continue
            else:
                rep.links.append(
                    {
                        "file": f,
                        "base": None,
                        "basis": "none",
                        "reason": "no base plan contains this enlarged sheet's content",
                    }
                )
                continue
            if base and area_ratio is None:
                outer = _geometry_bbox(walls[base])
                area_ratio = round(_bbox_area(inner) / _bbox_area(outer), 3) if outer else None
        extra = {
            k: v
            for k, v in (("containment", containment), ("area_ratio", area_ratio))
            if v is not None
        }
        rep.links.append({"file": f, "base": base, "basis": basis, **extra})
        if base:
            rep.exclusions.append(
                {
                    "file": f,
                    "kind": "enlarged_plan",
                    "reason": "enlarged plan: its area is already on the base plan",
                    "linked_to": base,
                    "basis": basis,
                }
            )
    return rep


def takeoff_exclusions(entries: Dict[str, dict]) -> List[dict]:
    """Record each partial plan a full plan for the same level already covers."""
    full = {
        (
            (e.get("discipline") or {}).get("value") if e.get("discipline") else None,
            (e.get("level") or {}).get("value") if e.get("level") else None,
        )
        for e in entries.values()
        if (e.get("type") or {}).get("value") in ("floor_plan", "plan")
    }
    out = []
    for f, e in sorted(entries.items()):
        if (e.get("type") or {}).get("value") != "partial_plan":
            continue
        key = (
            (e.get("discipline") or {}).get("value") if e.get("discipline") else None,
            (e.get("level") or {}).get("value") if e.get("level") else None,
        )
        if key in full:
            out.append(
                {
                    "file": f,
                    "kind": "partial_plan",
                    "reason": "partial plan: a full plan for this level exists",
                }
            )
    return out
