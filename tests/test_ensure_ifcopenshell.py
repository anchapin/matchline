"""Regression tests for issue #1339: the shared IfcOpenShell availability guard.

The guard used to be pasted four times: once in ``bem/bem_ifc4.py`` and three
times in a row in ``ifc/ifc_import.py``, with drifting messages. The ``bem``
copy raised a ``PipelineDependencyError`` the module never imported, so in
exactly the environments the error exists for (IfcOpenShell missing) the
callers died with a ``NameError`` instead of an actionable message.

``pipeline_exceptions.ensure_ifcopenshell()`` is now the single implementation;
both frontends delegate to it. Both failure modes stay distinguishable:
"not installed" (install) vs "installed but broken" (force-reinstall).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import bem.bem_ifc4
import ifc.ifc_import
import pipeline_exceptions
from pipeline_exceptions import PipelineDependencyError, ensure_ifcopenshell

ROOT = Path(__file__).resolve().parent.parent


def _real_ifcopenshell_present() -> bool:
    """True when the environment really has IfcOpenShell importable."""
    try:
        return importlib.util.find_spec("ifcopenshell") is not None
    except (ImportError, ValueError):
        return False


class _BrokenBinaryFinder:
    """Meta path finder that makes ``import ifcopenshell`` fail like a bad binary."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ifcopenshell" or fullname.startswith("ifcopenshell."):
            raise OSError("libGL.so.1: cannot open shared object file")
        return None


def _patched_find_spec(monkeypatch, answer):
    """Patch find_spec for "ifcopenshell" only; leave every other lookup alone."""
    real = importlib.util.find_spec

    def _fake(name, *args, **kwargs):
        if name == "ifcopenshell":
            return answer
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _fake)


@pytest.fixture
def missing_ifcopenshell(monkeypatch):
    """Simulate an environment where IfcOpenShell is not installed at all."""
    _patched_find_spec(monkeypatch, None)


@pytest.fixture
def broken_ifcopenshell(monkeypatch):
    """Simulate an environment where IfcOpenShell is installed but broken."""
    _patched_find_spec(monkeypatch, object())
    monkeypatch.delitem(sys.modules, "ifcopenshell", raising=False)
    finder = _BrokenBinaryFinder()
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


@pytest.fixture
def working_ifcopenshell(monkeypatch):
    """Simulate a working IfcOpenShell install (the real one, or a stand-in)."""
    if _real_ifcopenshell_present():
        yield
        return
    _patched_find_spec(monkeypatch, object())
    monkeypatch.setitem(sys.modules, "ifcopenshell", types.ModuleType("ifcopenshell"))
    yield


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------


def test_missing_dependency_raises_pipeline_dependency_error(missing_ifcopenshell):
    """The shared helper reports a missing dependency, not a NameError."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        ensure_ifcopenshell()
    message = str(exc_info.value)
    assert "not installed" in message
    assert "pip install ifcopenshell" in message


def test_bem_ifc4_missing_dependency_raises_pipeline_dependency_error(missing_ifcopenshell):
    """bem.bem_ifc4._ensure_ifc() must not die with NameError (#1339)."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        bem.bem_ifc4._ensure_ifc()
    assert "pip install ifcopenshell" in str(exc_info.value)


def test_ifc_import_missing_dependency_raises_pipeline_dependency_error(missing_ifcopenshell):
    """ifc.ifc_import._ensure_ifc() must not die with NameError (#1339)."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        ifc.ifc_import._ensure_ifc()
    assert "pip install ifcopenshell" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Broken binary
# ---------------------------------------------------------------------------


def test_broken_binary_raises_pipeline_dependency_error(broken_ifcopenshell):
    """An installed-but-broken IfcOpenShell says so, and how to fix it."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        ensure_ifcopenshell()
    message = str(exc_info.value)
    assert "installed but failed to import" in message
    assert "--force-reinstall" in message
    assert isinstance(exc_info.value.__cause__, OSError)


MISSING_MESSAGE = (
    "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
)
BROKEN_MESSAGE = (
    "ifcopenshell is installed but failed to import (broken binary or missing "
    "system library). Reinstall with: pip install ifcopenshell --force-reinstall"
)


def test_missing_message_says_how_to_install(missing_ifcopenshell):
    """The availability message names the install command."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        ensure_ifcopenshell()
    assert str(exc_info.value) == MISSING_MESSAGE
    assert "--force-reinstall" not in str(exc_info.value)


def test_broken_message_says_how_to_reinstall(broken_ifcopenshell):
    """The integrity message names the force-reinstall command."""
    with pytest.raises(PipelineDependencyError) as exc_info:
        ensure_ifcopenshell()
    assert str(exc_info.value) == BROKEN_MESSAGE
    assert "not installed" not in str(exc_info.value)

# ---------------------------------------------------------------------------
# Working install
# ---------------------------------------------------------------------------


def test_helper_returns_without_raising_when_available(working_ifcopenshell):
    """A usable IfcOpenShell install passes the guard."""
    assert ensure_ifcopenshell() is None


def test_frontends_return_without_raising_when_available(working_ifcopenshell):
    """Both frontends pass the guard when IfcOpenShell is usable."""
    assert bem.bem_ifc4._ensure_ifc() is None
    assert ifc.ifc_import._ensure_ifc() is None


# ---------------------------------------------------------------------------
# Single implementation
# ---------------------------------------------------------------------------


def test_both_frontends_delegate_to_the_same_helper():
    """The pasted copies are gone: one helper, two thin delegates."""
    assert bem.bem_ifc4.ensure_ifcopenshell is pipeline_exceptions.ensure_ifcopenshell
    assert ifc.ifc_import.ensure_ifcopenshell is pipeline_exceptions.ensure_ifcopenshell


def test_guard_message_no_longer_duplicated_in_frontends():
    """The guard's OSError handling lives only in pipeline_exceptions.py."""
    for rel in ("bem/bem_ifc4.py", "ifc/ifc_import.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "installed but failed to import" not in source, rel
