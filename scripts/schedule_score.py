"""Grouped-vs-flat scoring for mechanical schedule extraction (#1112).

The benchmark harness (#748) scores building-level quantities (counts,
areas, terminal room assignment); it has never scored schedule rows. This
module adds the row-level metric pair the 2026-10 research sweep proposed
(KIEval, arXiv:2503.05488), so that when a schedule-evaluation path lands
in the harness both scores exist:

- **grouped** (whole-row credit): a truth row scores only when the
  prediction carries the same tag AND every compared field agrees.
- **flat** (field-level credit): over the same tag-matched pairs, each
  field is credited independently, so a row with three right fields and
  one wrong one earns partial credit. Flat flatters crossed rows; the gap
  between the two scores is the row-integrity error the flat score hides.

Identity is the equipment tag (exact match), which mech_schedule parses
from the component name (``parse_tag``). Matching is intentionally strict:
duplicate or missing tags are reported, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

DEFAULT_FIELDS: Tuple[str, ...] = ("system_type", "service_area", "capacity")


def mech_row_to_dict(row: Any) -> Dict[str, Any]:
    """Adapt a ``mech_schedule.MechScheduleRow`` to the scorer's dict form."""
    cap = row.capacity
    capacity = None if cap is None else {"value": cap.get("value"), "unit": cap.get("unit")}
    return {
        "tag": row.tag,
        "fields": {
            "system_type": row.system_type,
            "service_area": row.service_area,
            "capacity": capacity,
        },
    }


def _field_eq(a: Any, b: Any) -> bool:
    return a == b


@dataclass
class ScheduleScore:
    """Row-level schedule scores, flat and grouped, plus the match audit.

    Attributes:
        grouped: fully-correct matched rows / truth rows.
        flat_f1: per-field F1 over tag-matched pairs (missing fields
            count as misses on both sides of the pair).
        flat_precision, flat_recall: the components of ``flat_f1``.
        matched: number of tag-matched pairs.
        crossed: matched pairs where some fields agree but not all; the
            flat-vs-grouped gap lives here.
        missing_tags: truth tags with no prediction.
        extra_tags: prediction tags with no truth row.
        duplicate_tags: tags appearing more than once on either side
            (reported, excluded from matching: identity must be unique
            for a grouped score to mean anything).
    """

    grouped: float
    flat_precision: float
    flat_recall: float
    flat_f1: float
    matched: int
    crossed: int
    missing_tags: List[str] = field(default_factory=list)
    extra_tags: List[str] = field(default_factory=list)
    duplicate_tags: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"grouped {self.grouped:.3f} | flat F1 {self.flat_f1:.3f} "
            f"(P {self.flat_precision:.3f} / R {self.flat_recall:.3f}) | "
            f"matched {self.matched}, crossed {self.crossed}, "
            f"missing {len(self.missing_tags)}, extra {len(self.extra_tags)}"
            + (f", duplicate tags {self.duplicate_tags}" if self.duplicate_tags else "")
        )


def _dupes(tags: Sequence[str]) -> List[str]:
    seen: Dict[str, int] = {}
    for t in tags:
        seen[t] = seen.get(t, 0) + 1
    return sorted(t for t, n in seen.items() if n > 1)


def score_schedule_rows(
    pred_rows: Iterable[Dict[str, Any]],
    truth_rows: Iterable[Dict[str, Any]],
    fields: Sequence[str] = DEFAULT_FIELDS,
) -> ScheduleScore:
    """Score predicted schedule rows against truth rows.

    Each row is ``{"tag": str, "fields": {name: value}}``; build these from
    ``mech_schedule.MechScheduleRow`` with :func:`mech_row_to_dict`, or pass
    plain dicts from fixtures. ``capacity`` fields compare as
    ``{"value", "unit"}`` pairs (the raw string is presentation, not data).
    """
    pred = list(pred_rows)
    truth = list(truth_rows)
    dupes = sorted(set(_dupes([r["tag"] for r in pred])) | set(_dupes([r["tag"] for r in truth])))
    truth_by_tag = {r["tag"]: r for r in truth if r["tag"] not in dupes}
    pred_by_tag = {r["tag"]: r for r in pred if r["tag"] not in dupes}

    matched_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    crossed = 0
    tp = fn = 0
    fp = 0
    fully_correct = 0
    for tag, trow in truth_by_tag.items():
        prow = pred_by_tag.get(tag)
        if prow is None:
            fn += len(fields)
            continue
        matched_pairs.append((trow, prow))
        agreements = [_field_eq(trow["fields"].get(f), prow["fields"].get(f)) for f in fields]
        if all(agreements):
            fully_correct += 1
        elif any(agreements):
            crossed += 1
        tp += sum(agreements)
        fn += sum(1 for a in agreements if not a)
    pred_tags = set(pred_by_tag)
    truth_tags = set(truth_by_tag)
    fp += len(pred_tags - truth_tags) * len(fields)

    total_truth = len(truth_by_tag)
    grouped = (fully_correct / total_truth) if total_truth else (1.0 if not pred else 0.0)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision is None or recall is None or (precision + recall) == 0:
        flat_f1 = 1.0 if (total_truth == 0 and not pred) else 0.0
    else:
        flat_f1 = 2 * precision * recall / (precision + recall)
    return ScheduleScore(
        grouped=grouped,
        flat_precision=precision if precision is not None else flat_f1,
        flat_recall=recall if recall is not None else flat_f1,
        flat_f1=flat_f1,
        matched=len(matched_pairs),
        crossed=crossed,
        missing_tags=sorted(truth_tags - pred_tags),
        extra_tags=sorted(pred_tags - truth_tags),
        duplicate_tags=dupes,
    )
