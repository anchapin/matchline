"""License ledger and the release check (#751)."""

from __future__ import annotations

import copy
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_release_licenses", ROOT / "scripts" / "check_release_licenses.py"
)
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


@pytest.fixture(scope="module")
def ledger():
    return C.load()


def test_ledger_is_valid(ledger):
    assert C.validate(ledger) == []


def test_every_committed_artifact_is_in_the_ledger(ledger):
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert C.check_committed(ledger, C.committed_files()) == []


def test_unledgered_committed_artifact_is_caught(ledger):
    errs = C.check_committed(ledger, ["detector/new_weights.pt", "README.md"])
    assert errs == ["detector/new_weights.pt: committed artifact is not in license_ledger.json"]


def test_noncommercial_and_agpl_weights_are_evaluation_only(ledger):
    det = [a for a in ledger["artifacts"] if "detector_runs" in a["path"]]
    assert det and all(C.artifact_class(ledger, a) == "copyleft" for a in det)
    ds = {d["id"]: d["class"] for d in ledger["datasets"]}
    for nc in ("cubicasa5k", "aec_bench", "archcad", "floorplancad"):
        assert ds[nc] == "noncommercial"


def _wheel(tmp_path, members):
    p = tmp_path / "matchline-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(p, "w") as z:
        for m in members:
            z.writestr(m, "x")
    return p


def test_a_build_with_eval_only_weights_fails(tmp_path, ledger):
    ledger = copy.deepcopy(ledger)
    ledger["artifacts"].append(
        {"path": "detector/weights/door.pt", "kind": "weights", "datasets": ["cubicasa5k"]}
    )
    w = _wheel(tmp_path, ["cli.py", "detector/weights/door.pt"])
    errs = C.check_dist(ledger, w.name, C.dist_members(w))
    assert len(errs) == 1 and "evaluation-only (noncommercial" in errs[0]


def test_a_build_with_an_unknown_model_file_fails(tmp_path, ledger):
    w = _wheel(tmp_path, ["cli.py", "models/mystery.onnx"])
    errs = C.check_dist(ledger, w.name, C.dist_members(w))
    assert errs and "not in the ledger" in errs[0]


def test_share_alike_needs_the_notice_in_the_build(tmp_path, ledger):
    bare = _wheel(tmp_path, ["cli.py", "facade_priors.json"])
    assert "no NOTICE.md" in C.check_dist(ledger, bare.name, C.dist_members(bare))[0]
    (tmp_path / "ok").mkdir()
    ok = _wheel(tmp_path / "ok", ["cli.py", "facade_priors.json", "x.dist-info/NOTICE.md"])
    assert C.check_dist(ledger, ok.name, C.dist_members(ok)) == []


def test_facade_priors_share_alike_obligation_is_stated(ledger):
    a = next(a for a in ledger["artifacts"] if a["path"] == "facade_priors.json")
    assert C.artifact_class(ledger, a) == "share_alike"
    assert "CC BY-SA" in a["notice"] and "Tylecek" in a["notice"]
    notice = (ROOT / "NOTICE.md").read_text()
    assert "facade_priors.json" in notice and "CC BY-SA" in notice


def test_malformed_ledger_is_rejected(ledger):
    bad = copy.deepcopy(ledger)
    bad["artifacts"].append({"path": "x.npz", "datasets": ["no_such_dataset"]})
    bad["datasets"][0]["class"] = "public-ish"
    errs = C.validate(bad)
    assert any("unknown dataset 'no_such_dataset'" in e for e in errs)
    assert any("unknown class 'public-ish'" in e for e in errs)


def test_ledger_is_linked_from_datasets_and_releasing():
    for doc in ("DATASETS.md", "RELEASING.md"):
        assert "license_ledger" in (ROOT / doc).read_text(), doc
