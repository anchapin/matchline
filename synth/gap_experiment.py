#!/usr/bin/env python3
"""Sim-to-real gap experiment.

Trains the WiSARD classifier (jesse.py) on SYNTHETIC symbol crops and
evaluates on the REAL AEC-geometric-bench crops (1,626 crops, 8 classes,
via datasets_adapter.load_aec_bench), vs. a real-trained baseline.

Must run under the venv python (~/workspace/.venv-ocr/bin/python) --
load_aec_bench needs pymupdf.

Writes synth/out/gap_results.json and prints the comparison table.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

import sys as _sys
sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in _sys.path:
    _sys.path.insert(0, sys_path)

from jesse import WisardClassifier, make_tuple_indices
from datasets_adapter import load_aec_bench
from synth.symbols import make_symbol_dataset, AEC_CLASSES

AEC_ROOT = Path.home() / "workspace" / "datasets" / "aec-geometric-bench" / "dataset"
OUT = Path(__file__).resolve().parent / "out"

DOOR = {"Single Swing Door", "Double Swing Door"}
WINDOW = {"Window"}


def stratified_split(y, frac=0.8, seed=0):
    rng = np.random.default_rng(seed)
    tr, te = [], []
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        rng.shuffle(idx)
        k = int(len(idx) * frac)
        tr += idx[:k].tolist()
        te += idx[k:].tolist()
    tr = np.array(tr)
    te = np.array(te)
    rng.shuffle(tr)
    rng.shuffle(te)
    return tr, te


def door_window_acc(y_true, y_pred, classes):
    """Binary door-vs-window accuracy on the door/window subset."""
    di = [classes.index(c) for c in DOOR]
    wi = [classes.index("Window")]
    mask = np.isin(y_true, di + wi)
    yt = np.isin(y_true[mask], di).astype(int)
    yp = np.isin(y_pred[mask], di).astype(int)
    return float((yt == yp).mean()), int(mask.sum())


def main():
    t0 = time.perf_counter()
    print("loading real AEC crops ...", flush=True)
    samples, _takeoff = load_aec_bench(AEC_ROOT, dpi=200, size=28)
    X_real = np.stack([s.image for s in samples])
    y_real = np.array([AEC_CLASSES.index(s.label) for s in samples],
                      dtype=np.int64)
    print(f"real: {X_real.shape}, class counts: "
          f"{dict(zip(AEC_CLASSES, np.bincount(y_real, minlength=8)))}",
          flush=True)

    tidx = make_tuple_indices(28, 28, seed=42)

    # --- baseline: train on 80% real, test on 20% real -----------------------
    tr, te = stratified_split(y_real, 0.8, seed=0)
    clf = WisardClassifier(8, tuple_idx=tidx)
    clf.fit(X_real[tr], y_real[tr])
    pred = clf.predict_logodds(X_real[te], alpha=0.1)
    acc_real = float((pred == y_real[te]).mean())
    dw_acc_real, dw_n = door_window_acc(y_real[te], pred, AEC_CLASSES)
    print(f"baseline real->real: acc={acc_real*100:.2f}% "
          f"door-vs-window={dw_acc_real*100:.2f}% (n={dw_n})", flush=True)

    # --- gap: train on synthetic, test on ALL real ----------------------------
    print("rendering synthetic training crops ...", flush=True)
    X_syn, y_syn = make_symbol_dataset(AEC_CLASSES, n_per_class=400, seed=1)
    clf2 = WisardClassifier(8, tuple_idx=tidx)
    clf2.fit(X_syn, y_syn)
    pred2 = clf2.predict_logodds(X_real, alpha=0.1)
    acc_syn = float((pred2 == y_real).mean())
    dw_acc_syn, dw_n2 = door_window_acc(y_real, pred2, AEC_CLASSES)
    print(f"gap synth->real:     acc={acc_syn*100:.2f}% "
          f"door-vs-window={dw_acc_syn*100:.2f}% (n={dw_n2})", flush=True)

    # per-class accuracy, synthetic-trained
    per_class = {}
    for i, name in enumerate(AEC_CLASSES):
        m = y_real == i
        per_class[name] = round(float((pred2[m] == i).mean()), 4) if m.any() else None
    print("per-class synth->real:", per_class, flush=True)

    results = {
        "n_real": int(len(y_real)),
        "n_synth_train": int(len(y_syn)),
        "baseline_real_train_real_test_acc": round(acc_real, 4),
        "baseline_door_vs_window_acc": round(dw_acc_real, 4),
        "synth_train_real_test_acc": round(acc_syn, 4),
        "synth_door_vs_window_acc": round(dw_acc_syn, 4),
        "per_class_synth_to_real": per_class,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "gap_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"saved {OUT / 'gap_results.json'}", flush=True)


if __name__ == "__main__":
    main()
