"""Tests for detector/eca.py and the two model configs (issue #500).

Run with the detector venv (pytest is not a training dependency):

    detector/.venv-det/bin/pip install pytest
    detector/.venv-det/bin/python -m pytest detector/tests -q

These live outside the main ``tests/`` suite because they need torch +
ultralytics, which the main project treats as an optional ``detector`` extra.
Pure-python detector modules (degrade.py, eval_robustness.py) are tested in
tests/test_detector_*.py instead.

Happy path: ECA preserves shape, gates per-channel, and the ECA config differs
from the control config only by the four inserted ECA rows.
Invariants: the initial gate is near-neutral; k stays odd; params differ by
exactly the ECA weights.
Defect injection: wrong-rank input, bad k, unalignable sequences; and a
regression test for the layer-index shift that silently defeated Ultralytics'
name-based weight loading.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eca import ECA, adaptive_ksize, register_eca, transfer_aligned  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
ECA_YAML = CONFIG_DIR / "yolo11n_eca.yaml"
BASE_YAML = CONFIG_DIR / "yolo11n_baseline.yaml"
ECA_INDICES = (3, 6, 9, 12)


def _params(model):
    return sum(p.numel() for p in model.parameters())


# --------------------------------------------------------------------------
# adaptive_ksize
# --------------------------------------------------------------------------


@pytest.mark.parametrize("c", [8, 32, 64, 128, 256, 512, 1024, 2048])
def test_adaptive_ksize_is_always_odd(c):
    """Symmetric padding needs an odd k, else the channel axis changes length."""
    k = adaptive_ksize(c)
    assert k % 2 == 1, f"k must be odd for k={c}, got {k}"
    assert k >= 1


def test_adaptive_ksize_rejects_zero_channels():
    with pytest.raises(ValueError):
        adaptive_ksize(0)


# --------------------------------------------------------------------------
# ECA block: happy path
# --------------------------------------------------------------------------


def test_forward_preserves_shape():
    m = ECA()
    x = torch.rand(2, 64, 32, 32)
    assert m(x).shape == x.shape


def test_explicit_k_size_is_honored():
    m = ECA(k_size=7)
    m(torch.rand(1, 32, 8, 8))
    assert m.conv.kernel_size[0] == 7


def test_gate_is_per_channel_not_a_global_scalar():
    """ECA's whole point is channel selectivity; a scalar gate would be a bug."""
    torch.manual_seed(0)
    x = torch.rand(1, 512, 8, 8)
    gate = (ECA()(x) / x)[0, :, 0, 0]
    assert len(torch.unique(gate.round(decimals=4))) > 1, "gate collapsed to one value"


def test_gradients_reach_input_and_weight():
    m = ECA()
    x = torch.rand(1, 128, 16, 16, requires_grad=True)
    m(x).sum().backward()
    assert x.grad is not None and torch.isfinite(x.grad).all()
    assert m.conv.weight.grad is not None


def test_module_is_deterministic():
    m = ECA()
    m(torch.rand(1, 64, 8, 8))  # trigger lazy build
    x = torch.rand(1, 64, 8, 8)
    assert torch.equal(m(x), m(x))


# --------------------------------------------------------------------------
# ECA block: invariants
# --------------------------------------------------------------------------


def test_gate_stays_in_unit_interval():
    """A sigmoid gate must never scale a feature past its own magnitude."""
    x = torch.rand(2, 128, 8, 8) * 10.0
    y = ECA()(x)
    assert (y.abs() <= x.abs() + 1e-6).all()


def test_rescale_gives_near_neutral_init():
    """rescale=True should start at sigmoid(mean) so fine-tuning from COCO is fair.

    ECA-Net's all-ones init would give sigmoid(k*mean) -- a spurious k-fold
    rescale of every backbone activation at step 0.
    """
    torch.manual_seed(0)
    x = torch.rand(2, 64, 32, 32)
    gate_scaled = (ECA(rescale=True)(x) / x).mean().item()
    gate_faithful = (ECA(rescale=False)(x) / x).mean().item()
    expected = torch.sigmoid(x.mean()).item()
    assert abs(gate_scaled - expected) < 0.05, gate_scaled
    # and the un-rescaled variant must be materially higher (k-fold gain)
    assert gate_faithful > gate_scaled + 0.1, (gate_faithful, gate_scaled)


def test_channel_count_drives_kernel_width():
    """The same module must pick different k for different widths."""
    narrow, wide = ECA(), ECA()
    narrow(torch.rand(1, 64, 8, 8))
    wide(torch.rand(1, 1024, 8, 8))
    assert wide.conv.kernel_size[0] >= narrow.conv.kernel_size[0]


# --------------------------------------------------------------------------
# ECA block: defect injection
# --------------------------------------------------------------------------


