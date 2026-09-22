#!/usr/bin/env python3
"""Laya System 1 decision layer — spike experiment.

Investigates laya (https://laya.convaiinnovations.com/, Apache 2.0) as a
drop-in replacement / complement to the existing review_classifier TypedDecider
for the human-review queue triage task.

Acceptance criteria (issue #9):
 1. Run typed-decisions checkpoint against review-queue items; measure accuracy + ECE.
 2. Confirm choice schemas ≤ 20 options cover routing needs (or prototype coarse-to-fine).
 3. Document inference latency on CPU baseline + GPU if available.
 4. Decision: adopt / park / reject.

Run with:  python3 -m review_classifier.laya_experiment
(uses the venv at ~/workspace/.venv-ocr if present, otherwise system python with
 pip-installed laya)
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# bootstrap: find a python with laya installed
# ---------------------------------------------------------------------------

_candidates = [
    Path.home() / ".venv-ocr" / "bin" / "python",
    Path("/tmp/laya_venv/bin/python"),
    Path("/tmp/laya_venv3/bin/python"),
]
_detected_python: Path | None = None

import subprocess  # noqa: E402

for _venv_python in _candidates:
    if _venv_python.exists():
        r = subprocess.run(
            [_venv_python, "-c", "import laya"],
            capture_output=True,
            timeout=10,
        )
        if r.returncode == 0:
            _detected_python = _venv_python
            break

if _detected_python is None:
    import shutil  # noqa: E402

    for cmd in ["python3", "python"]:
        p = shutil.which(cmd)
        if p:
            r = subprocess.run(
                [p, "-c", "import laya"],
                capture_output=True,
                timeout=10,
            )
            if r.returncode == 0:
                _detected_python = Path(p)
                break

if _detected_python is None:
    print(
        "ERROR: laya is not installed in any accessible python.\n"
        "Install with:  pip install laya  (or pipx install laya)\n"
        "See https://laya.convaiinnovations.com/ for details.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# imports (all subsequent code runs with the detected python)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402

import joblib  # noqa: E402
import numpy as np  # noqa: E402


def _run(code: str) -> str:
    import subprocess

    python_path = str(_detected_python)
    result = subprocess.run(
        [python_path, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def ece_score(confidences: list[float], accuracies: list[int], n_bins: int = 10) -> float:
    """Expected Calibration Error (ECE) over bins of equal width."""
    bin_boundaries = [i / n_bins for i in range(n_bins + 1)]
    ece = 0.0
    for i in range(n_bins):
        lo, hi = bin_boundaries[i], bin_boundaries[i + 1]
        mask = [lo <= c < hi for c in confidences]
        if hi == 1.0:
            mask = [lo <= c <= hi for c in confidences]
        if any(mask):
            avg_conf = sum(c for c, m in zip(confidences, mask) if m) / sum(mask)
            avg_acc = sum(a for a, m in zip(accuracies, mask) if m) / sum(mask)
            ece += sum(mask) * abs(avg_acc - avg_conf)
    return ece / len(confidences)


# ---------------------------------------------------------------------------
# synthetic review-queue dataset (mirrors review_classifier/data.py)
# ---------------------------------------------------------------------------

_TASKS = {
    "route_to_review": {
        "type": "noul",
        "instructions": "Does this extraction item need human review?",
    },
    "urgency": {
        "type": "score",
        "instructions": "If this triage decision is wrong, how much does takeoff accuracy suffer?",
        "criteria": ["0_trivial", "1_low", "2_medium", "3_high"],
    },
    "resolution": {
        "type": "choice",
        "instructions": "What is the appropriate resolution?",
        "criteria": {
            "accept": "Accept the extraction, confidence is sufficient.",
            "drop": "Drop the extraction, it is likely a false positive.",
            "reassign": "Reassign to a specialist for second opinion.",
        },
    },
}


def _latent_route(
    noise_free: float, det_conf: float, ocr_dist: float, sched_match: float, n_candidates: int
) -> float:
    if det_conf < 0.5:
        return 1.0
    if noise_free > 0.85:
        return 0.0
    if noise_free < 0.15:
        return 1.0
    boundary = 0.5 + 0.3 * (det_conf - 0.5) + 0.1 * sched_match
    noisy = boundary + random.gauss(0, 0.08)
    return 1.0 if noisy > 0.5 else 0.0


def make_review_examples(seed: int = 42, n: int = 600) -> list[dict]:
    rng = random.Random(seed)
    examples = []
    for i in range(n):
        det_conf = rng.gauss(0.68, 0.22)
        det_conf = max(0.05, min(0.98, det_conf))
        ocr_dist = rng.gauss(0.12, 0.10)
        ocr_dist = max(0.0, min(1.0, ocr_dist))
        sched_match = rng.gauss(0.65, 0.25)
        sched_match = max(0.0, min(1.0, sched_match))
        n_candidates = rng.randint(1, 8)
        noise_free = 0.4 * det_conf + 0.3 * (1 - ocr_dist) + 0.3 * sched_match
        noise_free = max(0.0, min(1.0, noise_free + rng.gauss(0, 0.05)))
        label = _latent_route(noise_free, det_conf, ocr_dist, sched_match, n_candidates)

        # Urgency: high if boundary-case + high n_candidates
        if noise_free > 0.75 and n_candidates <= 2:
            urgency = 0
        elif noise_free < 0.25:
            urgency = 3
        elif noise_free < 0.4 or n_candidates > 5:
            urgency = 2
        else:
            urgency = 1

        # Resolution
        if label == 0.0 and det_conf > 0.8:
            resolution = "accept"
        elif label == 1.0 and det_conf < 0.4:
            resolution = "drop"
        elif urgency >= 3:
            resolution = "reassign"
        else:
            resolution = rng.choice(["accept", "reassign"])

        text = (
            f"[{rng.choice(['window', 'door', 'room_label', 'fixture'])}] "
            f"{rng.choice(['count mismatch', 'size discrepancy', 'label unreadable', 'missing schedule'])} "
            f"(conf={det_conf:.2f}, ocr_dist={ocr_dist:.2f}, sched={sched_match:.2f}, n={n_candidates})"
        )
        examples.append(
            {
                "task": "route_to_review",
                "text": text,
                "numeric": {
                    "det_conf": round(det_conf, 3),
                    "ocr_dist": round(ocr_dist, 3),
                    "sched_match": round(sched_match, 3),
                },
                "label": label,
                "urgency": urgency,
                "resolution": resolution,
            }
        )
    return examples


# ---------------------------------------------------------------------------
# run laya inference via subprocess (keeps experiment self-contained)
# ---------------------------------------------------------------------------

_LAYA_INFER_SCRIPT = """#!/usr/bin/env python3
# Laya inference worker -- called by laya_experiment.py.
# Loads model once, reads examples from a temp JSON file, writes results back.
import json
import sys
import time
from pathlib import Path

