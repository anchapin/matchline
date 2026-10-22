#!/usr/bin/env python3
"""Schedule-truth benchmark (#1240): score mechanical schedule extraction against frozen truth.

Usage:
    python scripts/schedule_bench.py --config scripts/benchmarks.json \
        [--building NAME] [--out scoreboard.md] [--json scoreboard.json] [--export-validity]

The IFC benchmark (#748/#1165) scores geometry against frozen independent
``truth.json``; this module scores mechanical schedule extraction the same
way (#1240). Each configured building may carry a ``schedule_workbook``: the
COBie workbook that is the project's real schedule deliverable (the clinic's
is the only one in the suite). Truth is frozen next to the workbook by
``scripts/schedule_truth.py``, which reads the workbook with openpyxl only
and never imports ``mech_schedule``, so the run measures extraction rather
than self-comparison.

Metrics, per building:

- equipment tags found: truth tags recovered (recall) and extractor tags
  that are truth tags (precision). Missed truth tags are listed with their
  type names so exclusions (receptacles, doors) stay visible instead of
  silently deflating the score. Grouped/flat/KR/EM/VF1 come from
  ``scripts/schedule_score`` over the stated fields (service area, capacity).
- capacity error: over matched tags whose type description states capacity
  tokens, the extractor's parsed capacity (value, unit) agrees with an
  independent parse of the same stated string. Ranges state an interval and
  parse to value ``None`` on both sides.
- airflow error: over matched tags with stated component requirements in an
  airflow unit, the airflow slots the extractor recovered. The clinic
  workbook states these on the ``Attribute`` sheet, which the extractor does
  not read yet; the metric reports the measured gap instead of hiding it.
- zone-linkage correctness: the extractor's service area equals the truth's
  stated Space reference, and that reference names a real Space-sheet room.
- export validity rate (with ``--export-validity``): the building's
  BuildingSync export passes XSD validation. Measured per building, never
  asserted.

Writes a markdown scoreboard plus the raw JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bem.buildingsync_export import validate_buildingsync, write_buildingsync  # noqa: E402
from mech_schedule import parse_cobie_workbook  # noqa: E402
from scripts.schedule_score import mech_row_to_dict, score_schedule_rows  # noqa: E402
from scripts.schedule_truth import TruthMismatch, load_or_freeze_truth  # noqa: E402

# Units that name airflow (the slots the value-slot reader fills, #1236) or
# that name a stated requirement on the Attribute sheet.
_AIRFLOW_UNITS = {"l/s", "lps", "cfm", "m3/s", "m3/h", "l/min"}

# Units a stated capacity token can carry (energy, power, heat, water
# flow), read independently of the extractor's unit table; the airflow
# units above stay separate. A stated ``24 Volt`` is a stated value, but
# it is not a stated capacity, so it does not enter the capacity metric.
_CAP_TOKEN_UNITS = {
    "kw", "kva", "w", "watt", "watts", "j", "kj", "mj",
    "btu", "btu/h", "mbh", "ton", "tons", "tr", "gpm",
}

# Units that name dimensions, not equipment capacities: a stated "24 mm" is
# geometry, never a schedule value.
_NOT_A_UNIT = {"x", "mm", "cm", "m", "ft", "in"}

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_VALUE_RE = re.compile(rf"({_NUMBER})(?:\s*-\s*({_NUMBER}))?\s*([A-Za-z/]+)")


@dataclass
class SchedRow:
    name: str
    schedule_workbook: Path | None = None
    arch_ifc: Path | None = None


@dataclass
class BenchRow:
    name: str
    status: str = "ok"
    detail: str | None = None
    extras: dict = field(default_factory=dict)


def stated_values(text: str) -> list[dict]:
    """Independent parse of the stated value tokens in a description string.

    Returns one entry per stated ``number [range] unit`` token: ``value``
    (``None`` for a stated range), ``unit`` as stated, ``raw``. Dimension
    units (the stated ``x``, ``mm``, ``cm``, ``m``, ``ft``, ``in``) are
    skipped: they name geometry, never a schedule value. Units are single
    tokens, so ``"4100mm Troffer"`` does not read ``Troffer`` as a unit.
    """

    out: list[dict] = []
    for m in _VALUE_RE.finditer(text or ""):
        unit = m.group(3).strip()
        if unit.lower() in _NOT_A_UNIT:
            continue
        value = None
        if m.group(2) is None:
            raw = m.group(1).replace(",", "")
            value = float(raw)
        out.append({"value": value, "unit": unit, "raw": m.group(0).strip()})
    return out


def _airflow_reqs(requirements: list[dict]) -> list[dict]:
    return [r for r in requirements if r["unit"].strip().lower() in _AIRFLOW_UNITS]


def run_schedule_bench(truth: dict, result) -> dict:
    """Score one building's parse result against its frozen schedule truth."""

    truth_rows = truth["rows"]
    types = truth["types"]
    spaces = truth["spaces"]
    requirements = truth["requirements"]

    # Unique tags only: the extractor's rows that parsed to no tag are
    # reported below, never charged against precision; its duplicate tags
    # are counted and collapsed to the first row, like truth's.
    raw_rows = [mech_row_to_dict(r) for r in result.rows]
    empty_tag_rows = sum(1 for r in raw_rows if not r["tag"])
    pred_by_tag: dict[str, dict] = {}
    pred_dup_tags = 0
    for r in raw_rows:
        tag = r["tag"]
        if not tag:
            continue
        if tag in pred_by_tag:
            pred_dup_tags += 1
            continue
        pred_by_tag[tag] = r
    pred_rows = list(pred_by_tag.values())
    truth_by_tag = dict(truth_rows)  # every stated row is truth, keyed by tag

    matched = sorted(set(truth_by_tag) & set(pred_by_tag))
    missed = sorted(set(truth_by_tag) - set(pred_by_tag))
    extra = sorted(set(pred_by_tag) - set(truth_by_tag))
    row_slots: dict[str, list] = {}
    for src in result.rows:
        if src.tag and src.tag not in row_slots:
            row_slots[src.tag] = list(src.values)

    # capacity agreement over matched tags whose type description states values
    cap_stated = cap_agree = 0
    cap_disagree: list[dict] = []
    # airflow agreement over matched tags with stated airflow requirements
    air_stated = air_agree = 0
    air_missing = 0
    # zone linkage over matched tags
    zone_ok = zone_mismatch = 0
    zone_bad_rooms = 0

    truth_dicts = []
    pred_dicts = []
    for tag in matched:
        trow = truth_by_tag[tag]
        prow = pred_by_tag[tag]
        t_cap = None
        stated = stated_values(types.get(trow["type_name"], {}).get("description", ""))
        if stated:
            first = stated[0]
            t_cap = {"value": first["value"], "unit": first["unit"]}
            cap_stated += 1
            p_cap = prow["fields"].get("capacity")
            if p_cap and _norm_pair(p_cap) == _norm_pair(t_cap):
                cap_agree += 1
            else:
                cap_disagree.append({"tag": tag, "stated": first["raw"], "pred": p_cap})
        truth_dicts.append(
            {"tag": tag, "fields": {"service_area": trow["space"], "capacity": t_cap}}
        )
        pred_dicts.append(prow)

        reqs = _airflow_reqs(requirements.get(trow["name"], []))
        if reqs:
            air_stated += len(reqs)
            slots = row_slots.get(tag, [])
            if not slots:
                air_missing += len(reqs)
            else:
                for r in reqs:
                    t_val = _num(r["value"])
                    unit = r["unit"].strip().lower()
                    for v in slots:
                        same_unit = v.unit.strip().lower() == unit
                        if (
                            v.value is not None
                            and t_val is not None
                            and same_unit
                            and abs(v.value - t_val) < 1e-9
                        ):
                            air_agree += 1
                            break

        sa = prow["fields"].get("service_area")
        if sa == trow["space"]:
            zone_ok += 1
        else:
            zone_mismatch += 1
        if trow["space"] and trow["space"] not in spaces:
            zone_bad_rooms += 1

    for tag in missed:
        trow = truth_by_tag[tag]
        truth_dicts.append(
            {"tag": tag, "fields": {"service_area": trow["space"], "capacity": None}}
        )

    score = score_schedule_rows(pred_dicts, truth_dicts, fields=("service_area", "capacity"))

    missed_sample = [
        {"tag": t, "name": truth_by_tag[t]["name"], "type_name": truth_by_tag[t]["type_name"]}
        for t in missed[:10]
    ]
    families: Counter = Counter(
        truth_by_tag[t]["type_name"].split("-")[0].split()[0] for t in missed
    )
    return {
        "counts": {
            "truth_rows": len(truth_rows),
            "pred_rows": len(result.rows),
            "pred_unique_tags": len(pred_rows),
            "empty_tag_rows": empty_tag_rows,
            "pred_duplicate_tags": pred_dup_tags,
            "truth_duplicate_tags": truth["counts"].get("duplicate_tags"),
            "matched_tags": len(matched),
            "missed_tags": len(missed),
            "extra_tags": len(extra),
            "review_items": len(result.review_items),
        },
        "tags": {
            "recall": len(matched) / len(truth_rows) if truth_rows else None,
            "precision": len(matched) / len(pred_rows) if pred_rows else None,
            "missed_sample": missed_sample,
            "missed_families_top": families.most_common(8),
        },
        "score": {
            "grouped": score.grouped,
            "flat_f1": score.flat_f1,
            "flat_precision": score.flat_precision,
            "flat_recall": score.flat_recall,
            "kr": score.kr,
            "em": score.em,
            "vf1": score.vf1,
        },
        "capacity": {
            "stated": cap_stated,
            "agree": cap_agree,
            "disagree": cap_disagree[:5],
            "disagree_count": len(cap_disagree),
        },
        "airflow": {
            "stated": air_stated,
            "agree": air_agree,
            "missing_extractor_slots": air_missing,
            "note": "stated component requirements the extractor has no slots for",
        },
        "zone_linkage": {
            "matched": zone_ok,
            "mismatch": zone_mismatch,
            "referenced_rooms_not_in_space_sheet": zone_bad_rooms,
        },
        "stats": dict(result.stats),
    }


