"""Repo-map generator (#1096): a deterministic Mermaid module map built from imports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_repo_map.py"


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _run(root: Path, out: Path) -> str:
    subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--out", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.read_text(encoding="utf-8")


def test_internal_nodes_and_edges_appear(tmp_path):
    _write_tree(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "def f():\n    return 1\n",
            "synth/__init__.py": "",
            "synth/sheets.py": "from . import mech\nimport a\n",
            "synth/mech.py": "pass\n",
        },
    )
    text = _run(tmp_path, tmp_path / "map.mmd")
    assert "    a" in text and "    b" in text
    assert "    synth_sheets" in text and "    synth_mech" in text
    assert "  a --> b" in text
    assert "  synth_sheets --> synth_mech" in text
    assert "  synth_sheets --> a" in text


def test_stdlib_and_external_imports_excluded(tmp_path):
    _write_tree(
        tmp_path,
        {
            "a.py": "import os\nimport json\nfrom collections import defaultdict\n",
        },
    )
    text = _run(tmp_path, tmp_path / "map.mmd")
    for gone in ("os", "json", "collections"):
        assert f"    {gone}\n" not in text


def test_self_import_excluded(tmp_path):
    _write_tree(tmp_path, {"a.py": "import a\nimport b\n", "b.py": "pass\n"})
    text = _run(tmp_path, tmp_path / "map.mmd")
    assert "a --> a" not in text


def test_cluster_prefix_assignment(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_repo_map", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.cluster_of("bem_export") == "BEM"
    assert mod.cluster_of("ifc_wall_loops") == "IFC"
    assert mod.cluster_of("cli") == "CORE"


def test_deterministic_output(tmp_path):
    _write_tree(tmp_path, {"a.py": "import b\n", "b.py": "pass\n"})
    first = _run(tmp_path, tmp_path / "one.mmd")
    second = _run(tmp_path, tmp_path / "two.mmd")
    assert first == second


def test_creates_missing_out_directory(tmp_path):
    _write_tree(tmp_path, {"a.py": "pass\n"})
    out = tmp_path / "docs" / "nested" / "map.mmd"
    _run(tmp_path, out)
    assert out.exists()
    assert out.with_suffix(".md").exists()
