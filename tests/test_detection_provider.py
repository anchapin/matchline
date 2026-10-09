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


def _door_png(tmp_path, ppm=50.0, width_m=0.9):
    """A 20 px wall with one 0.9 m door (leaf plus quarter arc) at ``ppm``."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    x, yh, r = 150, 150, width_m * ppm
    d.rectangle([x - 10, 40, x + 10, 360], fill=0)
    d.rectangle([x - 11, yh, x + 11, yh + r], fill=255)
    d.line([x, yh, x + r, yh], fill=0, width=3)
    d.arc([x - r, yh - r, x + r, yh + r], start=0, end=90, fill=0, width=2)
    p = tmp_path / "sheet_001.png"
    img.save(p)
    return p, (x, yh + r / 2)


def test_door_swing_provider_may_ship_and_finds_the_door(tmp_path):
    p = dp.provider_from_config({"provider": "door_swing"}, release=True)
    assert isinstance(p, dp.DoorSwingProvider)
    assert (p.info.license_class, p.info.eval_only) == ("permissive", False)
    assert "may ship" in p.info.note()
    png, (cx, cy) = _door_png(tmp_path)
    dets = p.detect(png, "set.pdf:sheet_001.json", px_per_m=50.0)
    assert len(dets) == 1
    d = dets[0]
    assert d.label == "door" and d.source == "set.pdf:sheet_001.json"
    x0, y0, x1, y1 = d.bbox
    assert x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2


def test_door_swing_provider_without_a_scale_reports_nothing(tmp_path):
    png, _ = _door_png(tmp_path)
    assert dp.DoorSwingProvider().detect(png, "s") == []
    fixed = dp.provider_from_config({"provider": "door_swing", "px_per_m": 50.0})
    assert len(fixed.detect(png, "s")) == 1
    with pytest.raises(dp.ProviderConfigError, match="px_per_metre"):
        dp.provider_from_config({"provider": "door_swing", "px_per_metre": 50.0})


def _glazing_sheet(tmp_path, dpi=36):
    """A 10 x 6 m shell with a 2 m glazing line in the south wall (#743)."""
    import test_plan_walls as T
    from pdf_fixtures import PageSpec, line, write_pdf

    import pdf_ingest as P

    gx0, gx1 = T._pt(6, 0)[0], T._pt(8, 0)[0]
    gy = T._pt(0, 0)[1]
    content = T._outline(T._mass(T.SHELL)) + line(gx0, gy, gx1, gy, 0.3)
    pdf = write_pdf(
        tmp_path / "g.pdf", [PageSpec(width=T.PAGE_W, height=T.PAGE_H, content=content)]
    )
    sheet = P.ingest_pdf(pdf, out_dir=tmp_path / "o", dpi=dpi).sheets[0]
    # fixture points are PDF y-up; the rendered sheet is y-down
    centre = ((gx0 + gx1) / 2 * sheet.px_per_pt, (T.PAGE_H - gy) * sheet.px_per_pt)
    return sheet, centre, T.M_PER_PT, tmp_path / "o" / sheet.raster_file


def test_vector_glazing_provider_may_ship_and_puts_the_window_on_the_glass(tmp_path):
    import numpy as np
    from PIL import Image

    import plan_walls as W

    p = dp.provider_from_config({"provider": "vector_glazing"}, release=True)
    assert isinstance(p, dp.VectorGlazingProvider)
    assert (p.info.license_class, p.info.eval_only) == ("permissive", False)
    sheet, (cx, cy), m_per_pt, png = _glazing_sheet(tmp_path)
    walls = W.extract_walls(sheet, m_per_pt).to_dict()
    plan = {"walls": walls, "height_pt": sheet.height_pt, "px_per_pt": sheet.px_per_pt}
    (d,) = p.detect(png, "set.pdf:sheet_001.json", plan=plan)
    assert d.label == "window" and d.source == "set.pdf:sheet_001.json"
    assert d.score == pytest.approx(W.WINDOW_CONFIDENCE)
    x0, y0, x1, y1 = d.bbox
    assert abs((x0 + x1) / 2 - cx) <= 2 and abs((y0 + y1) / 2 - cy) <= 2
    # 2 m of glass plus half the 0.30 m wall either side
    assert x1 - x0 == pytest.approx(2.3 / m_per_pt * sheet.px_per_pt, abs=1)
    img = np.asarray(Image.open(png))
    box = img[int(y0) : int(y1) + 1, int(x0) : int(x1) + 1]
    assert (box < 128).sum() > 0  # the glazing ink is inside the box


def test_vector_glazing_reports_windows_only_and_nothing_without_walls():
    walls = {
        "m_per_pt": 0.01,
        "walls": [{"id": "W1", "thickness_m": 0.2}],
        "openings": [
            {"walls": ["W1"], "a_m": (1, 1), "b_m": (2, 1), "kind": "door"},
            {"walls": ["W1"], "a_m": (3, 1), "b_m": (4, 1), "kind": "window",
             "window_confidence": 0.75, "tag_text": "W-1"},
        ],
    }  # fmt: skip
    (d,) = dp.window_detections(walls, height_pt=500.0, px_per_pt=2.0, source="s")
    assert (d.label, d.tag, d.score) == ("window", "W-1", 0.75)
    # x 300..400 pt, y (500 - 100) pt, padded by 0.1 m = 10 pt, all at 2 px per pt
    assert d.bbox == pytest.approx((580.0, 780.0, 820.0, 820.0))
    assert dp.VectorGlazingProvider().detect("x.png", "s") == []
    assert dp.window_detections({"openings": walls["openings"]}, 500.0, 2.0, "s") == []
