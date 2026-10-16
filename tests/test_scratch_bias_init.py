"""Tests for detector.train_scratch.init_box_head_bias.

Collected without torch (CI's pytest env has no torch); tests skip at
runtime when torch is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import torch

    from detector.train_scratch import ScratchNet, init_box_head_bias

    HAS_TORCH = True
except ImportError:  # pragma: no cover - exercised in the no-torch CI env
    HAS_TORCH = False

pytestmark = pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")


def _labels(tmp_path: Path, rows) -> Path:
    lbl = tmp_path / "labels" / "train"
    lbl.mkdir(parents=True)
    lines = "\n".join(f"{c} {x:.4f} {y:.4f} {w:.4f} {h:.4f}" for c, x, y, w, h in rows)
    (lbl / "sheet.txt").write_text(lines + "\n")
    (lbl / "empty.txt").write_text("")
    return tmp_path


ROWS = [(0, 0.5, 0.5, 0.04, 0.08), (1, 0.3, 0.7, 0.05, 0.06)]


def test_bias_init_matches_label_stats(tmp_path):
    root = _labels(tmp_path, ROWS)
    model = ScratchNet(num_classes=2, width=8, imgsz=320)
    before = model.head[-1].bias.detach().clone()
    stats = init_box_head_bias(model, root / "labels" / "train", num_classes=2)
    assert stats is not None
    mean_w, mean_h, n = stats
    assert n == 2
    assert mean_w == pytest.approx(0.045)
    assert mean_h == pytest.approx(0.07)
    bias = model.head[-1].bias.detach()
    # tx/ty zeroed (targets uniform in [0, 1)), w/h at the logit of the means
    assert bias[3] == pytest.approx(0.0)
    assert bias[4] == pytest.approx(0.0)
    assert bias[5] == pytest.approx(0.045)
    assert bias[6] == pytest.approx(0.07)
    # cls and obj channels untouched
    assert torch.equal(bias[:3], before[:3])


def test_initial_box_predictions_near_target_mean(tmp_path):
    root = _labels(tmp_path, ROWS)
    model = ScratchNet(num_classes=2, width=8, imgsz=320)
    init_box_head_bias(model, root / "labels" / "train", num_classes=2)
    with torch.no_grad():
        bias = model.head[-1].bias
        assert bias[5].item() == pytest.approx(0.045, abs=1e-6)
        assert bias[6].item() == pytest.approx(0.07, abs=1e-6)


def test_bias_init_no_labels_leaves_bias_alone(tmp_path):
    model = ScratchNet(num_classes=2, width=8, imgsz=320)
    before = model.head[-1].bias.detach().clone()
    stats = init_box_head_bias(model, tmp_path / "labels" / "train", num_classes=2)
    assert stats is None
    assert torch.equal(model.head[-1].bias.detach(), before)
