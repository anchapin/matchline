#!/usr/bin/env python3
"""Independent mechanical-schedule ground truth, read straight from the COBie workbook (#1240).

Usage:
    python scripts/schedule_truth.py --workbook <path> [--out truth.json] [--force]

The schedule-truth benchmark (#1240) scores matchline's mechanical schedule
extraction against frozen independent truth, the way the IFC benchmark scores
against ``bench_truth`` (#1165). This module reads the workbook with openpyxl
only and must never import matchline's schedule reader (``mech_schedule``);
``tests/test_schedule_truth.py`` pins that. Truth records what the schedule
states, so a benchmark run measures extraction, not self-comparison.

Per building with a schedule workbook this writes ``truth.json`` next to the
workbook (outside the repo, like the IFCs):

- rows: every ``Component`` sheet row keyed by equipment tag. The tag is the
  stated ``TagNumber`` when the workbook carries one; the clinic workbook
  leaves it ``n/a`` and embeds the tag in the name, so the stated convention
  (docs/mech_schedule.md) applies: the trailing ``PREFIX-digits`` token wins.
  Rows with no tag are recorded under ``untagged`` with a reason, never
  dropped; duplicate tags are recorded under ``duplicate_tags``, first kept.
- types: the ``Type`` sheet by type name: the stated description (where the
  workbook's capacities live, recorded as stated, never converted) and the
  category (carried as provenance only; the column is unreliable).
- spaces: the ``Space`` sheet by room name (floor name, category) — the room
  list the service areas reference.
- requirements: the ``Attribute`` sheet's Component rows (name, value, unit
  as stated) grouped by component name — the schedule's stated per-equipment
  requirements. On the clinic workbook these carry the airflow requirements
  (``Total`` / ``Outside Air``, L/s).

``truth.json`` carries the workbook's sha256. The benchmark refuses a truth
file whose sha256 no longer matches the workbook, so truth cannot drift
silently; re-freeze only with ``--force`` after a confirmed workbook change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "matchline.schedule_truth/1"
TRUTH_FILENAME = "truth.json"

# The stated tag convention (docs/mech_schedule.md): the trailing
# ``PREFIX-digits`` token in the component name wins. Written here
# independently of mech_schedule's reader; the pin is that this module never
# imports it, not that the stated convention differs.
_TAG_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,5})-(\d+[A-Za-z]?)\b")

_STATED_NO = {"n/a", "na", "", "none", "-"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stated(value) -> str:
    """A cell's stated text; the workbook's ``n/a`` placeholder reads as empty."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _STATED_NO else text


def _header(ws) -> dict:
    header = {str(c.value).strip(): i for i, c in enumerate(ws[1], start=1) if c.value}
    return header


def _cell(row, header, name: str):
    idx = header.get(name)
    return row[idx - 1] if idx else None


def parse_name_tag(name: str) -> str:
    """The trailing ``PREFIX-digits`` token of a component name, else ``""``."""
    tokens = _TAG_RE.findall(name or "")
    if tokens:
        prefix, digits = tokens[-1]
        return f"{prefix}-{digits}"
    return ""


def read_components(wb) -> tuple[dict, list, list]:
    """Every Component row keyed by tag; untagged and duplicate rows reported."""
    ws = wb["Component"]
    header = _header(ws)
    rows: dict[str, dict] = {}
    untagged: list[dict] = []
    duplicates: list[dict] = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        name = _stated(_cell(row, header, "Name"))
        if not name:
            continue
        stated_tag = _stated(_cell(row, header, "TagNumber"))
        tag = stated_tag or parse_name_tag(name)
        entry = {
            "row": i,
            "name": name,
            "type_name": _stated(_cell(row, header, "TypeName")),
            "space": _stated(_cell(row, header, "Space")),
            "description": _stated(_cell(row, header, "Description")),
        }
        if not tag:
            untagged.append(
                {**entry, "reason": "no stated TagNumber and no PREFIX-digits token in the name"}
            )
        elif tag in rows:
            duplicates.append({**entry, "tag": tag, "first_row": rows[tag]["row"]})
        else:
            rows[tag] = {**entry, "tag_source": "TagNumber" if stated_tag else "name"}
    return rows, untagged, duplicates