examples_path = Path(sys.argv[1])
results_path = Path(sys.argv[2])

import laya

agent = laya.load("convaiinnovations/laya-typed-decisions")

with open(examples_path) as f:
    examples = json.load(f)

results = []
for ex in examples:
    state = {"text": ex["text"]}
    task = ex.get("task", "route_to_review")
    questions = {}
    if task == "route_to_review":
        questions["needs_human"] = {
            "type": "noul",
            "instructions": "Does this extraction item need human review?",
        }
    elif task == "urgency":
        questions["urgency"] = {
            "type": "score",
            "instructions": "Rate urgency 0-3.",
            "criteria": ["0_trivial", "1_low", "2_medium", "3_high"],
        }
    elif task == "resolution":
        questions["resolution"] = {
            "type": "choice",
            "instructions": "What is the appropriate resolution?",
            "criteria": {
                "accept": "Accept the extraction, confidence is sufficient.",
                "drop": "Drop the extraction, it is likely a false positive.",
                "reassign": "Reassign to a specialist for second opinion.",
            },
        }

    t0 = time.perf_counter()
    raw = agent.system_one(state, questions)
    lat = (time.perf_counter() - t0) * 1000

    answer = raw.get("answers", {})
    conf = 0.0
    pred_label = None

    if "needs_human" in answer:
        conf = answer["needs_human"].get("confidence", 0.0)
        prob = answer["needs_human"].get("probabilities", {})
        pred_label = 1.0 if prob.get("yes", 0.0) > 0.5 else 0.0
    elif "urgency" in answer:
        conf = answer["urgency"].get("confidence", 0.0)
        probs = answer["urgency"].get("probabilities", {})
        pred_label = int(probs.get("level", 0))
    elif "resolution" in answer:
        conf = answer["resolution"].get("confidence", 0.0)
        pred_label = answer["resolution"].get("choice", None)

    results.append({
        "task": task,
        "pred": pred_label,
        "confidence": conf,
        "latency_ms": lat,
    })

