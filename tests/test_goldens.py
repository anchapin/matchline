"""Golden regression tests: ValidationReport JSON is pinned.

Regenerate with:  python -m tests.make_goldens
(then eyeball the diff before committing -- a golden change means the
pipeline or the battery changed behavior.)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate import run_checks  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"


def _load(name):
    with open(GOLDEN_DIR / name) as f:
        return json.load(f)


def test_golden_clean():
    from model_factory import make_clean_model
    report = run_checks(make_clean_model())
    assert report.to_dict() == _load("clean_report.json")


def test_golden_defective():
    from model_factory import (make_clean_model, break_area,
                               break_lpd_absurd,
                               break_fixture_no_schedule_flagged)
    m = make_clean_model()
    break_area(m)
    break_lpd_absurd(m)
    break_fixture_no_schedule_flagged(m)
    report = run_checks(m)
    assert report.to_dict() == _load("defective_report.json")
