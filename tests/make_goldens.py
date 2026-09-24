"""Regenerate golden ValidationReport JSON files. Run from the repo root:

python -m tests.make_goldens
"""

import hashlib
import json
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    clean = run_checks(make_clean_model())
    m = make_clean_model()
    break_area(m)
    break_lpd_absurd(m)
    break_fixture_no_schedule_flagged(m)
    defective = run_checks(m)
    checksums: dict[str, str] = {}
    for name, report in (("clean_report.json", clean), ("defective_report.json", defective)):
        p = GOLDEN_DIR / name
        p.write_text(report.to_json() + "\n")
        checksums[name] = _sha256(p)
        print(f"wrote {p} ({report.to_dict()['summary']})")
    sums_path = GOLDEN_DIR / "SHA256SUMS.json"
    sums_path.write_text(json.dumps(checksums, indent=2) + "\n")
    print(f"wrote {sums_path} ({len(checksums)} entries)")


if __name__ == "__main__":
    main()
