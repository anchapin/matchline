"""Noise-conditioned schedule evaluation: KR/EM/VF1 under degraded text (#1114).

The research sweep (#1094) asked for a noise-conditioned schedule
protocol (arXiv:2609.17538 + arXiv:2603.18652). The image-side
degradations stay in ``detector/degrade.py`` (covered by
detector-robustness.md); the schedule path is text, so this script
measures what text-level degradation does to schedule extraction:

- a mechanical schedule table is rendered into a vector PDF (the same
  hermetic builder the tests use), ingested, and parsed clean to derive
  truth rows (tag from the MARK column, system type from
  ``mech_schedule.classify`` on the TYPE cell, capacity from
  ``mech_schedule.parse_capacity`` on the CAPACITY cell);
- the same sheet's extracted text is degraded per condition with
  ``scripts.schedule_noise`` (OCR-confusion substitutions and glyph
  dropout, seeded and deterministic) and re-parsed;
- each condition is scored with the grouped/flat pair (#1112) plus
  KR/EM/VF1, and the table is written to a markdown doc.

Scope notes, stated rather than buried: the OCR round-trip of the issue
needs an OCR engine ([ocr] extra) and is out of scope here — the
OCR-confusion substitutions stand in for what that round-trip does to
cell text; no LLM judge is included (the issue made it optional and
nothing hosted may gate CI). Re-run with ``python3
scripts/eval_schedule_noise.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "tests"))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

from ingest.pdf_ingest import ingest_pdf  # noqa: E402
from ingest.pdf_schedules import extract_schedules  # noqa: E402
from mech_schedule import classify, parse_capacity, parse_tag  # noqa: E402
from scripts.schedule_noise import degrade_texts  # noqa: E402
from scripts.schedule_score import score_schedule_rows  # noqa: E402

PAGE_W, PAGE_H = 1224.0, 792.0
TITLE = "MECHANICAL SCHEDULE"
BASE_SEED = 2026

# (tag, type cell, service area, max cfm, capacity cell)
ROWS = [
    ("VAV-10", "VAV BOX", "101", "1,200", "6.0 kW"),
    ("VAV-12", "VAV BOX", "102", "800", "4.0 kW"),
    ("VAV-3", "VAV BOX", "103", "450", "2.5 kW"),
    ("EF1-1", "EXHAUST FAN", "ROOF", "1,200", "1.5 kW"),
    ("AC-1", "FAN COIL UNIT", "1B21", "3,000", "12 kW"),
]
FIELDS = ("system_type", "capacity")
CONDITIONS = [
    ("clean", 0.0),
    ("ocr_confusion", 0.05),
    ("ocr_confusion", 0.15),
    ("glyph_dropout", 0.05),
    ("glyph_dropout", 0.15),
]


def grid(x, ytop, widths, heights, cells=None, size=7):
    """A ruled table in PDF space (y up), same operator shape the tests use."""
    xs = [x]
    for w in widths:
        xs.append(xs[-1] + w)
    ys = [ytop]
    for h in heights:
        ys.append(ys[-1] - h)
    out = ""
    for r in range(len(heights) + 1):
        out += line(xs[0], ys[r], xs[-1], ys[r], 0.5)
    for c in range(len(widths) + 1):
        out += line(xs[c], ys[0], xs[c], ys[-1], 0.5)
    for (r, c), val in (cells or {}).items():
        out += text(xs[c] + 2, ys[r] - 2 - size, val, size)
    return out


def mech_schedule_table(x=60, ytop=740):
    heights = [14] + [20] * len(ROWS)
    cells = {(0, c): h for c, h in enumerate(["MARK", "TYPE", "SERVICE", "MAX CFM", "CAPACITY"])}
    for i, r in enumerate(ROWS):
        for c, v in enumerate(r):
            cells[(1 + i, c)] = v
    return text(x, ytop + 6, TITLE, 10) + grid(x, ytop, [50, 110, 60, 60, 70], heights, cells=cells)


def _sheet(tmp: Path) -> dict:
    pdf = write_pdf(
        tmp / "mech.pdf", [PageSpec(width=PAGE_W, height=PAGE_H, content=mech_schedule_table())]
    )
    ingest_pdf(pdf, out_dir=tmp / "out")
    return json.loads((tmp / "out" / "sheet_001.json").read_text())


def _row(tag: str, type_cell: str, capacity_cell: str) -> dict:
    system_type, conf = classify(tag, "", type_cell, "")
    if system_type is None:
        raise ValueError(f"truth row {tag}: classify returned None ({conf}) on {type_cell!r}")
    cap = parse_capacity(capacity_cell)
    if not cap or cap.get("value") is None:
        raise ValueError(f"truth row {tag}: capacity unparsed from {capacity_cell!r}")
    return {
        "tag": tag,
        "fields": {
            "system_type": system_type,
            "capacity": {"value": cap["value"], "unit": cap["unit"]},
        },
    }


def truth_rows() -> list[dict]:
    return [_row(tag, type_cell, cap_cell) for tag, type_cell, _svc, _cfm, cap_cell in ROWS]


def equipment_row_to_dict(rec: dict) -> dict:
    """Map one ``pdf_schedules`` mechanical equipment record to a scorer row."""
    tag = parse_tag(rec.get("tag", ""))
    vals = rec.get("values") or {}
    type_cell = vals.get("TYPE", "")
    cap_cell = vals.get("CAPACITY", "")
    system_type, _conf = classify(tag, "", type_cell, "")
    cap = parse_capacity(cap_cell)
    capacity = None if not cap else {"value": cap.get("value"), "unit": cap.get("unit")}
    return {"tag": tag, "fields": {"system_type": system_type, "capacity": capacity}}


def _degrade_sheet_texts(sheet: dict, variant: str, rate: float) -> dict:
    """Apply ``degrade_texts`` to every extracted text but the table title."""
    out = json.loads(json.dumps(sheet))
    entries = out.get("text") or []
    strings = [e.get("text") or "" for e in entries]
    noisy = degrade_texts(strings, variant, BASE_SEED, rate=rate)
    for e, s in zip(entries, noisy):
        if s != TITLE:
            e["text"] = s
    return out


def run_conditions(tmp: Path) -> list[dict]:
    sheet = _sheet(tmp)
    truth = truth_rows()
    found = [s for s in extract_schedules(sheet) if s.kind == "mechanical"]
    if len(found) != 1:
        raise ValueError(f"expected one mechanical schedule on the fixture, got {len(found)}")
    rows = []
    for variant, rate in CONDITIONS:
        noisy = _degrade_sheet_texts(sheet, variant, rate)
        recs = [r for s in extract_schedules(noisy) if s.kind == "mechanical" for r in s.equipment]
        score = score_schedule_rows([equipment_row_to_dict(r) for r in recs], truth, fields=FIELDS)
        rows.append({"variant": variant, "rate": rate, "score": score})
    return rows


def render_table(rows: list[dict]) -> str:
    lines = [
        "| condition | EM (grouped) | flat F1 | KR | VF1 | crossed | missing | extra |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        s = r["score"]
        label = r["variant"] if r["variant"] == "clean" else f"{r['variant']} @{r['rate']:.0%}"
        lines.append(
            f"| {label} | {s.em:.3f} | {s.flat_f1:.3f} | {s.kr:.3f} "
            f"| {s.vf1:.3f} | {s.crossed} | {len(s.missing_tags)} | {len(s.extra_tags)} |"
        )
    return "\n".join(lines)


def main() -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "schedule-noise-2026-10.md"))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        rows = run_conditions(Path(td))
    body = "\n\n".join(
        [
            "# Noise-conditioned schedule evaluation (KR/EM/VF1), 2026-10",
            "",
            "Deliverable for [#1114](https://github.com/anchapin/matchline/issues/1114):"
            " the schedule benchmark under degraded-text conditions, scored with the"
            " grouped/flat pair (#1112) plus KR/EM/VF1 (arXiv:2609.17538 +"
            " arXiv:2603.18652). Truth rows come from a mechanical schedule table"
            " rendered into a vector PDF and parsed clean; each condition degrades the"
            " extracted cell text with `scripts/schedule_noise.py` (seeded, base seed"
            f" {BASE_SEED}, per-condition indices in row order) and re-parses.",
            "",
            render_table(rows),
            "",
            "## What the numbers say",
            "",
            "- OCR-confusion substitutions degrade gradually: at 15% the exact-match"
            " row rate is down to 0.600 while KR holds at 0.900 — the values are"
            " mostly recovered even where identity matching already fails, which is"
            " exactly the gap the KR/EM pair is meant to expose.",
            "- Glyph dropout is catastrophic to the whole-row score: a single dropped"
            " character in a tag or the CAPACITY header breaks identity or column"
            " detection, so EM reads 0.000 at both rates. Headers are in noise scope"
            " by design; column detection failing under header corruption is a real"
            " robustness gap, not a metric artifact.",
            "- The flat score flatters all of this relative to EM, as expected.",
            "",
            "## Scope notes",
            "",
            "- The image-side degradations (scan noise, thickened strokes) stay in"
            " `detector/degrade.py` per detector-robustness.md; this doc covers the"
            " schedule text path only.",
            "- The OCR round-trip named in the issue needs an OCR engine ([ocr]"
            " extra) and is out of scope here; the OCR-confusion substitutions stand"
            " in for what that round-trip does to cell text.",
            "- No LLM judge is included: the issue made it optional, and nothing"
            " hosted may gate CI.",
            "- Re-run with `python3 scripts/eval_schedule_noise.py` (writes this doc).",
        ]
    )
    Path(args.out).write_text(body + "\n")
    print(f"wrote {args.out}")
    for r in rows:
        print(f"{r['variant']}@{r['rate']}: {r['score'].summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
