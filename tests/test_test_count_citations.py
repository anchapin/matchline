"""Drift guard for the collected-test-count figures cited in the docs (#1359).

`.github/workflows/ci.yml` pins `EXPECTED_TEST_COUNT` and that pin is the
canonical figure. Three orientation docs quote the same number in prose
(`AGENTS.md` Verify section, `docs/ci.md` and `README.md`'s quickstart), and
`docs/ci.md` repeats it in the Behaviour section of the test-count gate
description. Those prose figures used to sit at a stale value (3752) while the
suite was well past it, and nothing failed: the drift was invisible until CI
printed both numbers side by side.

`README.md` joined the guarded set in #1366, which also fixed the last
unguarded figures: the README's "~3,500 tests" quickstart note became a guarded
citation, and the per-file count in `docs/ifc_import.md` is now pinned to the
count pytest actually collects from `tests/test_ifc_import.py`.
`QUALITY-SCORE.md` is a dated snapshot (Analysis Date: 2026-10-07), so its
"1,744 tests" line is history rather than a live figure -- see
`_HISTORICAL_DOCS`.

This module follows the doc-count sync pattern of `tests/test_validate.py`
(the `_COUNT_DOCS` assertions that pin prose counts to a code constant): the
prose figures are parsed out of the docs and asserted against the canonical
constant read from `ci.yml`. A missing or unparseable citation is drift too,
so it fails the same way a wrong number does.

Two constraints shape the layout below. First, the CI test-count gate counts
the tests in this file too, so the guard must not change the number of
collected items: every check lives inside an existing test function rather
than in a new one. Second, `pytest` is run as a subprocess for the per-file
count, because a collected count is what `pytest --collect-only` reports and
that is the number a reader of the doc can reproduce.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_CI_YML = _REPO / ".github" / "workflows" / "ci.yml"

# Docs that cite the collected-test count of the suite in their Verify section.
_CITATION_DOCS = (
    "AGENTS.md",
    "docs/ci.md",
    "README.md",
)

# Docs that cite the collected count of a single test file, mapped to that file.
_PER_FILE_CITATIONS = {
    "docs/ifc_import.md": "tests/test_ifc_import.py",
}

# Historical documents that may quote an outdated figure on purpose. The guard
# must never read them -- CONTRACT.md's stale mention is dated draft history,
# CHANGELOG.md records past counts, and QUALITY-SCORE.md is a dated snapshot
# (Analysis Date: 2026-10-07) whose "1,744 tests" line describes the suite as
# of that date, not as of today.
_HISTORICAL_DOCS = (
    "CONTRACT.md",
    "CHANGELOG.md",
    "QUALITY-SCORE.md",
)

# "(3752 collected; ...)" -- the Verify-section citation.
_CITATION_RE = re.compile(r"\((\d+) collected")

# "`EXPECTED_TEST_COUNT` (3951; ...)" -- the docs/ci.md Behaviour section.
_BEHAVIOR_RE = re.compile(r"EXPECTED_TEST_COUNT`? \((\d+);")

# "EXPECTED_TEST_COUNT=4143  # ..." -- the canonical pin in ci.yml.
_PIN_RE = re.compile(r"^\s*EXPECTED_TEST_COUNT=(\d+)\b", re.M)

# "`tests/test_ifc_import.py` (51 collected; ...)" -- a per-file citation.
_PER_FILE_RE = re.compile(r"`(tests/[\w./-]+\.py)` \((\d+) collected")

# "51 tests collected in 1.02s" -- the tail of a pytest --collect-only run.
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")


def _expected_test_count() -> int:
    """The canonical collected-test count, read from ci.yml."""
    values = _PIN_RE.findall(_CI_YML.read_text(encoding="utf-8"))
    assert len(values) == 1, (
        f"expected exactly one EXPECTED_TEST_COUNT= pin in {_CI_YML}, found {values}"
    )
    return int(values[0])


def _cited_counts(relpath: str) -> list[int]:
    """Every collected-test figure cited in `relpath`."""
    text = (_REPO / relpath).read_text(encoding="utf-8")
    return [int(n) for n in _CITATION_RE.findall(text)]


def _collected_count(test_relpath: str) -> int:
    """The number of tests pytest collects from `test_relpath`."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", test_relpath, "--collect-only", "-q"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    counts = _COLLECTED_RE.findall(proc.stdout)
    assert counts, (
        f"could not read a collected count for {test_relpath} "
        f"(pytest exit {proc.returncode}):\n{proc.stdout[-2000:]}"
    )
    return int(counts[-1])


@pytest.mark.parametrize("relpath", _CITATION_DOCS)
def test_cited_collected_count_matches_expected_test_count(relpath):
    expected = _expected_test_count()
    cited = _cited_counts(relpath)
    assert cited, (
        f"{relpath} no longer cites a collected-test count "
        f'(expected "({expected} collected; ...)"); a deleted citation is drift'
    )
    assert cited == [expected], (
        f"{relpath} cites {cited}, EXPECTED_TEST_COUNT in ci.yml is {expected}"
    )


def test_per_file_collected_counts_match_collection():
    # Docs that quote the count of a single test file must quote the count
    # pytest actually collects from it -- the figure moves whenever that file
    # gains or loses a test, and nothing else would notice.
    for relpath, test_relpath in _PER_FILE_CITATIONS.items():
        text = (_REPO / relpath).read_text(encoding="utf-8")
        cited = {path: int(n) for path, n in _PER_FILE_RE.findall(text)}
        assert cited, (
            f"{relpath} no longer cites a per-file collected count "
            f'(expected "`{test_relpath}` (N collected; ...)"); '
            "a deleted citation is drift"
        )
        assert test_relpath in cited, (
            f"{relpath} cites per-file counts {sorted(cited)}, "
            f"expected one for {test_relpath}"
        )
        actual = _collected_count(test_relpath)
        assert cited[test_relpath] == actual, (
            f"{relpath} says {test_relpath} has {cited[test_relpath]} collected tests, "
            f"pytest collects {actual}"
        )


def test_guard_invariants():
    # The pin the guard reads must be exactly one parseable line, and positive.
    expected = _expected_test_count()
    assert expected > 0, f"EXPECTED_TEST_COUNT in {_CI_YML} is not a positive count"
    # docs/ci.md repeats the canonical figure in the Behaviour section of the
    # test-count gate description; it must agree with ci.yml too.
    text = (_REPO / "docs" / "ci.md").read_text(encoding="utf-8")
    stated = [int(n) for n in _BEHAVIOR_RE.findall(text)]
    assert stated, (
        "docs/ci.md lost its 'EXPECTED_TEST_COUNT (N; ...)' Behaviour figure; "
        "a deleted citation is drift"
    )
    assert stated == [expected], (
        f"docs/ci.md Behaviour section says {stated}, "
        f"EXPECTED_TEST_COUNT in ci.yml is {expected}"
    )
    # CONTRACT.md quotes a stale figure on purpose (dated draft history),
    # CHANGELOG.md records past counts and QUALITY-SCORE.md is a dated
    # snapshot; flagging them would be a false positive.
    assert not set(_CITATION_DOCS) & set(_HISTORICAL_DOCS)
    for relpath in _HISTORICAL_DOCS:
        assert relpath not in _CITATION_DOCS
    for relpath in _PER_FILE_CITATIONS:
        assert relpath not in _HISTORICAL_DOCS
