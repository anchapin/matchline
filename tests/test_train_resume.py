"""Resumable train-state save/load (#1116 ROCm crash recovery).

train_scratch.save_train_state / load_train_state round-trip on CPU: the
CI pytest env does not install torch, so these skip there like the other
scratch tests (test_scratch_loss.py); the GPU path is exercised on the
runner by detector-train.yml itself.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pytest

DETECTOR = Path(__file__).resolve().parents[1] / "detector"
sys.path.insert(0, str(DETECTOR))

try:
    import torch
    from train_scratch import load_train_state, save_train_state  # noqa: E402

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

pytestmark = pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.lin = torch.nn.Linear(4, 3)


def _fresh_opt_sched(model, lr=0.1):
    opt = torch.optim.SGD(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.StepLR(opt, step_size=1)
    return opt, sched


def test_round_trip_restores_model_opt_sched_and_position(tmp_path):
    torch.manual_seed(0)
    m = Tiny()
    opt, sched = _fresh_opt_sched(m)
    sched.step()  # lr 0.1 -> 0.01 (StepLR default gamma 0.1)
    save_train_state(
        tmp_path / "ckpt_last.pt",
        m,
        opt,
        sched,
        epoch=7,
        best_f1=0.42,
        history=[{"epoch": 7, "mean_f1": 0.42}],
        cfg={"k": 1},
    )
    weights_saved = m.lin.weight.detach().clone()

    m2 = Tiny()
    opt2, sched2 = _fresh_opt_sched(m2, lr=0.9)
    state = load_train_state(tmp_path / "ckpt_last.pt", m2, opt2, sched2)

    assert state["epoch"] == 7
    assert state["best_f1"] == 0.42
    assert state["history"] == [{"epoch": 7, "mean_f1": 0.42}]
    assert torch.allclose(m2.lin.weight, weights_saved)
    assert opt2.param_groups[0]["lr"] == pytest.approx(0.01)
    assert sched2.last_epoch == 1


def test_rng_states_resume_the_stream(tmp_path):
    torch.manual_seed(1)
    np.random.seed(1)
    random.seed(1)
    m = Tiny()
    opt, sched = _fresh_opt_sched(m)
    save_train_state(tmp_path / "ckpt_last.pt", m, opt, sched, 0, -1.0, [], {})

    # Advance every RNG past the saved state, capture, then dirty again.
    drawn_t = [torch.rand(1).item() for _ in range(3)]
    drawn_np = [float(np.random.rand()) for _ in range(3)]
    drawn_py = [random.random() for _ in range(3)]
    torch.manual_seed(999)
    np.random.seed(999)
    random.seed(999)

    m2 = Tiny()
    opt2, sched2 = _fresh_opt_sched(m2)
    load_train_state(tmp_path / "ckpt_last.pt", m2, opt2, sched2)

    assert [torch.rand(1).item() for _ in range(3)] == drawn_t
    assert [float(np.random.rand()) for _ in range(3)] == drawn_np
    assert [random.random() for _ in range(3)] == drawn_py
