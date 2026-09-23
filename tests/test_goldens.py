"""Golden regression tests: ValidationReport JSON is pinned with SHA256 integrity.

Regenerate with:  python -m tests.make_goldens
(and eyeball the diff before committing -- a golden change means the
pipeline or the battery changed behavior.)
"""

import hashlib
import json
from pathlib import Path

import pytest

from validate import run_checks

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(name):
    with open(GOLDEN_DIR / name) as f:
        return json.load(f)


def _load_checksums():
    with open(GOLDEN_DIR / "SHA256SUMS.json") as f:
        return json.load(f)


def _verify_golden(name: str):
    """Verify SHA256 of a golden file before loading it."""
    checksums = _load_checksums()
    expected = checksums.get(name)
    if expected is None:
        pytest.fail(f"{name} has no SHA256 entry in SHA256SUMS.json")
    actual = _sha256(GOLDEN_DIR / name)
    if actual != expected:
        pytest.fail(
            f"{name} SHA256 mismatch (integrity check failed):\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            f"Regenerate with: python -m tests.make_goldens"
        )


def test_golden_clean():
    from tests.model_factory import make_clean_model

    _verify_golden("clean_report.json")
    report = run_checks(make_clean_model())
    assert report.to_dict() == _load("clean_report.json")


def test_golden_defective():
    from tests.model_factory import (
        break_area,
        break_fixture_no_schedule_flagged,
        break_lpd_absurd,
        make_clean_model,
    )

    _verify_golden("defective_report.json")
    m = make_clean_model()
    break_area(m)
    break_lpd_absurd(m)
    break_fixture_no_schedule_flagged(m)
    report = run_checks(m)
    assert report.to_dict() == _load("defective_report.json")
