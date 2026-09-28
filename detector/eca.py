"""Efficient Channel Attention (ECA) for the detector backbone.

Reference
---------
FloorYOLO: an attention-enhanced YOLOv8n for floor plan object recognition
(Frontiers in Artificial Intelligence, 2026; doi:10.3389/frai.2026.1911589),
which bolts an ECA block onto the backbone to improve discrimination of
visually similar intra-class symbol variants -- our hard case (door/window
symbol variants across drawing styles).

The underlying block is ECA-Net (Wang et al., CVPR 2020). ECA deliberately
skips the spatial convolution and the bottleneck reduction of SE/CBAM: it
models local cross-channel interaction with a single 1-D convolution of width
``k`` applied along the channel axis, gated by a sigmoid. Cost is
``O(k)`` parameters per stage (k <= 9 here) and no reduction, so the block is
negligible against a detection backbone -- which is the property that makes
this a clean A/B against the plain yolo11n baseline.

Why the init matters here
-------------------------
This block is fine-tuned from a COCO-pretrained checkpoint, so the model must
start at (or very near) the baseline's behaviour or the comparison measures the
init perturbation instead of the attention. ECA-Net's reference init sets the
1-D conv weights to all ones, which makes the pre-sigmoid response ``k * mean``
and the initial gate ``sigmoid(k * mean)`` -- a spurious ``k``-fold gain that
would rescale every backbone activation at step 0.

We therefore default to ``1/k`` scaling (``rescale=True``): the initial gate is
``sigmoid(mean)``, a near-neutral per-channel recalibration, and the block has
to *learn* any cross-channel interaction from there. Pass ``rescale=False`` for
bit-faithful ECA-Net init when reproducing published numbers.

Ultralytics integration
------------------------
``parse_model`` resolves YAML module names through the ``globals()`` dict of
``ultralytics.nn.tasks`` (the ``globals()[m]`` lookup). A custom module is
therefore registered by injecting it into that dict. A module with no YAML args
lands in the final ``else: c2 = ch[f]`` branch, i.e. channels pass through
unchanged and the module is constructed with no arguments -- hence the lazy
channel inference in ``forward``.

Usage
-----
Build the ECA variant from ``configs/yolo11n_eca.yaml`` (with a partial
COCO-pretrained load via ``--pretrained yolo11n.pt``); call
:func:`register_eca` first so ``parse_model`` can resolve the name.
"""

from __future__ import annotations

import math
from typing import Optional

import torch
from torch import nn

__all__ = [
    "ECA",
    "adaptive_ksize",
    "register_eca",
    "transfer_aligned",
    "MODEL_YAML",
]


# Adapter for configs/yolo11n_eca.yaml. Kept beside the module so the YAML and
# the code that can parse it stay in one place.
MODEL_YAML = "configs/yolo11n_eca.yaml"


def adaptive_ksize(channels: int, gamma: int = 2, b: int = 1) -> int:
    """ECA-Net's adaptive receptive field: ``k = |log2(C)/gamma + b/gamma|_odd``.

    Wider stages get a slightly wider interaction window; ``k`` is always odd so
    the 1-D conv can use symmetric padding and keep the channel axis length.
    """
    if channels < 1:
        raise ValueError(f"channels must be >= 1, got {channels}")
    k = int(abs(math.log2(channels) / gamma + b / gamma))
    return k if k % 2 == 1 else k + 1


