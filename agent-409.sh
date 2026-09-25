#!/bin/bash
set -euo pipefail
WORKTREE=/home/alex/Projects/worktrees/wave1-issue409
ISSUE=409
BRANCH=fix/issue-409-xxe-enforcement
cd $WORKTREE

# Confirm clean state
git checkout -b $BRANCH 2>&1
git status --short

# --- Fix 1: tests/test_safe_xml_enforcement.py ---
cat > tests/test_safe_xml_enforcement.py << 'EOF'
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
        f"  {p.relative_to(Path('$WORKTREE'))}:{ln}: {msg}"
        for p, ln, msg in violations
    )
    raise AssertionError(f"Dangerous XML imports found:\n{lines}")


def test_no_dangerous_xml_imports():
    _test_no_dangerous_xml()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
EOF

# --- Fix 2: safe_xml.py — add public enforcement helpers ---
cat >> safe_xml.py << 'EOF'


def is_safe_parser(module_name: str) -> bool:
    """Return True for known-safe XML parser modules."""
    return module_name not in {
        "xml.etree.ElementTree",
        "xml.dom.minidom",
        "xml.sax.expatreader",
        "lxml",
        "defusedxml",
    }
EOF

# --- Fix 3: docs/design-docs/index.md ---
if ! grep -q "XXE" docs/design-docs/index.md 2>/dev/null; then
    echo -e "\n## XML Security (XXE Protection)\n\nAll XML parsing MUST use `safe_xml.py`. Never import `xml.etree.ElementTree`, `lxml.etree`, or bare `defusedxml` directly. The enforcement test is `tests/test_safe_xml_enforcement.py`." >> docs/design-docs/index.md
fi

# --- Fix 4: CONTRIBUTING.md ---
if [ -f CONTRIBUTING.md ] && ! grep -q "safe_xml" CONTRIBUTING.md; then
    echo -e "\n## XML Parsing\n\nUse `safe_xml.py` for all XML parsing. Never use `xml.etree.ElementTree`, `lxml.etree`, or bare `defusedxml` directly." >> CONTRIBUTING.md
fi

git add -A && git commit -m "fix: add project-wide XXE protection enforcement test

Closes #409" 2>&1
git push -u origin $BRANCH 2>&1
echo "AGENT_409_DONE"
