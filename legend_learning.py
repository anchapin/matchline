"""Per-project mechanical legend tag learning (#995).

``hvac_legend.classify`` maps a legend row's description to an HVAC detector
class from its words alone, so a firm's own phrasing ("SUPPLY GRILLE", a
"CEILING OUTLET") that names no class keeps ``cls=None`` and lands in review
on every sheet. This module learns those mappings per project and stores them
in a sidecar next to the drawing set, so the next run maps them before the
detector ever sees a sheet.

Learning has two sources, both project-local:

* ``learn_from_set_report`` reads a stage set report's ``hvac_legends`` rows
  and keeps every (description, class) pair the built-in rules already
  resolved, so the project's phrasing survives a rules change. Rows the rules
  could not map are reported, not guessed.
* ``--tag DESC=CLASS`` on ``matchline learn-legend`` records a human decision
  for exactly those rows.

The sidecar is JSON (``matchline.legend_tags/1``), lives beside the drawing
set, is loaded by ``run_pipeline`` only when it is present, and is never
written into the repository: nothing here changes the built-in defaults.
``eval_before_after`` compares two set reports and flags a regression: a row
mapped before that came back unmapped, or flipped class, after the tags were
applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA = "matchline.legend_tags/1"
# the sidecar sits beside the drawing set the run reads it from
SIDECAR_NAME = "legend_tags.json"
# synth.mech.MECH_CLASSES: the classes the HVAC detector knows
CLASSES = ("vav", "ahu", "diffuser", "grille", "sensor")


class LegendTagsError(ValueError):
    """A legend-tags sidecar or tag is malformed."""


def normalize(description: str) -> str:
    """The exact key ``hvac_legend.classify`` looks a description up under."""
    return " ".join(str(description).upper().split())


def _checked_tags(raw: object, source: str) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise LegendTagsError(f"{source}: tags must be a JSON object")
    out: Dict[str, str] = {}
    for desc, cls in raw.items():
        if not isinstance(desc, str) or not isinstance(cls, str):
            raise LegendTagsError(f"{source}: tags must map strings to strings")
        if not normalize(desc):
            raise LegendTagsError(f"{source}: empty description")
        if cls not in CLASSES:
            raise LegendTagsError(
                f"{source}: {desc!r} maps to unknown class {cls!r} (known: {', '.join(CLASSES)})"
            )
        out[normalize(desc)] = cls
    return out


def load_tags(path) -> Optional[Dict[str, str]]:
    """Read a sidecar; None when absent, LegendTagsError when malformed."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise LegendTagsError(f"{p}: not valid JSON ({e})") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise LegendTagsError(f"{p}: expected schema {SCHEMA}")
    return _checked_tags(doc.get("tags"), str(p))


def save_tags(path, tags: Dict[str, str]) -> Path:
    """Write the sidecar, normalized and sorted, schema-checked like a read."""
    p = Path(path)
    clean = _checked_tags(tags, str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    from atomic_io import write_text_atomic

    write_text_atomic(
        p, json.dumps({"schema": SCHEMA, "tags": dict(sorted(clean.items()))}, indent=2) + "\n"
    )
    return p


def learn_from_set_report(report: dict) -> Tuple[Dict[str, str], List[str], List[str]]:
    """Tags from a stage set report's ``hvac_legends`` rows.

    Returns (tags, conflicts, unmapped): a description the report maps to two
    classes is a conflict and is skipped; rows the rules could not map come
    back as distinct unmapped descriptions, the ones ``--tag`` is for.
    """
    tags: Dict[str, str] = {}
    conflicts: List[str] = []
    dropped: set = set()
    unmapped: set = set()
    for leg in report.get("hvac_legends") or []:
        for row in leg.get("rows") or []:
            desc, cls = row.get("description"), row.get("class")
            if not (desc or "").strip():
                continue
            key = normalize(desc)
            if cls is None:
                unmapped.add(key)
                continue
            if cls not in CLASSES or key in dropped:
                continue
            if key in tags and tags[key] != cls:
                # two sheets read the same words differently: drop the tag
                # rather than guess, and let --tag settle it
                conflicts.append(f"{key}: {tags[key]} vs {cls}")
                del tags[key]
                dropped.add(key)
                continue
            tags[key] = cls
    return tags, sorted(set(conflicts)), sorted(unmapped)


def add_tags(tags: Dict[str, str], pairs) -> Dict[str, str]:
    """Merge ``DESC=CLASS`` pairs into tags, validating each class."""
    out = dict(tags)
    for pair in pairs or []:
        desc, _, cls = (pair or "").partition("=")
        desc, cls = desc.strip(), cls.strip().lower()
        if not desc or cls not in CLASSES:
            raise LegendTagsError(f"--tag expects DESC={('|').join(CLASSES)}, got {pair!r}")
        out[normalize(desc)] = cls
    return out


def _legend_rows(report: dict) -> List[Tuple[str, str, Optional[str]]]:
    """Every legend row of a set report as (sheet, description, class)."""
    out: List[Tuple[str, str, Optional[str]]] = []
    for leg in report.get("hvac_legends") or []:
        sheet = str(leg.get("sheet") or "")
        for row in leg.get("rows") or []:
            out.append((sheet, row.get("description") or "", row.get("class")))
    return out


def eval_before_after(before: dict, after: dict) -> dict:
    """Before/after eval of a learned-tags run against the baseline run.

    Both arguments are stage set reports (or anything holding the same
    ``hvac_legends`` rows). A regression is a row the baseline mapped that the
    tagged run leaves unmapped (``lost``) or maps to a different class
    (``flipped``); everything the tags newly map is ``newly_mapped``. Learned
    tags only ever add mappings by construction, so any entry here says a
    manual ``--tag`` overrode a rule or the rules changed underneath.
    """
    b = {(s, normalize(d)): c for s, d, c in _legend_rows(before)}
    a = {(s, normalize(d)): c for s, d, c in _legend_rows(after)}
    lost = [
        {"sheet": s, "description": d, "was": b[(s, d)]}
        for s, d in sorted(b)
        if b[(s, d)] is not None and a.get((s, d)) is None
    ]
    flipped = [
        {"sheet": s, "description": d, "was": b[(s, d)], "now": a[(s, d)]}
        for s, d in sorted(b)
        if b[(s, d)] is not None and a.get((s, d)) not in (None, b[(s, d)])
    ]
    newly = sum(1 for k, c in a.items() if c is not None and b.get(k) is None)
    before_mapped = sum(1 for c in b.values() if c is not None)
    after_mapped = sum(1 for c in a.values() if c is not None)
    return {
        "rows_before": len(b),
        "rows_after": len(a),
        "mapped_before": before_mapped,
        "mapped_after": after_mapped,
        "newly_mapped": newly,
        "lost": lost,
        "flipped": flipped,
        "regressions": len(lost) + len(flipped),
    }
