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


# --- artifact goldens (#1127) -------------------------------------------------
# Each artifact golden is rebuilt from its pinned fixture and compared
# byte-for-byte; the volatility whitelist in tests/make_goldens.py scrubs
# legitimately timestamped fields before hashing.


def test_golden_gbxml():
    from tests.make_goldens import golden_gbxml

    _verify_golden("clean_model.gbxml")
    assert golden_gbxml() == (GOLDEN_DIR / "clean_model.gbxml").read_bytes()


def test_golden_review_decisions():
    from tests.make_goldens import golden_review_decisions

    _verify_golden("review_decisions.json")
    assert golden_review_decisions() == (GOLDEN_DIR / "review_decisions.json").read_bytes()


def test_golden_schedule_score():
    from tests.make_goldens import golden_schedule_score

    _verify_golden("schedule_score.json")
    assert golden_schedule_score() == (GOLDEN_DIR / "schedule_score.json").read_bytes()


def test_golden_package_manifest():
    """Manifest golden: byte-stable after scrubbing, digests verify on disk."""
    import json as _json
    import tempfile
    from pathlib import Path as _Path

    from tests.make_goldens import golden_package_manifest, scrub_package
    from tests.make_goldens import make_clean_model as _mcm
    from tests.make_goldens import run_checks as _rc

    _verify_golden("package_manifest.json")
    assert golden_package_manifest() == (GOLDEN_DIR / "package_manifest.json").read_bytes()
    # the scrubber must actually fire: the real build stamps generated_at
    with tempfile.TemporaryDirectory() as td:
        out_dir = _Path(td)
        model = _mcm()
        import export_package

        export_package.build_package(out_dir, model, _rc(model), {}, [], pdf=False)
        pkg = out_dir / "package"
        raw = (pkg / "trust_report.json").read_text()
        assert "@SCRUBBED" not in raw, "scrub token leaked into the raw build"
        scrub_package(pkg)
        assert "@SCRUBBED" in (pkg / "trust_report.json").read_text(), "scrubber matched nothing"
        man = _json.loads((pkg / "manifest.json").read_text())
        for name, digest in man["files"].items():
            f = pkg / name
            assert f.exists(), f"manifest lists missing file {name}"
            import hashlib

            assert hashlib.sha256(f.read_bytes()).hexdigest() == digest, f"digest drift on {name}"


def test_golden_model_json():
    """Canonical-model golden: pins the versioned wrapper and field shape."""
    import json as _json

    from building_model import MODEL_VERSION
    from tests.make_goldens import golden_model_json

    _verify_golden("model.json")
    assert golden_model_json() == (GOLDEN_DIR / "model.json").read_bytes()
    doc = _json.loads((GOLDEN_DIR / "model.json").read_text())
    assert doc["model_version"] == MODEL_VERSION
    assert set(doc) == {"model_version", "model"}


def test_golden_telemetry():
    """Telemetry golden: pins spec_version and the five metric groups."""
    import json as _json

    from telemetry_emitter import SPEC_VERSION
    from tests.make_goldens import golden_telemetry

    _verify_golden("telemetry.json")
    assert golden_telemetry() == (GOLDEN_DIR / "telemetry.json").read_bytes()
    doc = _json.loads((GOLDEN_DIR / "telemetry.json").read_text())
    assert doc["spec_version"] == SPEC_VERSION
    assert set(doc) == {
        "spec_version",
        "consent",
        "conf_distributions",
        "corrections_by_type",
        "check_failures",
        "binned_aggregates",
        "stage_outcomes",
    }
