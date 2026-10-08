"""Export package with the one-page trust report (#750), on the synthetic e2e run."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import pytest

import export_package as E
import run_pipeline


def _run(tmp_path: Path, **kw) -> Path:
    out = tmp_path / "out"
    args = argparse.Namespace(
        seed=101,
        image=None,
        aec_bench=None,
        detections=None,
        schedule_csv=None,
        weights=Path("weights.pt"),
        out_dir=out,
        open_office_span=False,
        elevation_key="elev_grid",
        simplify_tol=0.01,
        **kw,
    )
    run_pipeline.main(args, config=None)
    return out / "package"


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("pkg"), climate_zone="5A")


def test_package_holds_the_exports_report_and_disclaimer(pkg):
    names = {p.name for p in pkg.iterdir()}
    assert {"trust_report.html", "trust_report.json", "DISCLAIMER.txt", "manifest.json"} <= names
    assert {"convention_report.json"} <= names
    assert any(n.endswith(".xml") for n in names) and any(n.endswith(".ifc") for n in names)
    assert "not an engineering stamp" in (pkg / "DISCLAIMER.txt").read_text()


def test_every_number_names_its_source(pkg):
    tr = json.loads((pkg / "trust_report.json").read_text())
    nums = tr["extracted"] + tr["convention_biases"] + tr["validation"]["counts"]
    nums.append(tr["review"]["open"])
    assert nums
    for n in nums:
        assert n["source"], n
        assert n["value"] is not None or n.get("reason"), n
    assert tr["inputs"] == [
        {"role": "synthetic seed", "value": 101},
        {"role": "climate zone", "value": "5A"},
    ]


def test_defaults_used_are_listed_with_their_source(pkg):
    tr = json.loads((pkg / "trust_report.json").read_text())
    assert tr["defaults"], "climate zone 5A fills undrawn constructions from Table 5.5"
    for d in tr["defaults"]:
        assert d["method"] == "construction_default"
        assert "90.1" in d["source"] and d["where"].startswith("model.")


def test_html_is_one_offline_page_with_the_disclaimer(pkg):
    page = (pkg / "trust_report.html").read_text()
    assert E.DISCLAIMER in page
    assert not re.search(r'(src|href)\s*=\s*["\']?https?:', page)  # no external assets
    assert "<script" not in page
    assert "#749" in page  # the decisions file is named as not yet available, not invented


def test_manifest_hashes_match_the_files(pkg):
    man = json.loads((pkg / "manifest.json").read_text())
    for name, digest in man["files"].items():
        assert hashlib.sha256((pkg / name).read_bytes()).hexdigest() == digest
    assert man["pdf"]["produced"] or man["pdf"]["reason"]


def test_input_files_are_hashed(tmp_path):
    f = tmp_path / "set.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    (row,) = E._inputs([{"role": "drawing set", "path": str(f)}])
    assert row["sha256"] == hashlib.sha256(b"%PDF-1.4 test").hexdigest() and row["bytes"] == 13
    (gone,) = E._inputs([{"role": "drawing set", "path": str(tmp_path / "nope.pdf")}])
    assert gone["missing"] and gone["sha256"] is None
