"""Drift guard for the collected-test-count figures cited in the docs (#1359).

`.github/workflows/ci.yml` pins `EXPECTED_TEST_COUNT` and that pin is the
canonical figure. Two orientation docs quote the same number in prose
(`AGENTS.md` Verify section and `docs/ci.md`), and `docs/ci.md` repeats it in
the Behaviour section of the test-count gate description. Those prose figures
used to sit at a stale value (3752) while the suite was well past it, and
nothing failed: the drift was invisible until CI printed both numbers side by
side.

This module follows the doc-count sync pattern of `tests/test_validate.py`
(the `_COUNT_DOCS` assertions that pin prose counts to a code constant): the
prose figures are parsed out of the docs and asserted against the canonical
constant read from `ci.yml`. A missing or unparseable citation is drift too,
so it fails the same way a wrong number does.

Historical documents that mention a stale figure on purpose (`CONTRACT.md`'s
dated draft history, `CHANGELOG.md`) are deliberately out of scope: the guard
never reads them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_CI_YML = _REPO / ".github" / "workflows" / "ci.yml"

# Docs that cite the collected-test count in their Verify section.
_CITATION_DOCS = (
    "AGENTS.md",
    "docs/ci.md",
)

# Historical documents that may quote an outdated figure on purpose. The guard
# must never read them -- CONTRACT.md's stale mention is dated draft history.
_HISTORICAL_DOCS = (
    "CONTRACT.md",
    "CHANGELOG.md",
)

# "(3752 collected; ...)" -- the Verify-section citation.
_CITATION_RE = re.compile(r"\((\d+) collected")

# "`EXPECTED_TEST_COUNT` (3951; ...)" -- the docs/ci.md Behaviour section.
_BEHAVIOR_RE = re.compile(r"EXPECTED_TEST_COUNT`? \((\d+);")

# "EXPECTED_TEST_COUNT=4143  # ..." -- the canonical pin in ci.yml.
_PIN_RE = re.compile(r"^\s*EXPECTED_TEST_COUNT=(\d+)\b", re.M)


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


def test_ci_yml_pins_the_canonical_test_count():
    # The guard is only as good as the pin it reads, so the pin itself is
    # asserted: exactly one parseable EXPECTED_TEST_COUNT= line in ci.yml.
    assert _expected_test_count() > 0


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


def test_docs_ci_behavior_figure_matches_expected_test_count():
    # docs/ci.md repeats the canonical figure in the Behaviour section of the
    # test-count gate description; it must agree with ci.yml too.
    expected = _expected_test_count()
    text = (_REPO / "docs" / "ci.md").read_text(encoding="utf-8")
    stated = [int(n) for n in _BEHAVIOR_RE.findall(text)]
    assert stated, (
        "docs/ci.md lost its 'EXPECTED_TEST_COUNT (N; ...)' Behaviour figure; "
        "a deleted citation is drift"
    )
    assert stated == [expected], (
        f"docs/ci.md Behaviour section says {stated}, EXPECTED_TEST_COUNT in ci.yml is {expected}"
    )


def test_guard_ignores_historical_documents():
    # CONTRACT.md quotes a stale figure on purpose (dated draft history) and
    # CHANGELOG.md records past counts; flagging them would be a false positive.
    assert not set(_CITATION_DOCS) & set(_HISTORICAL_DOCS)
    for relpath in _HISTORICAL_DOCS:
        assert relpath not in _CITATION_DOCS
