"""End-to-end demo: generate data, train per-task deciders, evaluate.

Usage:  python3 -m review_classifier.run_demo [--n-per-task 1500] [--seed 20260919]

Writes reliability.png and metrics.json into review_classifier/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split

from review_classifier import data as data_mod
from review_classifier.evaluate import expected_calibration_error, plot_reliability
from review_classifier.model import TypedDecider

HERE = Path(__file__).resolve().parent
TASKS = ["route_to_review", "schedule_match", "extraction_type"]


def _labels_to_int(examples) -> np.ndarray:
    classes = sorted({e.label for e in examples}, key=repr)
    mapping = {c: i for i, c in enumerate(classes)}
    return np.array([mapping[e.label] for e in examples]), mapping


def run(n_per_task: int = 1500, seed: int = 20260919, cv: int = 5) -> dict:
    metrics: dict = {}
    reliability_inputs: dict = {}
    for i, task in enumerate(TASKS):
        examples = data_mod.generate(task, n_per_task, seed=seed + 100 * i)
        y_int, mapping = _labels_to_int(examples)
        inv = {v: k for k, v in mapping.items()}
        # Re-label examples with integer ids so proba columns line up.
        for e, yi in zip(examples, y_int):
            e.label = int(yi)
        train, test = train_test_split(examples, test_size=0.2, random_state=seed, stratify=y_int)
        decider = TypedDecider(cv=cv, seed=seed).fit(train)

        X_test = decider.featurizer.transform(test)
        y_test = np.array([e.label for e in test])
        proba_cal = decider._calibrated.predict_proba(X_test)

        uncal = decider.fit_uncalibrated(train)
        proba_raw = uncal.predict_proba(decider.featurizer.transform(test))

        y_pred = proba_cal.argmax(axis=1)
        avg = "binary" if len(mapping) == 2 else "macro"
        acc = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, average=avg, zero_division=0)
        ece_cal = expected_calibration_error(y_test, proba_cal)
        ece_raw = expected_calibration_error(y_test, proba_raw)
        metrics[task] = {
            "n_train": len(train),
            "n_test": len(test),
            "classes": [repr(inv[j]) for j in range(len(mapping))],
            "accuracy": round(float(acc), 4),
            "f1": round(float(f1), 4),
            "ece_uncalibrated": round(float(ece_raw), 4),
            "ece_calibrated": round(float(ece_cal), 4),
        }
        reliability_inputs[f"{task}\nuncalibrated"] = (y_test, proba_raw)
        reliability_inputs[f"{task}\ncalibrated"] = (y_test, proba_cal)

        # Show off the typed API on one test example.
        d = decider.decide(test[0])
        print(f"[{task}] example decision: label={d.label!r} confidence={d.confidence:.3f}")
    plot_reliability(reliability_inputs, str(HERE / "reliability.png"))
    with open(HERE / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-task", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--cv", type=int, default=5)
    args = ap.parse_args()
    metrics = run(n_per_task=args.n_per_task, seed=args.seed, cv=args.cv)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
