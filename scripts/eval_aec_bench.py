"""Door and window F1 at IoU 0.50 on AEC-geometric-bench sheets (#743).

Usage:
    python scripts/eval_aec_bench.py --aec-bench <dataset dir> --detections <dir>
        [--iou 0.5] [--out aec_bench_f1.json]

Ground truth is ``annotations_15.xml`` in the dataset dir: every box or
polygon labelled "Single Swing Door" / "Double Swing Door" counts as a door and
"Window" as a window (polygons by their bounding box). Detections are one
``<sheet stem>.json`` per sheet in the detector output format
(schemas/detector_output_v1.schema.json, as detector/sahi_infer.py writes it).
Its boxes are rescaled from its own ``width``/``height`` to the annotation
frame. A sheet with no detections file counts as no detections and is listed.

Matching is detector/eval_robustness.match_predictions: per class, predictions
by descending confidence, each to the best unmatched truth box at IoU >= the
threshold. Prints one line per sheet and per-class totals with precision,
recall and F1, and writes the JSON report. Plumbing only: no dataset ships
with the repo and nothing here trains or tunes a detector.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datasets_adapter import (  # noqa: E402
    AEC_OBJECT_TO_TAKEOFF,
    detections_from_yolo_json,
    safe_xml_parse,
)
from detector.eval_robustness import match_predictions  # noqa: E402

CLASSES = ("door", "window")
IOU = 0.5


def load_truth(xml_path: str | Path) -> dict:
    """{sheet stem: {"width", "height", "boxes": [{"cls", "x0", "y0", "x1", "y1"}]}}."""
    out: dict = {}
    for img in safe_xml_parse(xml_path).getroot().findall(".//image"):
        boxes = []
        for b in img.findall("box"):
            cls = AEC_OBJECT_TO_TAKEOFF.get(b.attrib["label"])
            if cls in CLASSES:
                boxes.append(
                    {
                        "cls": cls,
                        "x0": float(b.attrib["xtl"]),
                        "y0": float(b.attrib["ytl"]),
                        "x1": float(b.attrib["xbr"]),
                        "y1": float(b.attrib["ybr"]),
                    }
                )
        for p in img.findall("polygon"):
            cls = AEC_OBJECT_TO_TAKEOFF.get(p.attrib["label"])
            if cls not in CLASSES:
                continue
            pts = [tuple(map(float, q.split(","))) for q in p.attrib["points"].split(";") if q]
            if pts:
                xs, ys = [q[0] for q in pts], [q[1] for q in pts]
                boxes.append(
                    {"cls": cls, "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}
                )
        out[Path(img.attrib["name"]).stem] = {
            "width": int(img.attrib["width"]),
            "height": int(img.attrib["height"]),
            "boxes": boxes,
        }
    return out


def load_preds(path: str | Path, width: int, height: int) -> list:
    """Door/window detections in the annotation frame, as match_predictions boxes."""
    data = json.loads(Path(path).read_text())
    sx = width / float(data["width"]) if data.get("width") else 1.0
    sy = height / float(data["height"]) if data.get("height") else 1.0
    out = []
    for d in detections_from_yolo_json(path, f"aec-bench:{Path(path).stem}"):
        cls = d.label.lower()
        if cls not in CLASSES:
            continue
        x0, y0, x1, y1 = d.bbox
        out.append(
            {
                "cls": cls,
                "conf": d.score,
                "x0": x0 * sx,
                "y0": y0 * sy,
                "x1": x1 * sx,
                "y1": y1 * sy,
            }
        )
    return out


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    # 2tp / (2tp + fp + fn): a class with truth and no predictions scores 0, not n/a
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def evaluate(truth: dict, det_dir: str | Path, iou: float = IOU) -> dict:
    det_dir = Path(det_dir)
    totals = {c: [0, 0, 0] for c in CLASSES}
    sheets, missing = {}, []
    for stem in sorted(truth):
        t = truth[stem]
        p = det_dir / f"{stem}.json"
        if p.exists():
            preds = load_preds(p, t["width"], t["height"])
        else:
            preds = []
            missing.append(stem)
        stats = match_predictions(t["boxes"], preds, iou)
        row = {}
        for c in CLASSES:
            s = stats.get(c)
            tp, fp, fn = (s["tp"], s["fp"], s["fn"]) if s else (0, 0, 0)
            row[c] = _prf(tp, fp, fn)
            totals[c][0] += tp
            totals[c][1] += fp
            totals[c][2] += fn
        sheets[stem] = row
    return {
        "iou": iou,
        "classes": {c: _prf(*totals[c]) for c in CLASSES},
        "sheets": sheets,
        "sheets_without_detections": missing,
    }


def _fmt(v) -> str:
    return "  n/a" if v is None else f"{v:.3f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aec-bench", required=True, help="dataset dir with annotations_15.xml")
    ap.add_argument("--detections", required=True, help="dir of <sheet stem>.json detector outputs")
    ap.add_argument("--iou", type=float, default=IOU)
    ap.add_argument("--out", default="aec_bench_f1.json")
    a = ap.parse_args(argv)
    xml = Path(a.aec_bench) / "annotations_15.xml"
    if not xml.exists():
        print(f"no annotations file: {xml}", file=sys.stderr)
        return 2
    rep = evaluate(load_truth(xml), a.detections, a.iou)
    for stem, row in rep["sheets"].items():
        print(stem, "  ".join(f"{c} {row[c]['tp']}/{row[c]['tp'] + row[c]['fn']}" for c in CLASSES))
    for c, s in rep["classes"].items():
        prf = f"P {_fmt(s['precision'])}  R {_fmt(s['recall'])}  F1 {_fmt(s['f1'])}"
        print(f"{c:6s} {prf} @ IoU {a.iou}")
    if rep["sheets_without_detections"]:
        print("no detections file:", ", ".join(rep["sheets_without_detections"]))
    Path(a.out).write_text(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
