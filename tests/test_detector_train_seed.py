"""Tests for detector.train -- ``--seed`` must reach ``model.train()`` (issue #506).

These live in the main ``tests/`` suite, not ``detector/tests/``, because the
defect is pure argument forwarding: no torch, no ultralytics, no dataset, no GPU.
``ultralytics`` and ``torch`` are stubbed in ``sys.modules`` (``train.py`` imports
them lazily inside ``main()``), so the guard runs in CI. The 28 torch-dependent
detector tests still do not -- that gap is #507.

Happy path: the value given to ``--seed`` is forwarded verbatim to
``model.train(seed=...)``.

Why this needs a test at all: ``train.py`` seeded ``random``/``numpy``/``torch``
itself and then called ``model.train()`` *without* a seed. Ultralytics re-seeds
all three from its own ``seed`` argument at the start of training, overriding
those calls and falling back to its default of ``0``. The python-level seeding
looked correct, so ``--seed 1`` and ``--seed 2`` produced byte-identical
``results.csv`` to ``--seed 0`` and every multi-seed A/B was one sample measured
three times. A driver reporting three identical rows is a failed experiment, not
three confirmations (see #500).

Limitations: asserts what is forwarded, not what ultralytics does with it. A
behavioural check (two seeds, different ``train/box_loss``) would be stronger but
needs a real run, and ROCm is not bit-reproducible at fixed seed anyway (#509).
"""

from __future__ import annotations

import importlib.util
import random
import sys
import types
from pathlib import Path

import pytest

from detector import train as detector_train

TRAIN_PY = Path(detector_train.__file__).resolve()

SEED_KWARG_LINE = "seed=args.seed,"


def _load_train_module(train_py: Path):
    """Import a train.py by path under a private module name.

    Loading by path (rather than ``from detector import train``) is what lets the
    defect injection below point the same assertions at a mutated *copy*, so
    proving the guard has teeth never edits the real file.
    """
    spec = importlib.util.spec_from_file_location("detector_train_under_test", train_py)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubResult:
    """Stand-in for ultralytics' Results object; train.py only reads save_dir."""

    save_dir = "/nonexistent/run"


def _stub_ultralytics(recorded: dict):
    module = types.ModuleType("ultralytics")

    class FakeYOLO:
        def __init__(self, model_path):
            recorded["model_path"] = model_path

        def train(self, **kwargs):
            recorded["train_kwargs"] = kwargs
            return _StubResult()

        def load(self, weights):
            recorded["loaded"] = weights

    module.YOLO = FakeYOLO
    return module


def _stub_torch(recorded: dict):
    module = types.ModuleType("torch")

    def manual_seed(value):
        recorded["torch_seed"] = value

    module.manual_seed = manual_seed
    return module


def _write_data_yaml(tmp_path: Path) -> Path:
    """A data yaml whose root is a real (empty) directory, so no dataset is needed."""
    root = tmp_path / "fake_dataset"
    (root / "images" / "train").mkdir(parents=True, exist_ok=True)
    (root / "images" / "val").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "data.yaml"
    path.write_text(f"path: {root}\ntrain: images/train\nval: images/val\nnc: 2\n")
    return path


@pytest.fixture(autouse=True)
def _restore_global_rng_state():
    """Undo train.main()'s real random.seed/np.random.seed calls.

    `torch` is stubbed, but `random` and `numpy` are the genuine modules, so
    driving main() reseeds process-global state that later tests may inherit.
    """
    import numpy as np

    py_state, np_state = random.getstate(), np.random.get_state()
    yield
    random.setstate(py_state)
    np.random.set_state(np_state)


def _run_main(monkeypatch, tmp_path, seed, train_py=TRAIN_PY):
    """Drive train.main() with a stubbed ultralytics/torch; return what it forwarded."""
    recorded: dict = {}
    monkeypatch.setitem(sys.modules, "ultralytics", _stub_ultralytics(recorded))
    monkeypatch.setitem(sys.modules, "torch", _stub_torch(recorded))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train.py",
            "--data",
            str(_write_data_yaml(tmp_path)),
            "--model",
            "yolo11n.pt",
            "--epochs",
            "1",
            "--name",
            "harness_check",
            "--project",
            str(tmp_path / "runs"),
            "--seed",
            str(seed),
        ],
    )
    _load_train_module(train_py).main()
    assert "train_kwargs" in recorded, "train.py never called model.train()"
    return recorded


def _assert_seed_forwarded(recorded: dict, seed: int) -> None:
    kwargs = recorded["train_kwargs"]
    assert "seed" in kwargs, f"model.train() got no seed kwarg (got {sorted(kwargs)})"
    assert kwargs["seed"] == seed, f"expected seed={seed}, forwarded {kwargs['seed']!r}"


@pytest.mark.parametrize("seed", [0, 1, 2, 1234])
def test_seed_reaches_model_train(monkeypatch, tmp_path, seed):
    recorded = _run_main(monkeypatch, tmp_path, seed)
    _assert_seed_forwarded(recorded, seed)


def test_distinct_seeds_forward_distinctly(monkeypatch, tmp_path):
    """A hardcoded forward would satisfy any single seed; three must differ.

    This is the exact shape of the #500 driver output, checked at the source
    instead of after four hours of GPU time.
    """
    forwarded = {
        seed: _run_main(monkeypatch, tmp_path, seed)["train_kwargs"]["seed"] for seed in (0, 1, 2)
    }
    assert forwarded == {0: 0, 1: 1, 2: 2}


def test_seed_zero_is_forwarded_not_omitted(monkeypatch, tmp_path):
    """--seed 0 is the default; `kwargs.get("seed") == 0` must not pass by accident.

    Ultralytics' own default is also 0, so an omitted kwarg is indistinguishable
    from a forwarded 0 by value alone. The key has to be present.
    """
    recorded = _run_main(monkeypatch, tmp_path, 0)
    assert "seed" in recorded["train_kwargs"]


def test_python_level_seeds_still_applied(monkeypatch, tmp_path):
    """random/numpy/torch are seeded too -- the calls that made the bug invisible.

    Kept as a test so nobody "cleans up" the redundant calls and re-breaks the
    run when ultralytics is swapped out.
    """
    recorded = _run_main(monkeypatch, tmp_path, 7)
    assert random.random() == pytest.approx(random.Random(7).random())
    assert recorded["torch_seed"] == 7


def test_removing_the_kwarg_is_detected(monkeypatch, tmp_path):
    """Defect injection: the guard has teeth, proved on a copy of train.py.

    The real train.py is never modified -- the matrix currently running reads
    it, and a mid-matrix edit would silently change later arms.
    """
    source = TRAIN_PY.read_text()
    assert SEED_KWARG_LINE in source, (
        f"train.py no longer contains {SEED_KWARG_LINE!r}; this test needs updating"
    )
    mutated = tmp_path / "train_no_seed.py"
    mutated.write_text(source.replace(SEED_KWARG_LINE, "# seed kwarg removed\n"))

    recorded = _run_main(monkeypatch, tmp_path, 7, train_py=mutated)
    with pytest.raises(AssertionError, match="no seed kwarg"):
        _assert_seed_forwarded(recorded, 7)
