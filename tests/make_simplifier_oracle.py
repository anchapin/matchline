"""Regenerate the simplifier oracle corpus artefacts (#1393).

The corpus INPUT side lives in ``tests/simplifier_oracle_corpus.py``; the
EXPECTED side is never hand-written. This script runs the CURRENT
simplifier over every corpus case and serialises the results:

* ``tests/fixtures/simplifier_corpus/cases.json`` -- the committed inputs,
* ``tests/fixtures/simplifier_corpus/expected.json`` -- the recorded
  outputs the harness compares against.

Run it from the repo root::

    python -m tests.make_simplifier_oracle

and eyeball the diff before committing: a changed expected output means the
simplifier changed behaviour, which is exactly what the oracle exists to
make visible. Re-recording is a deliberate act -- a refactor that is meant
to keep the same behaviour leaves both files untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.simplifier_oracle_corpus import CORPUS, corpus_inputs, run_case

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "simplifier_corpus"


def record() -> dict:
    """Run every corpus case and return the recorded outputs."""
    return {case.cid: run_case(case) for case in CORPUS}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cases_path = FIXTURES / "cases.json"
    expected_path = FIXTURES / "expected.json"

    cases_path.write_text(
        json.dumps(corpus_inputs(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    expected_path.write_text(
        json.dumps({"schema": 1, "cases": record()}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {cases_path} and {expected_path} ({len(CORPUS)} cases)")


if __name__ == "__main__":
    main()
