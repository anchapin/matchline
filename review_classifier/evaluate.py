"""Evaluation: accuracy/F1, expected calibration error, reliability diagrams."""

from __future__ import annotations

import numpy as np


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """ECE over the predicted probability of the predicted class."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = y_prob.argmax(axis=1)
    conf = y_prob.max(axis=1)
    correct = (y_pred == y_true).astype(float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf > lo) & (conf <= hi)
        if mask.sum() == 0:
            continue
        ece += (mask.sum() / len(y_true)) * abs(correct[mask].mean() - conf[mask].mean())
    return float(ece)


def reliability_curve(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (bin_centers, mean_confidence, empirical_accuracy) per bin."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = y_prob.argmax(axis=1)
    conf = y_prob.max(axis=1)
    correct = (y_pred == y_true).astype(float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centers, mean_conf, acc = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf > lo) & (conf <= hi)
        if mask.sum() == 0:
            continue
        centers.append((lo + hi) / 2)
        mean_conf.append(conf[mask].mean())
        acc.append(correct[mask].mean())
    return np.array(centers), np.array(mean_conf), np.array(acc)


def plot_reliability(
    results: dict[str, tuple[np.ndarray, np.ndarray]], path: str, n_bins: int = 10
) -> None:
    """Reliability diagram comparing several (y_true, y_prob) runs.

    ``results`` maps a subplot title to ``(y_true, y_prob)``.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(results)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4.5), squeeze=False)
    for ax, (title, (y_true, y_prob)) in zip(axes[0], results.items()):
        _, mean_conf, acc = reliability_curve(y_true, y_prob, n_bins=n_bins)
        ece = expected_calibration_error(y_true, y_prob, n_bins=n_bins)
        ax.plot([0, 1], [0, 1], "k--", label="perfect")
        ax.plot(mean_conf, acc, "o-", label="model")
        ax.set_xlabel("mean predicted confidence")
        ax.set_ylabel("empirical accuracy")
        ax.set_title(f"{title}\nECE={ece:.4f}")
        ax.legend(loc="upper left")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