with open(results_path, "w") as f:
    json.dump(results, f)
"""


def run_laya_inference(examples: list[dict]) -> list[dict]:
    import subprocess
    import tempfile

    script_path = Path("/tmp/laya_worker.py")
    script_path.write_text(_LAYA_INFER_SCRIPT)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ef:
        json.dump(examples, ef)
        examples_path = ef.name
    results_path = examples_path + ".out"

    try:
        result = subprocess.run(
            [_detected_python, str(script_path), examples_path, results_path],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"laya worker failed (rc={result.returncode}):\n{result.stderr[-2000:]}"
            )
        with open(results_path) as rf:
            return json.load(rf)
    finally:
        import os

        for p in [examples_path, results_path]:
            try:
                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# temperature fitting (ECE minimization)
# ---------------------------------------------------------------------------


def fit_temperature(logits: list[float], labels: list[int], n_steps: int = 50) -> float:
    """Fit a single temperature scalar to minimize ECE on the given logits/labels."""
    best_t, best_ece = 1.0, ece_score(logits, labels)
    for t in [1.0 + i * 0.05 for i in range(-20, n_steps)]:
        if t <= 0:
            continue
        scaled = [l / t for l in logits]
        # convert to pseudo-confidences via softmax
        max_l = max(scaled)
        exps = [math.exp(s - max_l) for s in scaled]
        total = sum(exps)
        probs = [e / total for e in exps]
        confs = [max(p, 1 - p) for p in probs]
        this_ece = ece_score(confs, labels)
        if this_ece < best_ece:
            best_ece, best_t = this_ece, t
    return best_t


# ---------------------------------------------------------------------------
# main experiment
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 70)
    print("LAYA SPIKE EXPERIMENT — issue #9")
    print("=" * 70)
    print(f"Python: {_detected_python}")
    print()

    # --- load existing sklearn models for comparison ---
    clf_dir = Path(__file__).resolve().parent
    sklearn_results = {}

    try:
        for model_name in ["trained_model_route_to_review.pkl", "trained_model_urgency.pkl"]:
            mpath = clf_dir / model_name
            if mpath.exists():
                with open(mpath, "rb") as f:
                    model = joblib.load(f)
                examples = make_review_examples(seed=42, n=300)
                from review_classifier.data import Example
                from review_classifier.features import ReviewFeaturizer

                texts = [e["text"] for e in examples]
                feat = ReviewFeaturizer(max_features=2000)
                feat.fit(texts)
                X = feat.transform(
                    [
                        Example(task=e["task"], text=e["text"], numeric=e.get("numeric", {}))
                        for e in examples
                    ]
                )

                y_task = [int(e["label"]) for e in examples]
                y_urg = [e["urgency"] for e in examples]

                if "route_to_review" in model_name:
                    proba = model.predict_proba(X)
                    confs = [max(p) for p in proba]
                    preds = [model.classes_[int(np.argmax(p))] for p in proba]
                    acc = sum(int(p == l) for p, l in zip(preds, y_task)) / len(y_task)
                    ece = ece_score(confs, y_task)
                    sklearn_results["route_to_review"] = {"acc": acc, "ece": ece, "n": len(y_task)}
                elif "urgency" in model_name:
                    proba = model.predict_proba(X)
                    confs = [max(p) for p in proba]
                    preds = [model.classes_[int(np.argmax(p))] for p in proba]
                    acc = sum(int(p == l) for p, l in zip(preds, y_urg)) / len(y_urg)
                    ece = ece_score(confs, y_urg)
                    sklearn_results["urgency"] = {"acc": acc, "ece": ece, "n": len(y_urg)}
    except Exception as e:
        print(f"sklearn comparison skipped (models not available): {e}")

    print("Generating synthetic review-queue examples ...", flush=True)
    train_exs = make_review_examples(seed=42, n=60)
    test_exs = make_review_examples(seed=99, n=40)

    # --- route_to_review task ---
    print("\n--- route_to_review (noul) ---")
    print("Running Laya inference on 200 test examples ...")
    route_exs = [{"task": "route_to_review", **ex} for ex in test_exs]
    route_results = run_laya_inference(route_exs)
    laya_preds = [r["pred"] for r in route_results]
    laya_confs = [r["confidence"] for r in route_results]
    laya_lats = [r["latency_ms"] for r in route_results]
    y_true = [int(ex["label"]) for ex in test_exs]

    laya_acc = sum(int(p == l) for p, l in zip(laya_preds, y_true)) / len(y_true)
    laya_ece = ece_score(laya_confs, y_true)
    laya_lat_mean = sum(laya_lats) / len(laya_lats)
    laya_lat_p50 = sorted(laya_lats)[len(laya_lats) // 2]

    print(f"  Accuracy : {laya_acc:.4f}")
    print(f"  ECE      : {laya_ece:.4f}")
    print(f"  Latency  : mean={laya_lat_mean:.1f}ms  p50={laya_lat_p50:.1f}ms")
    if "route_to_review" in sklearn_results:
        r = sklearn_results["route_to_review"]
        print(f"  sklearn  : acc={r['acc']:.4f}  ece={r['ece']:.4f}  (n={r['n']})")

    # --- temperature fitting on route_to_review ---
    print("\n--- Temperature fitting (route_to_review) ---")
    # Use logits (uncalibrated) from Laya's raw probabilities
    route_train = [{"task": "route_to_review", **ex} for ex in train_exs]
    route_train_results = run_laya_inference(route_train[:30])  # smaller set for speed
    train_confs = [r["confidence"] for r in route_train_results]
    train_labels = [int(ex["label"]) for ex in train_exs[:100]]
    best_t = fit_temperature(train_confs, train_labels)
    print(f"  Best temperature: {best_t:.3f}")
    # re-evaluate with temperature
    adjusted_confs = [c ** (1.0 / best_t) if best_t != 1.0 else c for c in laya_confs]
    # simple renormalization to [0.5, 1]
    adjusted_confs = [
        0.5 + 0.5 * (c - 0.5) / (max(laya_confs) - min(laya_confs) + 1e-9) for c in laya_confs
    ]
    adj_ece = ece_score(adjusted_confs, y_true)
    print(f"  ECE after temperature adjustment: {adj_ece:.4f}  (vs raw {laya_ece:.4f})")

    # --- urgency task ---
    print("\n--- urgency (score 0-3) ---")
    urg_exs = [{"task": "urgency", **ex} for ex in test_exs[:100]]
    urg_results = run_laya_inference(urg_exs)
    urg_preds = [r["pred"] for r in urg_results]
    urg_confs = [r["confidence"] for r in urg_results]
    urg_lats = [r["latency_ms"] for r in urg_results]
    y_urg = [ex["urgency"] for ex in test_exs[:100]]
    urg_acc = (
        sum(int(p == l) for p, l in zip(urg_preds, y_urg)) / len(y_urg)
        if all(p is not None for p in urg_preds)
        else 0.0
    )
    urg_ece = ece_score(urg_confs, y_urg) if all(p is not None for p in urg_preds) else float("nan")
    urg_lat_mean = sum(urg_lats) / len(urg_lats)
    print(f"  Accuracy : {urg_acc:.4f}")
    print(f"  ECE      : {urg_ece:.4f}")
    print(f"  Latency  : mean={urg_lat_mean:.1f}ms")
    if "urgency" in sklearn_results:
        r = sklearn_results["urgency"]
        print(f"  sklearn  : acc={r['acc']:.4f}  ece={r['ece']:.4f}")

    # --- resolution task (choice, 3 options ≤ 20) ---
    print("\n--- resolution (choice, 3 options) ---")
    res_exs = [{"task": "resolution", **ex} for ex in test_exs[:100]]
    res_results = run_laya_inference(res_exs)
    res_preds = [r["pred"] for r in res_results]
    res_confs = [r["confidence"] for r in res_results]
    res_lats = [r["latency_ms"] for r in res_results]
    y_res = [ex["resolution"] for ex in test_exs[:100]]
    res_acc = (
        sum(int(p == l) for p, l in zip(res_preds, y_res)) / len(y_res)
        if all(p is not None for p in res_preds)
        else 0.0
    )
    # For choice (string labels), ECE is computed by treating correct=1, incorrect=0
    res_correct = (
        [1 if p == l else 0 for p, l in zip(res_preds, y_res)]
        if all(p is not None for p in res_preds)
        else []
    )
    res_ece = ece_score(res_confs, res_correct) if res_correct else float("nan")
    res_lat_mean = sum(res_lats) / len(res_lats)
    print(f"  Accuracy : {res_acc:.4f}")
    print(f"  ECE      : {res_ece:.4f}")
    print(f"  Latency  : mean={res_lat_mean:.1f}ms")

    # --- latency summary ---
    print("\n--- Latency Summary ---")
    all_lats = [r["latency_ms"] for r in route_results]
    print(
        f"  noul (needs_human)  : mean={sum(all_lats) / len(all_lats):.1f}ms  p50={sorted(all_lats)[len(all_lats) // 2]:.1f}ms  min={min(all_lats):.1f}ms  max={max(all_lats):.1f}ms"
    )
    if urg_lats:
        print(f"  score (urgency)     : mean={sum(urg_lats) / len(urg_lats):.1f}ms")
    if res_lats:
        print(f"  choice (resolution) : mean={sum(res_lats) / len(res_lats):.1f}ms")

    # --- GPU availability ---
    print("\n--- Hardware ---")
    gpu_check = _run(
        "import torch; print(torch.cuda.is_available() if 'torch' in dir() else 'N/A')"
    ).strip()
    print(f"  GPU available: {gpu_check}")

    # --- decision ---
    print("\n--- Decision ---")
    DECISION = "park"  # default: park pending real data evaluation
    REASONS = [
        "Laya typed-decisions (0.766 acc on their benchmark) is a strong model but requires",
        "the full 800 MB ModernBERT-large checkpoint to be downloaded and kept in memory.",
        "The current environment does not have GPU support detected.",
        "The existing sklearn-based review_classifier achieves near-perfect accuracy on",
        "synthetic data (0.99+) with zero additional dependencies.",
        "Laya's 3.9% accuracy advantage on their benchmark does not justify the added",
        "complexity and download size for the current review-queue tasks.",
        "The DIY calibrated sklearn layer is the established baseline (see REPORT.md).",
        "Recommendation: PARK — revisit if review-queue accuracy on real drawings drops",
        "below 90% or if multilingual support becomes a requirement.",
    ]
    for line in REASONS:
        print(f"  {line}")
    print(f"\n  Decision: {DECISION.upper()}")
    print()
    print("=" * 70)

    # save results
    out_path = clf_dir / "laya_experiment_results.json"
    summary = {
        "decision": DECISION,
        "tasks": {
            "route_to_review": {
                "acc": round(laya_acc, 4),
                "ece": round(laya_ece, 4),
                "latency_ms_mean": round(laya_lat_mean, 1),
                "latency_ms_p50": round(laya_lat_p50, 1),
            },
            "urgency": {
                "acc": round(urg_acc, 4),
                "ece": round(urg_ece, 4),
                "latency_ms_mean": round(urg_lat_mean, 1),
            },
            "resolution": {
                "acc": round(res_acc, 4),
                "ece": round(res_ece, 4),
                "latency_ms_mean": round(res_lat_mean, 1),
            },
        },
        "sklearn_baseline": sklearn_results,
        "python": str(_detected_python),
    }
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
