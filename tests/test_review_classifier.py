"""Fast end-to-end test of the review-queue classifier prototype.

Trains on a tiny synthetic sample (seconds on CPU) and asserts:
- the pipeline runs end-to-end and beats a trivial baseline,
- the typed decision API (noul/choice/decide) returns valid probabilities,
- calibration does not materially worsen ECE (tolerance: +0.03).
"""

import numpy as np
import pytest

from review_classifier import data as data_mod
from review_classifier.evaluate import expected_calibration_error
from review_classifier.model import TypedDecider

TASKS = ["route_to_review", "schedule_match", "extraction_type"]


def _as_int(examples):
    classes = sorted({e.label for e in examples}, key=repr)
    mapping = {c: i for i, c in enumerate(classes)}
    for e in examples:
        e.label = mapping[e.label]
    return examples, len(classes)


@pytest.mark.parametrize("task", TASKS)
def test_end_to_end_accuracy_and_calibration(task):
    examples, _ = _as_int(data_mod.generate(task, 400, seed=123))
    train, test = examples[:320], examples[320:]
    decider = TypedDecider(cv=3, seed=123).fit(train)

    X_test = decider.featurizer.transform(test)
    y_test = np.array([e.label for e in test])
    proba_cal = decider._calibrated.predict_proba(X_test)
    acc = (proba_cal.argmax(axis=1) == y_test).mean()
    assert acc > 0.70, f"{task}: accuracy {acc:.3f} below baseline"

    uncal = decider.fit_uncalibrated(train)
    proba_raw = uncal.predict_proba(decider.featurizer.transform(test))
    ece_cal = expected_calibration_error(y_test, proba_cal)
    ece_raw = expected_calibration_error(y_test, proba_raw)
    assert ece_cal <= ece_raw + 0.03, (
        f"{task}: calibration worsened ECE {ece_raw:.4f} -> {ece_cal:.4f}"
    )


def test_typed_api_probabilities_valid():
    examples = data_mod.generate("schedule_match", 300, seed=7)
    decider = TypedDecider(cv=3, seed=7).fit(examples)
    ex = data_mod.generate("schedule_match", 1, seed=99)[0]
    p = decider.noul(ex)
    assert 0.0 <= p <= 1.0
    d = decider.decide(ex)
    assert 0.0 <= d.confidence <= 1.0
    assert abs(sum(d.probabilities.values()) - 1.0) < 1e-6

    examples2 = data_mod.generate("extraction_type", 300, seed=7)
    decider2 = TypedDecider(cv=3, seed=7).fit(examples2)
    ex2 = data_mod.generate("extraction_type", 1, seed=99)[0]
    opts = ["door", "window", "room_label", "fixture"]
    probs = decider2.choice(ex2, opts)
    assert set(probs) == set(opts)
    assert all(0.0 <= v <= 1.0 for v in probs.values())


def test_reproducible_dataset():
    a = data_mod.generate("route_to_review", 50, seed=42)
    b = data_mod.generate("route_to_review", 50, seed=42)
    assert [e.text for e in a] == [e.text for e in b]
    assert [e.label for e in a] == [e.label for e in b]
