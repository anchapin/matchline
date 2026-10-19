"""Regenerate golden files under tests/goldens. Run from the repo root:

python -m tests.make_goldens
(and eyeball the diff before committing -- a golden change means the
pipeline changed behavior.)

Golden files, each rebuilt from a pinned fixture:

- ``clean_report.json`` / ``defective_report.json``: the validate battery's
  ValidationReport JSON for a clean model and a three-defect model.
- ``clean_model.gbxml``: the gbXML ``bem_export.write_gbxml`` writes for a
  pinned single-space model. The export appends a summary note to
  ``model.notes``, so the builder (and the test) must build a FRESH model
  every time; a reused model would show its own first export's note in the
  second export's header comment.
- ``review_decisions.json``: the decisions file ``review_report.write_review``
  writes for a clean model with one open review item (schema, model hash,
  automatic states). Timestamp-free by construction.
- ``package_manifest.json``: the export package's manifest, after
  :func:`scrub_package` neutralizes the legitimately timestamped fields and
  re-hashes the scrubbed files.
- ``schedule_score.json``: ``scripts.schedule_score.score_schedule_rows``
  over pinned truth/prediction rows covering a full match, a crossed row,
  a missing tag, an extra tag and a duplicate tag.

Volatility whitelist (anything that depends on when or where the package
was built is scrubbed to ``@SCRUBBED`` before hashing, so a rerun in any
environment stays byte-stable):

- ``export_package`` writes ``generated_at`` (wall clock) into
  ``trust_report.json`` (signoff block) and embeds the same string in the
  HTML renders.
- the trust report's ``tool_version`` comes from the local install and the
  HTML renders print it ("matchline X.Y.Z"); it varies across checkouts.
- ``prepared_by`` / ``prepared_date`` are whatever the builder's
  environment supplies.
- correction-history and feedback events carry ``at`` timestamps (not
  present in the pinned package, which has neither file, but the scrubber
  catches them anyway if a future builder adds one).

A new timestamped or environment-dependent field must land in
``VOLATILE_KEYS`` (or the ISO regex must cover it), or the golden goes red
on the next run for the wrong reason.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import tempfile
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

_SCRUB_TOKEN = "@SCRUBBED"
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: Keys whose values depend on the environment that built the package
#: (wall-clock stamps and provenance read from the local install), not on
#: the model. The golden scrubber replaces them with ``@SCRUBBED`` so a
#: rerun in any environment stays byte-stable.
VOLATILE_KEYS = frozenset({"tool_version", "prepared_by", "prepared_date", "generated_at"})


def _scrub_obj(obj):
    """Replace timestamped and environment-dependent values, recursively."""
    if isinstance(obj, dict):
        return {
            k: _scrub_obj(v) if k not in VOLATILE_KEYS else _SCRUB_TOKEN for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    if isinstance(obj, str) and _ISO_TS.fullmatch(obj):
        return _SCRUB_TOKEN
    return obj


def scrub_package(pkg: Path) -> dict:
    """Neutralize timestamped fields in a built package, then re-hash.

    Rebuilds ``manifest.json``'s ``files`` digests over the scrubbed files so
    the manifest describes exactly what is on disk. Everything else in the
    manifest (schema, pdf verdict) is kept from the real build. Returns the
    manifest as written.
    """
    man = json.loads((pkg / "manifest.json").read_text())
    for p in sorted(pkg.rglob("*")):
        if not p.is_file() or p.name == "manifest.json":
            continue
        if p.suffix == ".json":
            p.write_text(json.dumps(_scrub_obj(json.loads(p.read_text())), indent=2) + "\n")
        else:
            text = _ISO_TS.sub(_SCRUB_TOKEN, p.read_text())
            # the HTML renders embed the tool version ("matchline X.Y.Z"),
            # which comes from the local install
            text = re.sub(r"matchline \d+\.\d+\.\d+", f"matchline {_SCRUB_TOKEN}", text)
            p.write_text(text)
    man["files"] = {
        p.relative_to(pkg).as_posix(): _sha256(p)
        for p in sorted(pkg.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    (pkg / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    return man


def _pinned_bem_model():
    from bem_export import BEMModel, BEMOpeningUnit, BEMSpace

    space = BEMSpace(
        sid="L1-101",
        name="Office 101",
        number="101",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        area_m2=100.0,
        volume_m3=300.0,
        lighting_w=0.0,
    )
    opening = BEMOpeningUnit(category="window", tag="A", width_m=1.2, height_m=1.5)
    return BEMModel(
        building_name="Golden Building",
        spaces=[space],
        openings=[opening],
        ring_m=[(0, 0), (20, 0), (20, 20), (0, 20)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.1,
        skipped_openings=[],
        notes=[],
        zones=[],
    )


def golden_gbxml() -> bytes:
    """The gbXML bytes for the pinned BEM model; fresh model every call."""
    import bem_export

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "clean_model.gbxml"
        bem_export.write_gbxml(_pinned_bem_model(), path)
        return path.read_bytes()


def _pinned_review_model():
    from building_model import Provenance, ReviewItem

    model = make_clean_model()
    model.review_queue.append(
        ReviewItem(
            id="rq-001",
            kind="door_height",
            description="Door height on sheet A101 needs a check",
            status="open",
            confidence=0.5,
            provenance=Provenance(sheet_id="A101", revision="", method="test", confidence=1.0),
            needs_review=True,
            acknowledged=False,
        )
    )
    return model


def golden_review_decisions() -> bytes:
    """The decisions file write_review emits for the pinned review model."""
    import review_report

    with tempfile.TemporaryDirectory() as td:
        d = review_report.write_review(Path(td), _pinned_review_model())
        return (d / "decisions.json").read_bytes()


def golden_package_manifest() -> bytes:
    """The scrubbed manifest of a package built from a clean model."""
    import export_package

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        model = make_clean_model()
        export_package.build_package(out_dir, model, run_checks(model), {}, [], pdf=False)
        scrub_package(out_dir / "package")
        return (out_dir / "package" / "manifest.json").read_bytes()


_GOLDEN_SCHEDULE_TRUTH = [
    {
        "tag": "AHU-1",
        "fields": {
            "system_type": "AHU",
            "service_area": "L1",
            "capacity": {"value": 12000, "unit": "cfm"},
        },
    },
    {
        "tag": "RTU-2",
        "fields": {
            "system_type": "RTU",
            "service_area": "L2",
            "capacity": {"value": 8000, "unit": "cfm"},
        },
    },
    {
        "tag": "FCU-3",
        "fields": {
            "system_type": "FCU",
            "service_area": "L1",
            "capacity": {"value": 400, "unit": "cfm"},
        },
    },
]

_GOLDEN_SCHEDULE_PRED = [
    {
        "tag": "AHU-1",
        "fields": {
            "system_type": "AHU",
            "service_area": "L1",
            "capacity": {"value": 12000, "unit": "cfm"},
        },
    },
    # crossed: same tag, capacity wrong -> grouped miss, flat partial credit
    {
        "tag": "RTU-2",
        "fields": {
            "system_type": "RTU",
            "service_area": "L2",
            "capacity": {"value": 9000, "unit": "cfm"},
        },
    },
    # missing: no prediction for FCU-3; extra: prediction for an unknown tag
    {
        "tag": "MAU-9",
        "fields": {
            "system_type": "MAU",
            "service_area": "L3",
            "capacity": {"value": 2000, "unit": "cfm"},
        },
    },
    # duplicate identity on the prediction side: reported, never matched
    {
        "tag": "AHU-1",
        "fields": {
            "system_type": "AHU",
            "service_area": "L4",
            "capacity": {"value": 11000, "unit": "cfm"},
        },
    },
]


def golden_schedule_score() -> bytes:
    """The grouped/flat/KR/EM/VF1 score over the pinned row fixture."""

    from scripts.schedule_score import score_schedule_rows

    score = score_schedule_rows(_GOLDEN_SCHEDULE_PRED, _GOLDEN_SCHEDULE_TRUTH)
    return (json.dumps(dataclasses.asdict(score), indent=2) + "\n").encode()


def main():
    clean = run_checks(make_clean_model())
    m = make_clean_model()
    break_area(m)
    break_lpd_absurd(m)
    break_fixture_no_schedule_flagged(m)
    defective = run_checks(m)
    builders = {
        "clean_report.json": lambda: (clean.to_json() + "\n").encode(),
        "defective_report.json": lambda: (defective.to_json() + "\n").encode(),
        "clean_model.gbxml": golden_gbxml,
        "review_decisions.json": golden_review_decisions,
        "package_manifest.json": golden_package_manifest,
        "schedule_score.json": golden_schedule_score,
    }
    checksums: dict[str, str] = {}
    for name, build in builders.items():
        p = GOLDEN_DIR / name
        p.write_bytes(build())
        checksums[name] = _sha256(p)
        print(f"wrote {p} ({len(p.read_bytes())} bytes)")
    sums_path = GOLDEN_DIR / "SHA256SUMS.json"
    sums_path.write_text(json.dumps(checksums, indent=2) + "\n")
    print(f"wrote {sums_path} ({len(checksums)} entries)")


if __name__ == "__main__":
    main()
