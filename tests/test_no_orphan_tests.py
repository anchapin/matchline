"""No pytest tests outside tests/ (#1064).

pyproject.toml sets testpaths = ["tests"], so a def test_ in any repo-root module is
silently never run in CI. This guard keeps those from accumulating.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DEF_TEST = re.compile(r"^\s*(?:async\s+)?def test_")


def test_no_test_functions_outside_tests_dir():
    strays = []
    for f in sorted(ROOT.glob("*.py")):
        for lineno, line in enumerate(
            f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if _DEF_TEST.match(line):
                strays.append(f"{f.name}:{lineno}")
    assert not strays, (
        "pytest never collects repo-root files (testpaths=['tests']); "
        f"move these into tests/: {strays}"
    )
