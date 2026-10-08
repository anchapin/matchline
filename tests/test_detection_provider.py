"""Detection provider chosen by config, license class from the ledger (#743)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent))

import detection_provider as dp  # noqa: E402

YOLO_RUN = "/home/x/workspace/datasets/detector_runs/yolo11n_baseline_s0/weights/best.pt"


def _preds(tmp_path, stem, preds, w=1728, h=1152):
    d = tmp_path / "dets"
    d.mkdir(exist_ok=True)
    (d / f"{stem}.json").write_text(
        json.dumps({"image": f"{stem}.png", "width": w, "height": h, "preds": preds})
    )
    return d


def test_ledger_classes_cubicasa_weights_as_eval_only():
    assert dp.license_class(YOLO_RUN) == "noncommercial"
    assert dp.license_class("review_classifier/trained_model_route_to_review.npz") == "permissive"
    assert dp.license_class("somewhere/new.pt") == "unknown"


def test_provider_is_chosen_by_config(tmp_path):
    d = _preds(tmp_path, "sheet_001", [])
    p = dp.provider_from_config({"provider": "precomputed", "dir": str(d)})
    assert isinstance(p, dp.PrecomputedProvider)
    cfg = tmp_path / "det.json"
    cfg.write_text(json.dumps({"provider": "yolo_sahi", "weights": YOLO_RUN}))
    y = dp.provider_from_config(cfg)
    assert isinstance(y, dp.YoloSahiProvider)
    assert y.info.license_class == "noncommercial" and y.info.eval_only
    assert isinstance(dp.provider_from_config(None), dp.NoneProvider)


def test_bad_config_is_refused(tmp_path):
    with pytest.raises(dp.ProviderConfigError, match="not one of"):
        dp.provider_from_config({"provider": "kreo"})
    with pytest.raises(dp.ProviderConfigError, match="needs weights"):
        dp.provider_from_config({"provider": "yolo_sahi"})


def test_release_refuses_eval_only_and_unlisted_weights(tmp_path):
    with pytest.raises(dp.ProviderLicenseError, match="noncommercial"):
        dp.provider_from_config({"provider": "yolo_sahi", "weights": YOLO_RUN}, release=True)
    d = _preds(tmp_path, "sheet_001", [])
    with pytest.raises(dp.ProviderLicenseError, match="does not list it"):
        dp.provider_from_config({"provider": "precomputed", "dir": str(d)}, release=True)
    # a provider whose artifact the ledger lists as permissive may ship
    ok = dp.provider_from_config(
        {
            "provider": "precomputed",
            "dir": str(d),
            "artifact": "review_classifier/trained_model_route_to_review.npz",
        },
        release=True,
    )
    assert not ok.info.eval_only


def test_precomputed_provider_returns_typed_detections(tmp_path):
    d = _preds(
        tmp_path,
        "sheet_001",
        [
            {"cls": 0, "conf": 0.9, "x0": 10, "y0": 10, "x1": 40, "y1": 20},
            {"cls": 1, "conf": 0.7, "x0": 50, "y0": 5, "x1": 90, "y1": 12},
        ],
    )
    p = dp.provider_from_config({"provider": "precomputed", "dir": str(d), "artifact": YOLO_RUN})
    dets = p.detect(tmp_path / "sheet_001.png", "set.pdf:sheet_001.json")
    assert [x.label for x in dets] == ["door", "window"]
    assert dets[0].bbox == (10, 10, 40, 20) and dets[1].score == 0.7
    assert dets[0].source == "set.pdf:sheet_001.json"
    assert p.detect(tmp_path / "sheet_009.png", "x") == []


def test_set_report_records_the_provider_and_eval_only(tmp_path):
    from test_real_set import _arch, _set

    import real_set

    pdf = _set(tmp_path, [_arch("A-101", "FIRST FLOOR PLAN", 1)])
    d = _preds(tmp_path, "sheet_001", [{"cls": 0, "conf": 0.8, "x0": 1, "y0": 1, "x1": 9, "y1": 9}])
    prov = dp.provider_from_config({"provider": "precomputed", "dir": str(d), "artifact": YOLO_RUN})
    _, rep = real_set.build_set_model(pdf, tmp_path / "out", provider=prov)
    r = rep.to_dict()
    assert r["detector"]["provider"] == "precomputed" and r["detector"]["eval_only"]
    sym = r["sheets"][0]["stages"]["symbols"]
    assert sym["status"] == "ok" and sym["n"] == 1 and sym["eval_only"]
    assert any("evaluation only" in n for n in r["notes"])
    # no provider: the stage still says so plainly
    _, rep0 = real_set.build_set_model(pdf, tmp_path / "out0")
    assert rep0.to_dict()["detector"] == {}
    assert rep0.to_dict()["sheets"][0]["stages"]["symbols"]["status"] == "skipped"