def read_types(wb) -> dict:
    ws = wb["Type"]
    header = _header(ws)
    types: dict[str, dict] = {}
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        name = _stated(_cell(row, header, "Name"))
        if not name:
            continue
        types[name] = {
            "row": i,
            "description": _stated(_cell(row, header, "Description")),
            "category": _stated(_cell(row, header, "Category")),
        }
    return types


def read_spaces(wb) -> dict:
    ws = wb["Space"]
    header = _header(ws)
    spaces: dict[str, dict] = {}
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        name = _stated(_cell(row, header, "Name"))
        if not name:
            continue
        spaces[name] = {
            "row": i,
            "floor_name": _stated(_cell(row, header, "FloorName")),
            "category": _stated(_cell(row, header, "Category")),
        }
    return spaces


def read_requirements(wb) -> dict:
    """Attribute rows attached to components, grouped by component name."""
    if "Attribute" not in wb.sheetnames:
        return {}
    ws = wb["Attribute"]
    header = _header(ws)
    grouped: dict[str, list] = {}
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        sheet_name = _stated(_cell(row, header, "SheetName"))
        if sheet_name != "Component":
            continue
        component = _stated(_cell(row, header, "RowName"))
        name = _stated(_cell(row, header, "Name"))
        if not component or not name:
            continue
        grouped.setdefault(component, []).append(
            {
                "name": name,
                "value": _stated(_cell(row, header, "Value")),
                "unit": _stated(_cell(row, header, "Unit")),
                "row": i,
            }
        )
    return grouped


def build_truth(workbook: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        rows, untagged, duplicates = read_components(wb)
        types = read_types(wb)
        spaces = read_spaces(wb)
        requirements = read_requirements(wb)
    finally:
        wb.close()
    return {
        "schema": SCHEMA,
        "workbook": str(workbook),
        "workbook_sha256": sha256_file(workbook),
        "counts": {
            "component_rows": len(rows) + len(untagged) + len(duplicates),
            "tagged": len(rows),
            "untagged": len(untagged),
            "duplicate_tags": len(duplicates),
            "types": len(types),
            "spaces": len(spaces),
            "components_with_requirements": len(requirements),
        },
        "rows": rows,
        "untagged": untagged,
        "duplicate_tags": duplicates,
        "types": types,
        "spaces": spaces,
        "requirements": requirements,
    }


def load_or_freeze_truth(workbook: Path, force: bool = False) -> tuple[dict, str]:
    """Load the frozen truth next to ``workbook``, freezing it on first use.

    Returns ``(truth, how)`` with ``how`` in ``{"frozen", "loaded"}``. A truth
    file whose workbook sha256 no longer matches is refused: re-freeze only
    with ``force`` after a confirmed workbook change.
    """

    out = workbook.parent / TRUTH_FILENAME
    sha = sha256_file(workbook)
    if out.exists():
        truth = json.loads(out.read_text())
        if truth.get("workbook_sha256") == sha:
            return truth, "loaded"
        if not force:
            raise TruthMismatch(
                f"{out} does not match the workbook's sha256; "
                "re-freeze only after confirming the workbook changed (--force)"
            )
        truth = build_truth(workbook)
        out.write_text(json.dumps(truth, indent=1, sort_keys=True))
        return truth, "refrozen"
    truth = build_truth(workbook)
    out.write_text(json.dumps(truth, indent=1, sort_keys=True))
    return truth, "frozen"


class TruthMismatch(Exception):
    """The frozen truth file no longer matches the workbook."""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--workbook", required=True, help="COBie workbook path")
    ap.add_argument("--out", default=None, help="truth.json path (default: next to the workbook)")
    ap.add_argument("--force", action="store_true", help="re-freeze over a changed workbook")
    args = ap.parse_args(argv)
    workbook = Path(args.workbook).expanduser()
    out = Path(args.out).expanduser() if args.out else workbook.parent / TRUTH_FILENAME
    truth, how = load_or_freeze_truth(workbook, force=args.force)
    if out != workbook.parent / TRUTH_FILENAME:
        out.write_text(json.dumps(truth, indent=1, sort_keys=True))
    print(json.dumps({"out": str(out), "how": how, "counts": truth["counts"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
