"""Average precision for the detector eval path (#1119).

Pure Python: no torch import, so the metric math is testable in the main
suite and importable from any venv. The checkpoint-level consumer is
``detector.train_scratch.evaluate``, which feeds per-detection
(confidence, matched) pairs from the same greedy IoU matcher that produces
the existing precision/recall/F1, so AP50 and F1 always describe
identical detections.

Scope note: ``detector-robustness.md`` keeps its greedy-matching stance
for the robustness harness itself; AP50 lives in the scratch eval path
only, reported alongside (not instead of) F1.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple


def average_precision(pairs: Iterable[Tuple[float, bool]], num_gt: int) -> Optional[float]:
    """All-point interpolated AP from (confidence, is_tp) pairs.

    Pairs are sorted by confidence (descending, stable). Returns None when
    the class has no ground truth (AP is undefined), 0.0 when there is
    ground truth but no predictions.
    """
    if num_gt <= 0:
        return None
    ordered = sorted(pairs, key=lambda p: -p[0])
    if not ordered:
        return 0.0
    points: List[Tuple[float, float]] = []
    tp = 0
    fp = 0
    for _conf, is_tp in ordered:
        if is_tp:
            tp += 1
        else:
            fp += 1
        points.append((tp / num_gt, tp / (tp + fp)))
    # Precision envelope: for each recall, the max precision at any
    # threshold with at least that recall (VOC 2010+ all-point form).
    env: List[Tuple[float, float]] = []
    best = 0.0
    for r, p in reversed(points):
        best = max(best, p)
        env.append((r, best))
    env.reverse()
    ap = 0.0
    prev_r = 0.0
    for r, p in env:
        if r > prev_r:
            ap += p * (r - prev_r)
            prev_r = r
    return ap


def mean_average_precision(per_class: Dict[str, dict]) -> Optional[float]:
    """mAP from an ``evaluate()``-shaped per-class dict (its ``ap50`` fields).

    Classes without ground truth (ap50 None) are excluded; when no class
    has ground truth, mAP is undefined and returns None.
    """
    vals = [
        entry["ap50"]
        for entry in per_class.values()
        if isinstance(entry, dict) and entry.get("ap50") is not None
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)