def _num(text: str):
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _norm_pair(cap):
    if not isinstance(cap, dict):
        return None
    value = cap.get("value")
    unit = (cap.get("unit") or "").strip().lower()
    if value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    return (value, unit)


def export_validity(name: str, arch_ifc: Path | None, out_dir: Path) -> dict:
    """BuildingSync export validity for one building's model, measured."""

    if arch_ifc is None or not arch_ifc.exists():
        return {"status": "missing", "detail": "arch IFC not on disk"}
    from geometry.geometry_simplify import footprint_from_regions, simplify_ring
    from ifc.ifc_import import import_ifc
    from run_pipeline import model_from_linked_model

    try:
        model = import_ifc(str(arch_ifc))
        # The export reuses the benchmark harness's own simplification
        # (#972): the footprint ring, the storey wall height, nothing hard
        # coded.
        polys = [sp.polygon_m for sp in model.spaces.values() if getattr(sp, "polygon_m", None)]
        wall_h = getattr(model.levels[0], "wall_height_m", None) if model.levels else None
        ring = footprint_from_regions(polys)
        sres = None
        if ring and len(ring) >= 3:
            sres = simplify_ring(ring, tol=0.02, wall_height=wall_h)
        bem = model_from_linked_model(
            model=model, simplified_ring=sres, wall_height_m=wall_h, simplify_tolerance=0.02
        )
        out = out_dir / f"{name}-buildingsync" / "model.buildingsync.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_buildingsync(bem, out)
        ok, errors = validate_buildingsync(out)
        return {"status": "valid" if ok else "invalid", "errors": errors[:10]}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}