def test_rejects_non_4d_input():
    with pytest.raises(ValueError, match=r"\(B, C, H, W\)"):
        ECA()(torch.rand(2, 64, 32))


def test_rejects_non_positive_k_size():
    with pytest.raises(ValueError):
        ECA(k_size=0)


def test_transfer_aligned_refuses_unalignable_sequences():
    """Silently pairing the wrong layers would corrupt weights without erroring."""

    class A(torch.nn.Module):
        pass

    class B(torch.nn.Module):
        pass

    src = torch.nn.Sequential(A(), A())
    dst = torch.nn.Sequential(A(), A(), A())
    with pytest.raises(ValueError, match="cannot align"):
        transfer_aligned(src, dst)


# --------------------------------------------------------------------------
# Model configs: structure
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def models():
    register_eca()
    from ultralytics import YOLO

    return YOLO(str(BASE_YAML)).model, YOLO(str(ECA_YAML)).model


def test_eca_config_inserts_exactly_four_eca_rows(models):
    _, eca = models
    types = [type(m).__name__ for m in eca.model]
    assert types.count("ECA") == 4
    assert [i for i, t in enumerate(types) if t == "ECA"] == list(ECA_INDICES)


def test_non_eca_layers_are_identical_between_arms(models):
    """Any other difference would confound the A/B beyond the attention block."""
    base, eca = models
    b = [type(m).__name__ for m in base.model]
    e = [type(m).__name__ for m in eca.model if type(m).__name__ != "ECA"]
    assert e == b


def test_eca_params_are_negligible(models):
    """+18 params (four 1x1xk convs). A regression here means the block grew."""
    base, eca = models
    assert _params(eca) - _params(base) == 18


def test_eca_blocks_sit_at_the_expected_widths(models):
    _, eca = models
    widths = {i: eca.model[i].conv.kernel_size[0] for i in ECA_INDICES}
    assert set(widths.values()) <= {3, 5}, widths
    assert all(k % 2 == 1 for k in widths.values())


def test_eca_model_forward_runs(models):
    _, eca = models
    eca.eval()
    with torch.no_grad():
        out = eca(torch.zeros(1, 3, 320, 320))
    assert out is not None


# --------------------------------------------------------------------------
# transfer_aligned: the regression that motivated it
# --------------------------------------------------------------------------


def test_aligned_transfer_pairs_by_forward_order():
    """REGRESSION: inserting ECA shifts every later index, so name-based loading
    (Ultralytics YOLO.load) transfers almost nothing -- 52/503 items -- leaving
    the ECA arm to train from scratch while the control arm starts from COCO.

    transfer_aligned must instead pair the non-ECA layers by forward order, so
    the same weight mass lands in both arms. Target below is the control arm's
    layer sequence with ECA rows spliced in at shifted positions.
    """
    import torch.nn as nn

    class Leaf(nn.Module):
        def __init__(self, tag):
            super().__init__()
            self.w = nn.Parameter(torch.full((2,), float(tag)))

    src = nn.Sequential(Leaf(1), Leaf(2), Leaf(3), Leaf(4))
    # 4 leaves, 2 inserted ECA -- same count as src once ECA rows are skipped
    dst = nn.Sequential(Leaf(0), Leaf(0), ECA(), Leaf(0), ECA(), Leaf(0))
    copied, mismatched = transfer_aligned(src, dst)
    assert copied == 8, f"expected 4 leaves x 2 params copied, got {copied}"
    assert mismatched == 0
    vals = [float(m.w.detach().flatten()[0]) for m in dst if isinstance(m, Leaf)]
    assert vals == [1.0, 2.0, 3.0, 4.0], vals


def test_aligned_transfer_leaves_eca_at_its_init():
    """No COCO counterpart exists for ECA, so it must not be zeroed or scaled."""
    import torch.nn as nn

    src = nn.Sequential(nn.Conv2d(4, 4, 1))
    dst = nn.Sequential(nn.Conv2d(4, 4, 1), ECA())
    transfer_aligned(src, dst)
    eca = dst[1]
    eca(torch.rand(1, 4, 8, 8))  # trigger lazy build
    k = eca.conv.kernel_size[0]
    w = eca.conv.weight.detach()
    assert float(w.flatten()[0]) == pytest.approx(1.0 / k)


def test_aligned_transfer_matches_shape_mismatches_as_zero():
    """A differently-shaped target tensor must be reported, not overwritten."""
    import torch.nn as nn

    src = nn.Sequential(nn.Conv2d(3, 8, 1))
    dst = nn.Sequential(nn.Conv2d(3, 16, 1))
    copied, mismatched = transfer_aligned(src, dst)
    assert copied == 0
    # Conv2d holds both weight and bias, so two tensors are shape-mismatched.
    assert mismatched == 2
    assert dst[0].weight.shape == (16, 3, 1, 1), "shape was overwritten anyway"
    assert dst[0].bias.shape == (16,)