class ECA(nn.Module):
    """Efficient Channel Attention.

    Parameters
    ----------
    k_size:
        Explicit interaction width. ``None`` (default) picks it adaptively from
        the channel count on first forward, which is what lets the module be
        constructed with no arguments by ``parse_model``.
    gamma, b:
        ECA-Net adaptive-width parameters (used only when ``k_size is None``).
    rescale:
        Divide the conv init by ``k`` so the initial gate is ``sigmoid(mean)``
        (near-neutral, good for fine-tuning). ``False`` gives the ECA-Net
        all-ones init.
    """

    def __init__(
        self,
        k_size: Optional[int] = None,
        gamma: int = 2,
        b: int = 1,
        rescale: bool = True,
    ) -> None:
        super().__init__()
        if k_size is not None and k_size < 1:
            raise ValueError(f"k_size must be >= 1, got {k_size}")
        self.k_size = k_size
        self.gamma = gamma
        self.b = b
        self.rescale = rescale
        self.conv: Optional[nn.Conv1d] = None

    def extra_repr(self) -> str:
        return f"k_size={self.k_size}, adaptive={self.k_size is None}, rescale={self.rescale}"

    def _build(self, channels: int) -> None:
        k = self.k_size if self.k_size is not None else adaptive_ksize(
            channels, self.gamma, self.b
        )
        if k % 2 == 0:
            k += 1  # keep the channel axis length under symmetric padding
        conv = nn.Conv1d(1, 1, kernel_size=k, padding=(k - 1) // 2, bias=False)
        with torch.no_grad():
            conv.weight.fill_(1.0 / k if self.rescale else 1.0)
        self.conv = conv

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x``: (B, C, H, W) -> channel-reweighted ``x``, same shape."""
        if x.dim() != 4:
            raise ValueError(f"ECA expects (B, C, H, W), got shape {tuple(x.shape)}")
        if self.conv is None:
            self._build(x.size(1))
        # (B, C, H, W) -> (B, C, 1, 1) -> (B, 1, C): the 1-D conv runs across
        # channels, never across space, so k stays tiny regardless of H and W.
        y = x.mean(dim=(2, 3), keepdim=True).flatten(1).unsqueeze(1)  # (B, 1, C)
        y = self.conv(y).transpose(-1, -2).unsqueeze(-1)  # (B, C, 1, 1)
        return x * torch.sigmoid(y)


def register_eca() -> type[ECA]:
    """Make ``ECA`` resolvable by Ultralytics ``parse_model``.

    Idempotent: returns the (possibly already-registered) class. Re-registration
    is harmless and keeps import order from mattering.
    """
    import ultralytics.nn.tasks as tasks

    existing = tasks.__dict__.get("ECA")
    if existing is not None:
        return existing
    tasks.ECA = ECA
    return ECA


def _is_seq(mod: nn.Module) -> bool:
    return isinstance(mod, (nn.Sequential, nn.ModuleList))


def transfer_aligned(
    src: nn.Module, dst: nn.Module, skip_types: tuple = (ECA,)
) -> tuple[int, int]:
    """Copy weights from *src* into *dst* by **structure**, not by parameter name.

    Why this exists
    ---------------
    Inserting ECA rows into the backbone shifts every later layer index, so the
    two models' parameter names no longer correspond: in
    ``yolo11n_eca.yaml`` index 3 is an ``ECA`` where ``yolo11n.pt`` has a
    ``Conv``, index 4 is a ``Conv`` where the checkpoint has ``C3k2``, and so
    on down the whole network. Ultralytics' own ``YOLO.load()`` matches by name
    and reports "Transferred 52/503 items" -- the ECA arm would silently train
    from scratch while the control arm starts from COCO weights, which confounds
    the entire experiment.

    So we ignore the inserted blocks and pair the remaining modules in forward
    order. For each pair whose type matches we recurse; at a leaf we copy the
    state dict when the keys *and* shapes agree. The ``nc=80`` -> ``nc=2`` Detect
    head therefore keeps its fresh init, exactly as intended for fine-tuning.

    Returns ``(num_params_copied, num_tensors_mismatched)``.
    """
    copied = 0
    mismatched = 0

    def walk(s: nn.Module, d: nn.Module) -> None:
        nonlocal copied, mismatched
        if type(s) is not type(d):
            return
        if any(isinstance(d, t) for t in skip_types):
            return  # inserted block: has no counterpart in src

        # Pair ordinary submodules by child name. This descends through wrappers
        # such as DetectionModel -> Sequential, and through leaf blocks (Conv,
        # C3k2, ...) whose internal structure we did not change.
        sch, dch = dict(s.named_children()), dict(d.named_children())
        if sch and sch.keys() == dch.keys():
            for k in sch:
                walk(sch[k], dch[k])
            return

        # Sequences are paired positionally after dropping the inserted blocks:
        # their child names are indices, which the insertion shifted.
        if _is_seq(d) and _is_seq(s):
            sc = list(s)
            dc = [c for c in d if not any(isinstance(c, t) for t in skip_types)]
            if len(sc) != len(dc):
                raise ValueError(
                    f"cannot align: {type(d).__name__} has {len(dc)} layers vs "
                    f"{type(s).__name__} {len(sc)} after skipping "
                    f"{[t.__name__ for t in skip_types]}"
                )
            for a, b in zip(sc, dc):
                walk(a, b)
            return
        ssd, dsd = s.state_dict(), d.state_dict()
        if ssd.keys() != dsd.keys():
            return
        for k, sv in ssd.items():
            if sv.shape == dsd[k].shape:
                dsd[k].copy_(sv)
                copied += sv.numel()
            else:
                mismatched += 1

    walk(src, dst)
    return copied, mismatched


def load_pretrained_into(model_path: str, weights: str) -> tuple[int, int]:
    """Build *model_path* and align COCO-pretrained *weights* into it.

    Thin wrapper over :func:`transfer_aligned` so callers do not have to know
    that Ultralytics' name-based ``load()`` is unsafe for an architecture with
    inserted layers.
    """
    from ultralytics import YOLO

    register_eca()
    y = YOLO(model_path)
    if weights.endswith(".pt"):
        import ultralytics.nn.tasks as tasks

        loaded = tasks.load_checkpoint(weights)
        ckpt = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
        if isinstance(ckpt, dict):
            ckpt = ckpt.get("ema") or ckpt.get("model")
        src = ckpt.float()
    else:
        src = YOLO(weights).model
    return transfer_aligned(src, y.model)
