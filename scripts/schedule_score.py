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
- **KR / EM / VF1** (#1114, noise-conditioned schedule evaluation):
  KR is key recall, identity-agnostic — the fraction of truth cells whose
  value is recovered anywhere in the prediction, so a shuffled or
  re-tagged table still shows what the extractor recovered at all. EM is
  exact-match row rate (the same number as ``grouped``, named for the
  KR/EM/VF1 protocol). VF1 is value F1 over the tag-matched pairs with
  value normalization (case and whitespace do not count as errors;
  numeric capacity compares value and unit).

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


def _norm(x: Any) -> Any:
    """Normalize a cell value for KR/VF1: strings fold case and spaces,
    numbers round, capacity compares as (value, unit)."""
    if isinstance(x, dict):
        v = x.get("value")
        return (None if v is None else round(float(v), 3), x.get("unit"))
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return round(float(x), 3)
    return " ".join(str(x).split()).casefold()


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
        kr: key recall, identity-agnostic (see module docstring).
        em: exact-match row rate; the same number as ``grouped``.
        vf1: value F1 with normalization (see module docstring).
    """

    grouped: float
    flat_precision: float
    flat_recall: float
    flat_f1: float
    matched: int
    crossed: int
    kr: float
    em: float
    vf1: float
    missing_tags: List[str] = field(default_factory=list)
    extra_tags: List[str] = field(default_factory=list)
    duplicate_tags: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"grouped {self.grouped:.3f} | flat F1 {self.flat_f1:.3f} "
            f"(P {self.flat_precision:.3f} / R {self.flat_recall:.3f}) | "
            f"KR {self.kr:.3f} | EM {self.em:.3f} | VF1 {self.vf1:.3f} | "
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
    tp_v = fn_v = fp_v = 0
    fully_correct = 0
    for tag, trow in truth_by_tag.items():
        prow = pred_by_tag.get(tag)
        if prow is None:
            fn += len(fields)
            fn_v += len(fields)
            continue
        matched_pairs.append((trow, prow))
        agreements = [_field_eq(trow["fields"].get(f), prow["fields"].get(f)) for f in fields]
        v_agreements = [
            _norm(trow["fields"].get(f)) == _norm(prow["fields"].get(f)) for f in fields
        ]
        if all(agreements):
            fully_correct += 1
        elif any(agreements):
            crossed += 1
        tp += sum(agreements)
        fn += sum(1 for a in agreements if not a)
        tp_v += sum(v_agreements)
        fn_v += sum(1 for a in v_agreements if not a)
    pred_tags = set(pred_by_tag)
    truth_tags = set(truth_by_tag)
    fp += len(pred_tags - truth_tags) * len(fields)
    fp_v += len(pred_tags - truth_tags) * len(fields)

    # KR: identity-agnostic recovery. A truth cell counts when its
    # normalized value shows up in any predicted row's same field.
    pred_cells = {
        (f, _norm(prow["fields"].get(f))) for prow in pred_by_tag.values() for f in fields
    }
    truth_cells = len(truth_by_tag) * len(fields)
    recovered = sum(
        1
        for trow in truth_by_tag.values()
        for f in fields
        if (f, _norm(trow["fields"].get(f))) in pred_cells
    )
    kr = recovered / truth_cells if truth_cells else 1.0

    total_truth = len(truth_by_tag)
    grouped = (fully_correct / total_truth) if total_truth else (1.0 if not pred else 0.0)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision is None or recall is None or (precision + recall) == 0:
        flat_f1 = 1.0 if (total_truth == 0 and not pred) else 0.0
    else:
        flat_f1 = 2 * precision * recall / (precision + recall)
    v_prec = tp_v / (tp_v + fp_v) if (tp_v + fp_v) else None
    v_rec = tp_v / (tp_v + fn_v) if (tp_v + fn_v) else None
    if v_prec is None or v_rec is None or (v_prec + v_rec) == 0:
        vf1 = 1.0 if (total_truth == 0 and not pred) else 0.0
    else:
        vf1 = 2 * v_prec * v_rec / (v_prec + v_rec)
    return ScheduleScore(
        grouped=grouped,
        flat_precision=precision if precision is not None else flat_f1,
        flat_recall=recall if recall is not None else flat_f1,
        flat_f1=flat_f1,
        matched=len(matched_pairs),
        crossed=crossed,
        kr=kr,
        em=grouped,
        vf1=vf1,
        missing_tags=sorted(truth_tags - pred_tags),
        extra_tags=sorted(pred_tags - truth_tags),
        duplicate_tags=dupes,
    )


# ---------------------------------------------------------------------------
# Confidence calibration (#1295)
# ---------------------------------------------------------------------------


@dataclass
class CalibrationBucket:
    """One (confidence, claim kind) cell of the calibration table.

    Attributes:
        confidence: The confidence number the model records for the claims
            in this bucket (0..1).
        kind: What the claims assert (e.g. ``"schedule_row_service_area"``).
        claims: How many claims the bucket holds.
        correct: How many of them the truth set confirms.
        abstained: How many facts the truth states that the extractor
            deliberately did not assert (a nulled value, an unread unit).
            An abstention is neither right nor wrong: nothing was claimed.
        unverifiable: Claims the truth set cannot score (e.g. the truth
            states a range, so a single number cannot be compared).
    """

    confidence: float
    kind: str
    claims: int = 0
    correct: int = 0
    abstained: int = 0
    unverifiable: int = 0

    @property
    def measured_correctness(self) -> float | None:
        """Measured correctness of the bucket, or ``None`` with no claims."""
        return self.correct / self.claims if self.claims else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "confidence": self.confidence,
            "kind": self.kind,
            "claims": self.claims,
            "correct": self.correct,
            "abstained": self.abstained,
            "unverifiable": self.unverifiable,
            "measured_correctness": self.measured_correctness,
        }


def calibration_table(claims: Iterable[Dict[str, Any]]) -> List[CalibrationBucket]:
    """Bucket calibration claims by confidence and claim kind.

    Each claim is a dict with ``confidence`` (float), ``kind`` (str) and
    one of:

    * ``correct: True`` / ``correct: False`` — a scored claim.
    * ``abstained: True`` — the truth states a fact and the extractor
      asserted nothing (a nulled value, an unread unit). Counted apart.
    * ``unverifiable: True`` — the truth cannot score the claim (e.g. it
      states a range where the extractor asserts a number). Counted apart.

    Buckets come back sorted by descending confidence, then by kind, so
    the table reads the same on every run.
    """
    buckets: Dict[Tuple[float, str], CalibrationBucket] = {}
    for claim in claims:
        key = (float(claim["confidence"]), str(claim["kind"]))
        bucket = buckets.setdefault(key, CalibrationBucket(confidence=key[0], kind=key[1]))
        if claim.get("abstained"):
            bucket.abstained += 1
        elif claim.get("unverifiable"):
            bucket.unverifiable += 1
        else:
            bucket.claims += 1
            if claim.get("correct"):
                bucket.correct += 1
    return [buckets[k] for k in sorted(buckets, key=lambda k: (-k[0], k[1]))]


def calibration_by_confidence(buckets: Iterable[CalibrationBucket]) -> List[Dict[str, Any]]:
    """Aggregate calibration buckets by confidence, kinds merged.

    One row per confidence value: how many claims carry it, how many the
    truth set confirms, and the measured correctness over those claims.
    """
    merged: Dict[float, Dict[str, Any]] = {}
    for b in buckets:
        row = merged.setdefault(
            b.confidence,
            {
                "confidence": b.confidence,
                "claims": 0,
                "correct": 0,
                "abstained": 0,
                "unverifiable": 0,
            },
        )
        row["claims"] += b.claims
        row["correct"] += b.correct
        row["abstained"] += b.abstained
        row["unverifiable"] += b.unverifiable
    out = []
    for conf in sorted(merged, reverse=True):
        row = merged[conf]
        row["measured_correctness"] = row["correct"] / row["claims"] if row["claims"] else None
        out.append(row)
    return out
