"""Tests for detector/train_scratch.py: ScratchLoss, decode, NMS (#743).

Regression context: run 38311804719 died eight seconds into Train on CUDA
with "indices should be either on cpu or on the same device as the indexed
tensor" -- ScratchLoss built its dedupe mask on preds' device while the
target-derived indices stayed on CPU (targets arrive on CPU from the
dataloader). ScratchLoss.forward now moves each target onto preds' device.
The CUDA-only mixed-device path is covered by the cuda-gated test at the
bottom; CI (CPU-only, no torch in the pytest env) runs the rest where torch
is present and the runtime loss-device smoke step in detector-train.yml
covers the GPU path on the runner itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DETECTOR = Path(__file__).resolve().parents[1] / "detector"
sys.path.insert(0, str(DETECTOR))

try:
    import torch
    from train_scratch import ScratchLoss, ciou_loss, decode, nms  # noqa: E402

    HAS_TORCH = True
except ImportError:
    # the CI pytest env does not install torch (see requirements-detector.txt);
    # the tests below must still COLLECT there so EXPECTED_TEST_COUNT stays
    # honest, then skip at runtime
    HAS_TORCH = False

pytestmark = pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")


def _preds(b=2, num_classes=2, grid=8, seed=0):
    g = torch.Generator().manual_seed(seed)
    return torch.randn(b, num_classes + 5, grid, grid, generator=g)


def _targets():
    mixed = torch.tensor(
        [
            [0.0, 0.25, 0.25, 0.10, 0.10],
            [1.0, 0.70, 0.40, 0.15, 0.20],
            [0.0, 0.50, 0.80, 0.10, 0.05],
        ]
    )
    empty = torch.zeros((0, 5))
    return [mixed, empty]


def test_loss_runs_finite_and_parts():
    loss, parts = ScratchLoss(num_classes=2)(_preds(), _targets())
    assert loss.dim() == 0
    assert torch.isfinite(loss)
    assert set(parts) == {"obj", "cls", "box"}
    assert all(isinstance(v, float) for v in parts.values())
    assert all(v >= 0.0 for v in parts.values())


def test_loss_all_empty_targets_only_penalizes_negatives():
    # with no positives there is nothing to match, but the hard-negative
    # subsample still trains objectness, so obj stays > 0 while cls/box are 0
    loss, parts = ScratchLoss(num_classes=2)(_preds(), [torch.zeros((0, 5))] * 2)
    assert torch.isfinite(loss)
    assert parts["cls"] == 0.0
    assert parts["box"] == 0.0
    assert parts["obj"] > 0.0


def test_loss_dedupes_duplicate_cells_and_backprops():
    preds = _preds(b=1, seed=3).requires_grad_(True)
    dup = torch.tensor(
        [
            [0.0, 0.101, 0.205, 0.10, 0.10],
            [1.0, 0.103, 0.207, 0.12, 0.12],
        ]
    )
    loss, _ = ScratchLoss(num_classes=2)(preds, [dup])
    assert torch.isfinite(loss)
    loss.backward()
    assert preds.grad is not None
    assert torch.isfinite(preds.grad).all()


def test_ciou_loss_identical_boxes_is_near_zero():
    box = torch.tensor([[0.3, 0.4, 0.2, 0.25]])
    assert float(ciou_loss(box, box.clone())) < 1e-6


def _preds_matching(box_target, cell=(4, 4), grid=8, num_classes=2, obj=6.0):
    """Raw preds whose decoded box equals (cx, cy, w, h) image-normalized."""
    preds = torch.zeros(1, num_classes + 5, grid, grid)
    i, j = cell
    preds[0, 0, i, j] = obj  # door class logit
    preds[0, 2, i, j] = obj  # objectness logit
    # (tx, ty) raw so sigmoid gives 0.5 -> cx = cy = (4 + 0.5) / 8 = 0.5625
    # (w, h) linear image-normalized, passed straight through
    preds[0, 3, i, j] = 0.0
    preds[0, 4, i, j] = 0.0
    preds[0, 5, i, j] = box_target[2]
    preds[0, 6, i, j] = box_target[3]
    return preds


def test_loss_matches_single_cell_box():
    # cx = cy = (4 + 0.5) / 8 = 0.5625 for grid 8, cell (4, 4)
    target = torch.tensor([[0.0, 0.5625, 0.5625, 0.10, 0.15]])
    _, parts = ScratchLoss(num_classes=2)(_preds_matching([0.0, 0.5625, 0.10, 0.15]), [target])
    assert parts["box"] < 1e-4


def test_loss_matches_multicell_box():
    # regression for run 38317139729: every sheet box spans 1.2-5.3 grid
    # cells, which the old sigmoid-on-w/h parametrization could not represent
    target = torch.tensor([[0.0, 0.5625, 0.5625, 0.30, 0.35]])
    _, parts = ScratchLoss(num_classes=2)(_preds_matching([0.0, 0.5625, 0.30, 0.35]), [target])
    assert parts["box"] < 1e-4


def test_decode_rows_are_normalized_and_conf_filtered():
    preds = _preds(b=1, num_classes=2, grid=8, seed=7)
    rows = decode(preds, num_classes=2, conf=0.25)[0]
    for cls, score, cx, cy, w, h in rows:
        assert cls in (0, 1)
        assert 0.0 <= score <= 1.0
        assert all(0.0 <= v <= 1.0 for v in (cx, cy, w, h))


def test_nms_keeps_non_overlapping():
    rows = [
        (0, 0.9, 0.5, 0.5, 0.2, 0.2),
        (0, 0.8, 0.51, 0.51, 0.2, 0.2),  # heavily overlaps the first
        (1, 0.7, 0.1, 0.1, 0.1, 0.1),
    ]
    kept = nms(rows, iou_thresh=0.5)
    assert [r[1] for r in kept] == [0.9, 0.7]


@pytest.mark.skipif(not HAS_TORCH or not torch.cuda.is_available(), reason="CUDA device required")
def test_loss_accepts_cpu_targets_with_cuda_preds():
    loss, parts = ScratchLoss(num_classes=2)(_preds().cuda(), [t for t in _targets()])
    assert torch.isfinite(loss)
    assert loss.device.type == "cuda"
    assert all(isinstance(v, float) for v in parts.values())
