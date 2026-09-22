"""Synthetic labeled review-decision examples for the prototype.

Three decision tasks modeled on realistic Matchline review-queue items:

1. ``route_to_review`` (binary): given an extraction's detector confidence,
   OCR edit distance, schedule-match score, candidate count and area delta,
   should this extraction be routed to human review?
2. ``schedule_match`` (binary): does this schedule row match this door/window tag?
3. ``extraction_type`` (4-way choice): classify an extraction into
   {door, window, room_label, fixture} from its textual description.

Labels come from a known latent scoring rule plus logistic noise, so the
dataset has a deliberate mix of easy cases and genuinely ambiguous ones near
the decision boundary. Fixed seeds make it reproducible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Example:
    task: str  # "route_to_review" | "schedule_match" | "extraction_type"
    text: str  # free-text rendering of the observation
    numeric: dict = field(default_factory=dict)
    label: object = None  # bool for binary tasks, str for extraction_type


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _gen_route_to_review(rng: random.Random, n: int) -> list[Example]:
    tags = ["A-101", "D-204", "W-312", "B-007", "C-118", "E-220"]
    out: list[Example] = []
    for _ in range(n):
        easy = rng.random() < 0.55
        if easy:
            det_conf = rng.uniform(0.75, 0.99)
            ocr_edits = rng.choice([0, 0, 1])
            sched_match = rng.uniform(0.8, 1.0)
            n_cand = rng.choice([1, 1, 2])
            area_delta = rng.uniform(0.0, 5.0)
        else:  # ambiguous band around the boundary
            det_conf = rng.uniform(0.35, 0.75)
            ocr_edits = rng.randint(1, 5)
            sched_match = rng.uniform(0.3, 0.85)
            n_cand = rng.randint(1, 5)
            area_delta = rng.uniform(2.0, 25.0)
        tag = rng.choice(tags)
        quality = (
            0.9 * det_conf
            - 0.12 * ocr_edits
            + 0.5 * sched_match
            - 0.08 * n_cand
            - 0.015 * area_delta
            + rng.gauss(0, 0.09)
        )
        route = quality < 0.55
        text = (
            f"extraction tag={tag} det_conf={det_conf:.2f} ocr_edits={ocr_edits} "
            f"sched_match={sched_match:.2f} candidates={n_cand} "
            f"area_delta_pct={area_delta:.1f}"
        )
        out.append(
            Example(
                task="route_to_review",
                text=text,
                numeric={
                    "det_conf": det_conf,
                    "ocr_edits": float(ocr_edits),
                    "sched_match": sched_match,
                    "n_candidates": float(n_cand),
                    "area_delta_pct": area_delta,
                },
                label=route,
            )
        )
    return out


def _gen_schedule_match(rng: random.Random, n: int) -> list[Example]:
    cats = ["door", "window"]
    out: list[Example] = []
    for _ in range(n):
        match = rng.random() < 0.5
        if match:
            tag_sim = rng.uniform(0.85, 1.0)
            dims_agree = rng.random() < 0.9
            cat_agree = rng.random() < 0.95
            size_ratio = rng.uniform(0.9, 1.1)
        else:
            tag_sim = rng.uniform(0.2, 0.9)
            dims_agree = rng.random() < 0.35
            cat_agree = rng.random() < 0.5
            size_ratio = rng.uniform(0.5, 1.6)
        cat = rng.choice(cats)
        row_cat = cat if cat_agree else ("window" if cat == "door" else "door")
        logit = (
            4.0 * tag_sim
            + 2.0 * float(dims_agree)
            + 2.0 * float(cat_agree)
            - 3.0 * abs(size_ratio - 1.0)
            - 3.0
            + rng.gauss(0, 0.6)
        )
        label = _sigmoid(logit) > 0.5
        text = (
            f"tag {cat} row_cat={row_cat} tag_sim={tag_sim:.2f} "
            f"dims_agree={int(dims_agree)} cat_agree={int(cat_agree)} "
            f"size_ratio={size_ratio:.2f}"
        )
        out.append(
            Example(
                task="schedule_match",
                text=text,
                numeric={
                    "tag_sim": tag_sim,
                    "dims_agree": float(dims_agree),
                    "cat_agree": float(cat_agree),
                    "size_dev": abs(size_ratio - 1.0),
                },
                label=label,
            )
        )
    return out


_TYPE_VOCAB = {
    "door": ["swing arc", "door leaf", "hinge jamb", "threshold", "opening in wall"],
    "window": ["glazing", "sill head", "mullion", "fenestration", "glass pane"],
    "room_label": ["room name", "room number", "text label", "area tag", "occupancy label"],
    "fixture": ["ceiling grid", "luminaire symbol", "recessed can", "wattage tag", "light fixture"],
}
_NOISE_VOCAB = ["north arrow", "dimension string", "grid bubble", "sheet border", "scale bar"]


def _gen_needs_human(rng: random.Random, n: int) -> list[Example]:
    """Binary: does this extraction need human review?

    Decision is driven by detector confidence, OCR edit distance, and
    schedule-match score. Easy cases (det_conf > 0.8, few OCR edits,
    high schedule match) are clearly auto; borderline items are genuinely
    ambiguous.
    """
    out: list[Example] = []
    for _ in range(n):
        easy = rng.random() < 0.55
        if easy:
            det_conf = rng.uniform(0.75, 0.99)
            ocr_edits = rng.choice([0, 0, 1])
            sched_match = rng.uniform(0.8, 1.0)
        else:
            det_conf = rng.uniform(0.35, 0.75)
            ocr_edits = rng.randint(1, 5)
            sched_match = rng.uniform(0.3, 0.85)
        quality = 0.9 * det_conf - 0.12 * ocr_edits + 0.5 * sched_match + rng.gauss(0, 0.09)
        needs_human = quality < 0.55
        text = (
            f"extraction det_conf={det_conf:.2f} ocr_edits={ocr_edits} "
            f"sched_match={sched_match:.2f}"
        )
        out.append(
            Example(
                task="needs_human",
                text=text,
                numeric={
                    "det_conf": det_conf,
                    "ocr_edits": float(ocr_edits),
                    "sched_match": sched_match,
                },
                label=needs_human,
            )
        )
    return out


def _gen_urgency(rng: random.Random, n: int) -> list[Example]:
    """4-class: how critical is this extraction error (0-3)?

    0 = trivial (minor area delta, high confidence)
    1 = low (moderate area impact)
    2 = medium (noticeable takeoff inaccuracy)
    3 = critical (large area delta or very low confidence)
    """
    out: list[Example] = []
    for _ in range(n):
        det_conf = rng.uniform(0.3, 0.99)
        area_delta = rng.uniform(0.0, 30.0)
        sched_match = rng.uniform(0.3, 1.0)

        score = 1.5 * (1 - det_conf) + 0.05 * area_delta - 0.5 * sched_match + rng.gauss(0, 0.2)

        if score < -0.5:
            urgency = 0
        elif score < 0.2:
            urgency = 1
        elif score < 0.8:
            urgency = 2
        else:
            urgency = 3

        text = (
            f"extraction det_conf={det_conf:.2f} area_delta={area_delta:.1f} "
            f"sched_match={sched_match:.2f}"
        )
        out.append(
            Example(
                task="urgency",
                text=text,
                numeric={
                    "det_conf": det_conf,
                    "area_delta": area_delta,
                    "sched_match": sched_match,
                },
                label=urgency,
            )
        )
    return out


def _gen_resolution(rng: random.Random, n: int) -> list[Example]:
    """3-class choice: what should we do with this flagged item?

    accept = link looks correct, auto-accept
    drop = link looks wrong, drop it
    reassign = ambiguous, needs human review
    """
    out: list[Example] = []
    for _ in range(n):
        det_conf = rng.uniform(0.3, 0.99)
        area_delta = rng.uniform(0.0, 30.0)
        sched_match = rng.uniform(0.3, 1.0)
        ocr_edits = rng.randint(0, 5)

        # Latent quality score for the link
        quality = (
            0.8 * det_conf
            + 0.4 * sched_match
            - 0.03 * area_delta
            - 0.1 * ocr_edits
            + rng.gauss(0, 0.15)
        )

        if quality > 0.6:
            label = "accept"
        elif quality < 0.2:
            label = "drop"
        else:
            label = "reassign"

        text = (
            f"link det_conf={det_conf:.2f} area_delta={area_delta:.1f} "
            f"sched_match={sched_match:.2f} ocr_edits={ocr_edits}"
        )
        out.append(
            Example(
                task="resolution",
                text=text,
                numeric={
                    "det_conf": det_conf,
                    "area_delta": area_delta,
                    "sched_match": sched_match,
                    "ocr_edits": float(ocr_edits),
                },
                label=label,
            )
        )
    return out


def _gen_extraction_type(rng: random.Random, n: int) -> list[Example]:
    classes = list(_TYPE_VOCAB)
    out: list[Example] = []
    for _ in range(n):
        label = rng.choice(classes)
        words = rng.sample(_TYPE_VOCAB[label], k=rng.randint(2, 3))
        # 25% of the time sprinkle in a distractor phrase from another class
        if rng.random() < 0.25:
            other = rng.choice([c for c in classes if c != label])
            words += rng.sample(_TYPE_VOCAB[other], k=1)
        words += rng.sample(_NOISE_VOCAB, k=rng.randint(0, 2))
        rng.shuffle(words)
        aspect = {
            "door": rng.uniform(0.35, 0.55),
            "window": rng.uniform(0.6, 1.4),
            "room_label": rng.uniform(1.5, 4.0),
            "fixture": rng.uniform(0.8, 1.2),
        }[label]
        text = "extraction: " + "; ".join(words)
        out.append(
            Example(
                task="extraction_type",
                text=text,
                numeric={"aspect_ratio": aspect},
                label=label,
            )
        )
    return out


_GENERATORS = {
    "route_to_review": _gen_route_to_review,
    "schedule_match": _gen_schedule_match,
    "extraction_type": _gen_extraction_type,
    "needs_human": _gen_needs_human,
    "urgency": _gen_urgency,
    "resolution": _gen_resolution,
}


def generate(task: str, n: int, seed: int = 20260919) -> list[Example]:
    """Generate ``n`` labeled examples for ``task`` with a fixed seed."""
    if task not in _GENERATORS:
        raise ValueError(f"unknown task {task!r}; expected one of {sorted(_GENERATORS)}")
    return _GENERATORS[task](random.Random(seed), n)


def generate_all(n_per_task: int = 1500, seed: int = 20260919) -> list[Example]:
    """Generate the full multi-task prototype dataset."""
    out: list[Example] = []
    for i, task in enumerate(_GENERATORS):
        out.extend(generate(task, n_per_task, seed=seed + i))
    return out