def load_config(path: Path) -> list[SchedRow]:
    data = json.loads(path.read_text())

    def p(b: dict, key: str):
        v = b.get(key)
        return Path(v).expanduser() if v else None

    return [
        SchedRow(
            name=b["name"],
            schedule_workbook=p(b, "schedule_workbook"),
            arch_ifc=p(b, "arch_ifc"),
        )
        for b in data["buildings"]
    ]


def render(rows: list[BenchRow]) -> str:
    lines = ["# Schedule-truth benchmark (#1240)", ""]
    for r in rows:
        lines.append(f"## {r.name} ({r.status})")
        if r.detail:
            lines.append(r.detail)
        e = r.extras
        if not e:
            continue
        c, t = e["counts"], e["tags"]
        lines += [
            f"- truth rows: {c['truth_rows']} tagged ({c['pred_rows']} extractor rows, "
            f"{c['review_items']} review items)",
            f"- tags found: recall {t['recall']:.3f}, precision {t['precision']:.3f} "
            f"({c['matched_tags']} matched, {c['missed_tags']} missed, {c['extra_tags']} extra)",
            f"- grouped {e['score']['grouped']:.3f} / flat F1 {e['score']['flat_f1']:.3f} / "
            f"KR {e['score']['kr']:.3f} / EM {e['score']['em']:.3f} / VF1 {e['score']['vf1']:.3f}",
            f"- capacity: {e['capacity']['agree']}/{e['capacity']['stated']} stated agree "
            f"({e['capacity']['disagree_count']} disagree)",
            f"- airflow: {e['airflow']['agree']}/{e['airflow']['stated']} stated agree, "
            f"{e['airflow']['missing_extractor_slots']} without extractor slots",
            f"- zone linkage: {e['zone_linkage']['matched']} matched, "
            f"{e['zone_linkage']['mismatch']} mismatch",
        ]
        if e.get("export_validity"):
            lines.append(f"- export validity: {e['export_validity']['status']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--config", default="scripts/benchmarks.json")
    ap.add_argument("--building", default=None, help="score one building")
    ap.add_argument("--out", default="schedule-scoreboard.md")
    ap.add_argument("--json", dest="json_out", default="schedule-scoreboard.json")
    ap.add_argument("--export-validity", action="store_true")
    args = ap.parse_args(argv)
    buildings = load_config(Path(args.config).expanduser())
    if args.building:
        buildings = [b for b in buildings if b.name == args.building]
    if not buildings:
        print("no such building", file=sys.stderr)
        return 1
    rows: list[BenchRow] = []
    json_rows: dict[str, dict] = {}
    for b in buildings:
        row = BenchRow(name=b.name)
        if b.schedule_workbook is None or not b.schedule_workbook.exists():
            row.status = "missing"
            row.detail = "no schedule workbook on disk (see DATASETS.md)"
            rows.append(row)
            continue
        try:
            truth, how = load_or_freeze_truth(b.schedule_workbook)
        except TruthMismatch as e:
            row.status = "error"
            row.detail = str(e)
            rows.append(row)
            continue
        result = parse_cobie_workbook(b.schedule_workbook)
        metrics = run_schedule_bench(truth, result)
        metrics["truth"] = how
        row.extras = metrics
        row.extras["counts"]["truth_rows"] = truth["counts"]["tagged"]
        if args.export_validity:
            row.extras["export_validity"] = export_validity(
                b.name, b.arch_ifc, Path(args.out).expanduser().parent
            )
        json_rows[b.name] = row.extras
        rows.append(row)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows))
    Path(args.json_out).expanduser().write_text(json.dumps(json_rows, indent=1))
    print(out.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
