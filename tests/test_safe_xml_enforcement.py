"""Test that all XML parsing in the project goes through safe_xml.py.

Runs as a lint-level test in CI (FAILS if any module imports dangerous parsers).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DANGEROUS = {
    "xml.etree.ElementTree": "xml.etree.ElementTree — use safe_xml.parse_xml instead",
    "xml.dom.minidom": "xml.dom.minidom — use safe_xml.parse_xml instead",
    "xml.sax.expatreader": "xml.sax — use safe_xml.parse_xml instead",
    "lxml": "lxml — use safe_xml.parse_xml instead",
    "defusedxml": "defusedxml — use safe_xml.parse_xml directly",
}

ALLOWED = {"safe_xml", "test_safe_xml_enforcement", "safe_xml_test_utils"}


def _find_violations(root: Path) -> list[tuple[Path, int, str]]:
    violations = []
    for py_file in root.rglob("*.py"):
        if any(b in py_file.parts for b in ALLOWED):
            continue
        try:
            src = py_file.read_text()
            tree = ast.parse(src, filename=str(py_file))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module in DANGEROUS:
                    violations.append((py_file, node.lineno, DANGEROUS[node.module]))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in DANGEROUS:
                        violations.append((py_file, node.lineno, DANGEROUS[alias.name]))
    return violations


def _test_no_dangerous_xml():
    violations = _find_violations(Path("$WORKTREE"))
    if not violations:
        return
    lines = "\n".join(
        f"  {p.relative_to(Path('$WORKTREE'))}:{ln}: {msg}" for p, ln, msg in violations
    )
    raise AssertionError(f"Dangerous XML imports found:\n{lines}")


def test_no_dangerous_xml_imports():
    _test_no_dangerous_xml()


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
