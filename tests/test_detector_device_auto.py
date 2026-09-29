"""Tests for detector/train.py --device auto-detection (issue #508).

Run from the repo root:
    python3 -m pytest tests/test_detector_device_auto.py -v

These live in the main ``tests/`` suite because they mock torch/ultralytics
and do not need a GPU. The patterns mirror test_detector_train_seed.py.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from detector import train as detector_train

TRAIN_PY = Path(detector_train.__file__).resolve()


def _load_train_module(train_py: Path):
    """Import a train.py by path under a private module name."""
    spec = importlib.util.spec_from_file_location("detector_train_under_test", train_py)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubResult:
    """Stand-in for ultralytics' Results object; train.py only reads save_dir."""

    save_dir = "/nonexistent/run"


def _make_torch_stub(cuda_available: bool, device_name: str = "AMD Radeon RX 6600 XT"):
    """Return a minimal torch stub that pretends to be CUDA-available (or not)."""

    # Create the cuda submodule first
    cuda_module = types.ModuleType("torch.cuda")
    cuda_module.is_available = lambda: cuda_available
    cuda_module.get_device_name = lambda idx: device_name

    # Create the main torch module
    module = types.ModuleType("torch")
    module.cuda = cuda_module
    module.manual_seed = lambda s: None
    return module


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


def _write_data_yaml(tmp_path: Path) -> Path:
    """A data yaml whose root is a real (empty) directory, so no dataset is needed."""
    root = tmp_path / "fake_dataset"
    (root / "images" / "train").mkdir(parents=True, exist_ok=True)
    (root / "images" / "val").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "data.yaml"
    path.write_text(f"path: {root}\ntrain: images/train\nval: images/val\nnc: 2\n")
    return path


def _run_main(monkeypatch, tmp_path, extra_argv: list[str], torch_stub):
    """Drive train.main() with a stubbed ultralytics/torch; return recorded calls."""
    recorded: dict = {"printed": []}
    monkeypatch.setitem(sys.modules, "ultralytics", _stub_ultralytics(recorded))
    monkeypatch.setitem(sys.modules, "torch", torch_stub)

    # Capture print output
    printed_lines = []

    def capture_print(*args, **kwargs):
        printed_lines.append(" ".join(str(a) for a in args))

    monkeypatch.setattr("builtins.print", capture_print)

    base_argv = [
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
    ]
    monkeypatch.setattr(sys, "argv", base_argv + extra_argv)
    _load_train_module(TRAIN_PY).main()
    assert "train_kwargs" in recorded, "train.py never called model.train()"
    recorded["printed"] = printed_lines
    return recorded


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_default_with_cuda_available_resolves_to_0(monkeypatch, tmp_path):
    """When --device is omitted and CUDA is available, auto-resolve to '0'."""
    torch_stub = _make_torch_stub(cuda_available=True)
    recorded = _run_main(monkeypatch, tmp_path, [], torch_stub)
    assert recorded["train_kwargs"]["device"] == "0", (
        f"Expected device='0' when CUDA available and --device not passed, "
        f"got {recorded['train_kwargs']['device']!r}"
    )


def test_default_with_cuda_unavailable_resolves_to_cpu(monkeypatch, tmp_path):
    """When --device is omitted and CUDA is unavailable, auto-resolve to 'cpu'."""
    torch_stub = _make_torch_stub(cuda_available=False)
    recorded = _run_main(monkeypatch, tmp_path, [], torch_stub)
    assert recorded["train_kwargs"]["device"] == "cpu", (
        f"Expected device='cpu' when CUDA unavailable and --device not passed, "
        f"got {recorded['train_kwargs']['device']!r}"
    )


def test_explicit_device_cpu_with_cuda_available_emits_warning(monkeypatch, tmp_path):
    """When --device cpu is explicit and CUDA is available, emit a warning."""
    torch_stub = _make_torch_stub(cuda_available=True, device_name="AMD Radeon RX 6600 XT")
    recorded = _run_main(monkeypatch, tmp_path, ["--device", "cpu"], torch_stub)
    # Warning should be in printed output
    warning_lines = [l for l in recorded["printed"] if "WARNING" in l and "cpu" in l.lower()]
    assert warning_lines, (
        f"Expected warning about --device cpu with CUDA available, "
        f"got printed lines: {recorded['printed']}"
    )
    assert "AMD Radeon RX 6600 XT" in warning_lines[0]


def test_explicit_device_0_unaffected(monkeypatch, tmp_path):
    """When --device 0 is explicitly passed, use it verbatim."""
    torch_stub = _make_torch_stub(cuda_available=True)
    recorded = _run_main(monkeypatch, tmp_path, ["--device", "0"], torch_stub)
    assert recorded["train_kwargs"]["device"] == "0"
    # No warning should be printed for explicit --device 0
    warning_lines = [l for l in recorded["printed"] if "WARNING" in l]
    assert not warning_lines, f"Unexpected warning for --device 0: {warning_lines}"


def test_device_resolved_logged_early(monkeypatch, tmp_path):
    """The resolved device should be printed early (before model.train() output)."""
    torch_stub = _make_torch_stub(cuda_available=True)
    recorded = _run_main(monkeypatch, tmp_path, [], torch_stub)
    # The [train] device= line should appear in printed output
    device_lines = [l for l in recorded["printed"] if "[train] device=" in l]
    assert device_lines, (
        f"Expected [train] device= line in printed output, got: {recorded['printed']}"
    )
    assert device_lines[0] == "[train] device=0"


def test_explicit_device_cpu_with_cuda_unavailable_no_warning(monkeypatch, tmp_path):
    """When --device cpu is explicit but CUDA is unavailable, no warning needed."""
    torch_stub = _make_torch_stub(cuda_available=False)
    recorded = _run_main(monkeypatch, tmp_path, ["--device", "cpu"], torch_stub)
    # Should still train on cpu without warning
    assert recorded["train_kwargs"]["device"] == "cpu"
    # No warning should be printed
    warning_lines = [l for l in recorded["printed"] if "WARNING" in l]
    assert not warning_lines, f"Unexpected warning for --device cpu without CUDA: {warning_lines}"


def test_run_ab_eca_sh_passes_device_explicitly():
    """Verify run_ab_eca.sh already passes --device explicitly (not relying on default)."""
    import re

    script = Path(__file__).resolve().parent.parent / "detector" / "run_ab_eca.sh"
    content = script.read_text()
    # Find the DEVICE variable assignment
    match = re.search(r"^DEVICE=(\S+)$", content, re.MULTILINE)
    assert match, "Could not find DEVICE= in run_ab_eca.sh"
    device_value = match.group(1)
    # The script should set DEVICE to '0' and pass it explicitly
    assert device_value == "0", f"Expected DEVICE=0 in run_ab_eca.sh, got {device_value!r}"
    # Also verify it passes --device "$DEVICE"
    assert "--device \"$DEVICE\"" in content or "--device $DEVICE" in content, (
        "run_ab_eca.sh should pass --device $DEVICE"
    )
