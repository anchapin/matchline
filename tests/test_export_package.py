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
    # the PDF is pure Python now, so every run (CI included) must produce it, on one page
    assert man["pdf"] == {"produced": True}
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(pkg / "trust_report.pdf"))
    assert len(doc) == 1
    text = doc[0].get_textpage().get_text_range()
    assert "Trust report" in text and "qualified" in text


def test_input_files_are_hashed(tmp_path):
    f = tmp_path / "set.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    (row,) = E._inputs([{"role": "drawing set", "path": str(f)}])
    assert row["sha256"] == hashlib.sha256(b"%PDF-1.4 test").hexdigest() and row["bytes"] == 13
    (gone,) = E._inputs([{"role": "drawing set", "path": str(tmp_path / "nope.pdf")}])
    assert gone["missing"] and gone["sha256"] is None


def test_numbers_display_rounded_but_json_keeps_full_value():
    cases = {
        -1.1822945666209127e-14: "0",
        0.0: "0",
        1.0: "1",
        361.6746126910826: "361.67",
        12345.678: "12,346",
        0.0123456: "0.0123",
        0.418: "0.418",
        2.50: "2.5",
        34: "34",
        12000: "12,000",
        None: "",
        "5A": "5A",
    }
    for v, shown in cases.items():
        assert E.fmt(v) == shown, (v, E.fmt(v))
    n = E._num("area", 361.6746126910826, "stage_02_model.json x")
    assert E._val(n) == "361.67" and n["value"] == 361.6746126910826
