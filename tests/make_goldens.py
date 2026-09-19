"""Regenerate golden ValidationReport JSON files. Run from the repo root:

python -m tests.make_goldens
"""

from pathlib import Path

from tests.model_factory import (
    break_area,
    break_fixture_no_schedule_flagged,
    break_lpd_absurd,
    make_clean_model,
)
from validate import run_checks

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"
GOLDEN_DIR.mkdir(exist_ok=True)


def main():
    clean = run_checks(make_clean_model())
    m = make_clean_model()
    break_area(m)
    break_lpd_absurd(m)
    break_fixture_no_schedule_flagged(m)
    defective = run_checks(m)
    for name, report in (("clean_report.json", clean), ("defective_report.json", defective)):
        p = GOLDEN_DIR / name
        p.write_text(report.to_json() + "\n")
        print(f"wrote {p} ({report.to_dict()['summary']})")


if __name__ == "__main__":
    main()
