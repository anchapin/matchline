"""Differential oracle for the two simplifiers (#1393).

A golden I/O snapshot harness over the CURRENT behaviour of
``geometry/geometry_simplify.py`` (envelope / footprint) and
``geometry/roof_simplify.py`` (roof). A fixed corpus of inputs lives in
``tests/simplifier_oracle_corpus.py``; the expected outputs are RECORDED
from the current simplifier (``python -m tests.make_simplifier_oracle``)
and committed as ``tests/fixtures/simplifier_corpus/expected.json``.

What the oracle is for: a refactor of either simplifier has to keep
producing the same geometry. The corpus covers both simplifiers over empty,
degenerate, small, typical and high-vertex-count inputs, so a behaviour
change shows up as a failing case with the exact path that moved instead of
as a silently different envelope.

TOLERANCE
---------
The comparison is NOT byte equality: floats make that brittle and it would
punish harmless re-ordering of a sum. Numbers are compared with

    math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL)

with ``REL_TOL = 1e-9`` and ``ABS_TOL = 1e-6``. In practice the absolute
term dominates for lengths (1 micrometre), areas (1e-6 m2) and percentage
points, and the relative term covers the large accumulated areas. Counts,
strings, booleans and the structure of the audit trail (provenance, skipped
operations) are compared exactly -- they are integers and text, not floats,
so there is nothing to be lenient about.

This tolerance is the ORACLE's tolerance and is unrelated to the standing
1-5% envelope-area gate of the simplifier itself: that gate decides which
simplification operations may be applied, this one decides whether two runs
of the simplifier agree with each other.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tests.simplifier_oracle_corpus import (
    CORPUS,
    REQUIRED_TAGS,
    corpus_inputs,
    run_case,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "simplifier_corpus"
CASES_PATH = FIXTURES / "cases.json"
EXPECTED_PATH = FIXTURES / "expected.json"

#: Relative tolerance for the numeric comparison (see the module docstring).
REL_TOL = 1e-9
#: Absolute tolerance for the numeric comparison (lengths, areas, percents).
ABS_TOL = 1e-6

_BY_ID = {case.cid: case for case in CORPUS}


def _load_expected() -> dict:
    assert EXPECTED_PATH.exists(), (
        f"missing {EXPECTED_PATH}; record it with `python -m tests.make_simplifier_oracle`"
    )
    data = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    assert data.get("schema") == 1, f"unknown expected-output schema: {data.get('schema')!r}"
    return data["cases"]


EXPECTED = _load_expected()


def _numbers_close(actual, expected) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    return math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _compare(expected, actual, path: str = "") -> list:
    """Every mismatch between the recorded and the fresh output, as strings."""
    if isinstance(expected, bool) or isinstance(expected, str) or expected is None:
        if expected == actual:
            return []
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)):
            return [f"{path}: expected a number, got {actual!r}"]
        if _numbers_close(actual, expected):
            return []
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected a list, got {actual!r}"]
        if len(expected) != len(actual):
            missing = [f"{path}[{i}]: missing" for i in range(len(actual), len(expected))]
            return [
                f"{path}: expected {len(expected)} items, got {len(actual)}",
                *missing,
            ]
        out = []
        for i, (exp, act) in enumerate(zip(expected, actual)):
            out.extend(_compare(exp, act, f"{path}[{i}]"))
        return out
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected a mapping, got {actual!r}"]
        out = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                out.append(f"{path}.{key}: unexpected key (value {actual[key]!r})")
            elif key not in actual:
                out.append(f"{path}.{key}: missing key (expected {expected[key]!r})")
            else:
                out.extend(_compare(expected[key], actual[key], f"{path}.{key}"))
        return out
    if expected == actual:
        return []
    return [f"{path}: expected {expected!r}, got {actual!r}"]


@pytest.mark.parametrize("case", CORPUS, ids=[case.cid for case in CORPUS])
def test_simplifier_matches_the_recorded_oracle(case) -> None:
    """Every corpus case still produces the recorded simplifier output."""
    assert case.cid in EXPECTED, (
        f"corpus case {case.cid} has no recorded output; "
        "re-record with `python -m tests.make_simplifier_oracle`"
    )
    actual = run_case(case)
    mismatches = _compare(EXPECTED[case.cid], actual, path=case.cid)
    assert not mismatches, (
        "the simplifier output changed for corpus case "
        f"{case.cid} (rel_tol={REL_TOL}, abs_tol={ABS_TOL}):\n  " + "\n  ".join(mismatches[:20])
    )


def test_oracle_covers_both_simplifiers_and_every_size_class() -> None:
    """The corpus spans both simplifiers and every documented input class."""
    kinds = {case.kind for case in CORPUS}
    assert kinds == {"ring", "multipart", "roof"}, f"corpus kinds are {sorted(kinds)}"
    seen = {tag for case in CORPUS for tag in case.tags}
    missing = [tag for tag in REQUIRED_TAGS if tag not in seen]
    assert not missing, f"corpus has no case tagged {missing}"
    assert len(CORPUS) >= 20, f"corpus is too small to be meaningful: {len(CORPUS)} cases"


def test_recorded_artefact_covers_every_corpus_case() -> None:
    """The recorded file holds exactly the corpus cases -- no stale entries."""
    assert set(EXPECTED) == set(_BY_ID), (
        "recorded outputs and corpus cases disagree: "
        f"missing {sorted(set(_BY_ID) - set(EXPECTED))}, "
        f"unknown {sorted(set(EXPECTED) - set(_BY_ID))}"
    )


def test_committed_input_artefact_matches_the_corpus() -> None:
    """The committed input artefact is the corpus, not a stale copy of it."""
    assert CASES_PATH.exists(), f"missing {CASES_PATH}"
    committed = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(corpus_inputs())), (
        "tests/fixtures/simplifier_corpus/cases.json is stale; "
        "re-record with `python -m tests.make_simplifier_oracle`"
    )
