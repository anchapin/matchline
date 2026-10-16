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
    assert "--apply decisions.json" in page  # how to replay the review decisions (#749)


def test_package_carries_the_review_report_and_decisions_template(pkg):
    tr = json.loads((pkg / "trust_report.json").read_text())
    assert tr["decisions_file"]["available"] is True
    for name in ("review.html", "decisions.json", "model.json"):
        assert (pkg / "review" / name).exists()
    man = json.loads((pkg / "manifest.json").read_text())
    assert "review/decisions.json" in man["files"]
    doc = json.loads((pkg / "review" / "decisions.json").read_text())
    model_sha = hashlib.sha256((pkg / "review" / "model.json").read_bytes()).hexdigest()
    assert doc["model_sha256"] == model_sha and doc["decisions"] == []


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


def test_signoff_block_is_dated_and_left_blank_for_the_professional(pkg):
    """#976: the report carries a dated sign-off block matchline never signs."""
    from datetime import datetime

    tr = json.loads((pkg / "trust_report.json").read_text())
    so = tr["signoff"]
    assert datetime.fromisoformat(so["generated_at"]).tzinfo is not None  # dated, UTC
    assert so["prepared_by"] == "" and so["reviewed_by"] == "" and so["stamp"] == ""
    page = (pkg / "trust_report.html").read_text()
    assert "Sign-off" in page and "Prepared by" in page and "stamp / seal" in page
    assert so["generated_at"] in page  # the HTML shows the same timestamp


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


# --- zip, share-back preview and contents listing (#999) ---------------------


def test_package_contents_are_listed_in_the_trust_report(pkg):
    tr = json.loads((pkg / "trust_report.json").read_text())
    man = json.loads((pkg / "manifest.json").read_text())
    listed = {c["path"] for c in tr["package_contents"]}
    assert listed == set(man["files"]) | {
        "manifest.json"
    }  # the manifest lists every file but itself
    for c in tr["package_contents"]:
        if c.get("generated"):
            assert c["sha256"] is None
        else:
            f = pkg / c["path"]
            assert c["bytes"] == f.stat().st_size
            assert c["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    by_path = {c["path"]: c for c in tr["package_contents"]}
    assert by_path["trust_report.html"]["generated"] is True
    assert by_path["review/decisions.json"]["sha256"]


def test_contents_section_renders_in_html_and_pdf(pkg):
    page = (pkg / "trust_report.html").read_text()
    assert "<h2>Package contents</h2>" in page
    assert "hashed in manifest.json" in page  # the report itself is hashed there
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(pkg / "trust_report.pdf"))
    text = doc[0].get_textpage().get_text_range()
    assert "Package contents" in text and "share_back.html" in text


def test_share_back_preview_is_one_selfcontained_page(pkg):
    page = (pkg / "share_back.html").read_text()
    assert "Share-back preview" in page and "Package contents" in page
    # #1001: the 3D review viewer rides on the share-back page, inline only
    assert "<canvas" in page
    assert not re.search(r"<script[^>]*\bsrc\s*=", page)
    assert not re.search(r'(src|href)\s*=\s*["\']?https?:', page)


def test_run_builds_the_one_zip(pkg):
    import zipfile

    zip_path = pkg.parent / "package.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert {
        "trust_report.html",
        "trust_report.pdf",
        "share_back.html",
        "review/decisions.json",
        "DISCLAIMER.txt",
        "manifest.json",
    } <= names
    assert any(n.endswith(".xml") for n in names) and any(n.endswith(".ifc") for n in names)


def test_cli_export_package_verifies_and_zips(pkg, tmp_path, capsys):
    from cli import cmd_export_package

    z = cmd_export_package(argparse.Namespace(out_dir=str(pkg.parent)))
    assert z.name == "package.zip" and z.exists()
    # a tampered file must fail the manifest check and exit 1
    import shutil

    other = tmp_path / "out"
    shutil.copytree(pkg.parent, other)
    (other / "package" / "DISCLAIMER.txt").write_text("tampered\n")
    with pytest.raises(SystemExit):
        cmd_export_package(argparse.Namespace(out_dir=str(other)))


def test_build_package_refuses_when_the_gate_has_not_acknowledged(tmp_path):
    from building_model import BuildingModel, Provenance, ReviewItem
    from validate.types import ValidationReport

    m = BuildingModel(name="snap999")
    prov = Provenance(sheet_id="A-1", revision=1, method="plan", confidence=0.9)
    m.review_queue.append(
        ReviewItem(
            id="R1",
            kind="space_merge",
            description="undecided",
            confidence=0.9,
            provenance=prov,
            urgency=1,
        )
    )
    rep = ValidationReport(building_name="snap999", results=[])
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        E.build_package(out, m, rep, {}, [], require_gate=True)
    assert not (out / "package").exists()
    pkg = E.build_package(out, m, rep, {}, [], require_gate=False)
    assert (pkg / "share_back.html").exists()
