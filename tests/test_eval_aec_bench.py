"""scripts/eval_aec_bench.py: door/window F1 at IoU 0.50 (#743), no dataset needed."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "eval_aec_bench", Path(__file__).resolve().parents[1] / "scripts" / "eval_aec_bench.py"
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

XML = """<annotations>
  <image id="0" name="sheet_01.png" width="1000" height="800">
    <box label="Single Swing Door" xtl="100" ytl="100" xbr="200" ybr="200"/>
    <box label="Double Swing Door" xtl="400" ytl="100" xbr="600" ybr="200"/>
    <box label="Window" xtl="700" ytl="50" xbr="900" ybr="80"/>
    <box label="Sink" xtl="10" ytl="10" xbr="40" ybr="40"/>
    <polygon label="Window" points="100,600;300,600;300,630;100,630"/>
    <polygon label="Room" points="0,0;500,0;500,500;0,500"/>
  </image>
  <image id="1" name="sheet_02.png" width="1000" height="800">
    <box label="Window" xtl="10" ytl="10" xbr="110" ybr="40"/>
  </image>
</annotations>
"""


def _preds(path, preds, width=1000, height=800):
    body = {"image": path.stem + ".png", "width": width, "height": height, "preds": preds}
    path.write_text(json.dumps(body))


def _p(cls, x0, y0, x1, y1, conf=0.9):
    return {"cls": cls, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


@pytest.fixture
def bench(tmp_path):
    (tmp_path / "annotations_15.xml").write_text(XML)
    dets = tmp_path / "dets"
    dets.mkdir()
    return tmp_path, dets


def test_truth_keeps_doors_and_windows_only_with_polygons_by_bbox(bench):
    root, _ = bench
    t = ev.load_truth(root / "annotations_15.xml")
    s1 = t["sheet_01"]
    assert (s1["width"], s1["height"]) == (1000, 800)
    assert sorted(b["cls"] for b in s1["boxes"]) == ["door", "door", "window", "window"]
    poly = [b for b in s1["boxes"] if b["y0"] == 600][0]
    assert (poly["x0"], poly["x1"], poly["y1"]) == (100, 300, 630)


def test_f1_per_class_with_a_miss_a_stray_and_a_missing_sheet(bench):
    root, dets = bench
    # cls 0 door, 1 window (CLASS_NAMES); sheet_02 has no detections file
    _preds(
        dets / "sheet_01.json",
        [
            _p(0, 102, 98, 198, 205),  # door 1, IoU ~0.9
            _p(0, 400, 300, 600, 400),  # stray door: no overlap
            _p(1, 700, 50, 900, 80),  # window exact
            _p(1, 100, 600, 300, 630, 0.8),  # polygon window exact
        ],
    )
    rep = ev.evaluate(ev.load_truth(root / "annotations_15.xml"), dets)
    door, win = rep["classes"]["door"], rep["classes"]["window"]
    assert (door["tp"], door["fp"], door["fn"]) == (1, 1, 1)
    assert door["precision"] == door["recall"] == door["f1"] == pytest.approx(0.5)
    assert (win["tp"], win["fp"], win["fn"]) == (2, 0, 1)  # sheet_02's window missed
    assert win["f1"] == pytest.approx(2 * 1.0 * (2 / 3) / (1 + 2 / 3))
    assert rep["sheets_without_detections"] == ["sheet_02"]


def test_iou_threshold_is_the_cut(bench):
    root, dets = bench
    # a window box shifted so IoU with (10,10,110,40) is 1/3: below 0.5, above 0.3
    _preds(dets / "sheet_02.json", [_p(1, 60, 10, 160, 40)])
    truth = ev.load_truth(root / "annotations_15.xml")
    assert ev.evaluate(truth, dets)["sheets"]["sheet_02"]["window"]["tp"] == 0
    assert ev.evaluate(truth, dets, iou=0.3)["sheets"]["sheet_02"]["window"]["tp"] == 1


def test_detections_rescaled_from_their_own_frame(bench):
    root, dets = bench
    # detector ran on a 2x image: boxes are twice the annotation frame
    _preds(dets / "sheet_02.json", [_p(1, 20, 20, 220, 80)], width=2000, height=1600)
    rep = ev.evaluate(ev.load_truth(root / "annotations_15.xml"), dets)
    assert rep["sheets"]["sheet_02"]["window"]["tp"] == 1


def test_no_predictions_and_no_truth_give_none_not_zero(bench):
    root, dets = bench
    rep = ev.evaluate(ev.load_truth(root / "annotations_15.xml"), dets)
    assert rep["sheets"]["sheet_02"]["door"] == {
        "tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None, "f1": None
    }  # fmt: skip
    win = rep["classes"]["window"]
    assert win["precision"] is None and win["recall"] == 0.0 and win["f1"] is None


def test_cli_writes_the_report(bench, capsys):
    root, dets = bench
    out = root / "rep.json"
    assert ev.main(["--aec-bench", str(root), "--detections", str(dets), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["iou"] == 0.5
    assert "no detections file: sheet_01, sheet_02" in capsys.readouterr().out
    assert ev.main(["--aec-bench", str(dets), "--detections", str(dets), "--out", str(out)]) == 2
