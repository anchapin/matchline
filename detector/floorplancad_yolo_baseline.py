#!/usr/bin/env python3
"""YOLO baseline precision/recall on FloorPlanCAD (corrected labels) — issue #1329.

Batch driver that produces the YOLO baseline P/R tables (clean subset +
scale/rotated subset) used by the template-fit arm (#1328) as its comparison
baseline. The subset is a deterministic slice of the corrected FloorPlanCAD
eval split: the first N image stems in sorted order.

Design constraints (from #1329):
  * The ground truth comes from the CORRECTED eval labels
    (``$MATCHLINE_DATASETS/detector_yolo/floorplancad/labels/eval``), which
    were regenerated with the fixed converter (COCO ``[x, y, w, h]`` bboxes).
  * Predictions are regenerated here with ``sahi_infer.infer_sheet`` + the
    local ``yolo11n.pt`` weights on CPU. No downloads, no training.
  * Scoring reuses the greedy IoU-0.5 matching of ``eval_zero_shot.py`` as
    implemented in ``eval_robustness.match_predictions`` (no second matching
    algorithm is invented here).
  * The scale/rotated subset is built from the SAME images with documented,
    deterministic transforms; image and GT boxes are transformed consistently.

Usage:
    python3 floorplancad_yolo_baseline.py --n 50 --weights <weights.pt> \\
        --out /tmp/baseline.json --images-out images.txt

The dataset root defaults to ``$MATCHLINE_DATASETS/detector_yolo/floorplancad``
(falling back to ``~/workspace/datasets/...``), so no machine path is baked
into the repo.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # package import: python -m detector.floorplancad_yolo_baseline, pytest
    from detector.classes import CLASS_NAMES
    from detector.eval_robustness import load_yolo_labels, match_predictions
except ImportError:  # direct script execution from inside detector/
    from classes import CLASS_NAMES
    from eval_robustness import load_yolo_labels, match_predictions


# --- transforms -------------------------------------------------------------
# Every transform is a pure function of (image, boxes, factor/angle); there is
# no randomness anywhere, so the scale/rotated subset is reproducible from the
# numbers in the results note alone.

SCALE_VARIANTS = (("scale_0.8", 0.8), ("scale_1.25", 1.25))
ROT_VARIANTS = (("rot_p15", 15.0), ("rot_m15", -15.0))
VARIANTS = ("clean",) + tuple(n for n, _ in SCALE_VARIANTS) + tuple(n for n, _ in ROT_VARIANTS)
#: the two headline subsets of the results note
SUBSETS = {"clean": ("clean",), "scale_rot": tuple(v for v in VARIANTS if v != "clean")}


def scale_boxes(boxes: list, factor: float) -> list:
    """Scale pixel-space boxes by ``factor`` (origin at the image top-left)."""
    out = []
    for b in boxes:
        out.append(
            {
                "cls": b["cls"],
                "x0": b["x0"] * factor,
                "y0": b["y0"] * factor,
                "x1": b["x1"] * factor,
                "y1": b["y1"] * factor,
            }
        )
    return out


def rotate_point(x: float, y: float, cx: float, cy: float, deg: float) -> tuple:
    """Rotate a point about ``(cx, cy)`` by ``deg`` degrees counter-clockwise.

    Image coordinates have y growing downwards, so a visually
    counter-clockwise rotation is the transpose of the usual matrix.
    """
    t = math.radians(deg)
    dx, dy = x - cx, y - cy
    return (cx + dx * math.cos(t) + dy * math.sin(t), cy - dx * math.sin(t) + dy * math.cos(t))


def rotate_boxes(boxes: list, deg: float, size: tuple, offset: tuple = (0.0, 0.0)) -> list:
    """Rotate GT boxes about the image centre; returns axis-aligned boxes.

    The four corners of each box are rotated and the axis-aligned bounds of
    the rotated corners are kept, so the label follows the rotated drawing.
    ``offset`` is the translation applied by an expanding rotation (the
    canvas grows by the rotation's bounding box, so the old centre moves).
    """
    W, H = size
    cx, cy = W / 2.0, H / 2.0
    dx_off, dy_off = offset
    out = []
    for b in boxes:
        pts = [
            rotate_point(b["x0"], b["y0"], cx, cy, deg),
            rotate_point(b["x1"], b["y0"], cx, cy, deg),
            rotate_point(b["x1"], b["y1"], cx, cy, deg),
            rotate_point(b["x0"], b["y1"], cx, cy, deg),
        ]
        xs = [p[0] + dx_off for p in pts]
        ys = [p[1] + dy_off for p in pts]
        out.append(
            {"cls": b["cls"], "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}
        )
    return out


def apply_variant(img, boxes: list, variant: str):
    """Return (transformed image, transformed boxes) for a variant name."""
    if variant == "clean":
        return img, boxes
    if variant.startswith("scale_"):
        factor = float(variant.split("_", 1)[1])
        W, H = img.size
        new = (max(1, int(round(W * factor))), max(1, int(round(H * factor))))
        return img.resize(new, 1), scale_boxes(boxes, factor)
    if variant.startswith("rot_"):
        deg = float(variant[4:].replace("p", "+").replace("m", "-"))
        rot = img.rotate(deg, expand=True, fillcolor=(255, 255, 255))
        W, H = img.size
        W2, H2 = rot.size
        # an expanding rotation grows the canvas symmetrically, so the old
        # centre moves by half the growth in each direction
        offset = ((W2 - W) / 2.0, (H2 - H) / 2.0)
        return rot, rotate_boxes(boxes, deg, img.size, offset)
    raise ValueError(f"unknown variant: {variant}")


# --- data helpers -----------------------------------------------------------


def dataset_root(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get("MATCHLINE_DATASETS") or os.path.expanduser("~/workspace/datasets")
    return os.path.join(os.path.expanduser(base), "detector_yolo", "floorplancad")


def _rel_to_datasets(path: str) -> str:
    """Render a path relative to the datasets root, so reports never carry a
    machine path. Falls back to the file name when the path is elsewhere."""
    path = os.path.abspath(os.path.expanduser(path))
    base = os.path.abspath(
        os.path.expanduser(os.environ.get("MATCHLINE_DATASETS") or "~/workspace/datasets")
    )
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return os.path.basename(path)
    if rel.startswith(".."):
        return os.path.basename(path)
    return "$MATCHLINE_DATASETS/" + rel.replace(os.sep, "/")


def default_weights() -> str:
    base = os.environ.get("MATCHLINE_DATASETS") or os.path.expanduser("~/workspace/datasets")
    return os.path.join(os.path.expanduser(base), "yolo11n.pt")


def list_stems(img_dir: str, n: int) -> list:
    stems = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(img_dir)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    return stems[:n]


def image_path(img_dir: str, stem: str) -> str:
    for ext in (".png", ".jpg", ".jpeg"):
        p = os.path.join(img_dir, stem + ext)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no image for stem {stem} in {img_dir}")


def infer(weights: str, path: str, args) -> list:
    try:
        from detector.sahi_infer import infer_sheet
    except ImportError:
        from sahi_infer import infer_sheet
    preds, _size = infer_sheet(
        weights,
        path,
        tile=args.tile,
        overlap=args.overlap,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
    )
    return preds


def load_preds_file(path: str) -> list:
    with open(path) as f:
        return json.load(f)["preds"]


def pr(s: Counter) -> tuple:
    return (s["tp"] / max(1, s["tp"] + s["fp"]), s["tp"] / max(1, s["n_gt"]))


def summarize(agg: dict) -> dict:
    """Per-class + overall P/R from an aggregate {cls: Counter}."""
    per_class = {}
    tot = Counter()
    for cls, s in sorted(agg.items()):
        tot += s
        p, r = pr(s)
        per_class[str(cls)] = {
            "name": _cls_name(cls),
            "p": p,
            "r": r,
            "tp": s["tp"],
            "fp": s["fp"],
            "fn": s["fn"],
            "n_gt": s["n_gt"],
            "n_pred": s["n_pred"],
        }
    p, r = pr(tot)
    per_class["overall"] = {
        "name": "overall",
        "p": p,
        "r": r,
        "tp": tot["tp"],
        "fp": tot["fp"],
        "fn": tot["fn"],
        "n_gt": tot["n_gt"],
        "n_pred": tot["n_pred"],
    }
    return per_class


def _cls_name(cls: int) -> str:
    try:
        if isinstance(CLASS_NAMES, dict):
            return CLASS_NAMES.get(cls, f"cls{cls}")
        return CLASS_NAMES[cls]
    except (IndexError, TypeError, KeyError):
        return f"cls{cls}"


# --- main evaluation --------------------------------------------------------


def evaluate(args) -> dict:
    from PIL import Image

    root = dataset_root(args.root)
    img_dir = os.path.join(root, "images", "eval")
    lbl_dir = os.path.join(root, "labels", "eval")
    if not os.path.isdir(img_dir) or not os.path.isdir(lbl_dir):
        raise SystemExit(
            f"dataset not found: {root}\n"
            "set MATCHLINE_DATASETS=<datasets root> or pass --root"
        )

    stems = list_stems(img_dir, args.n)
    variants = tuple(args.variants.split(",")) if args.variants else VARIANTS

    agg: dict = {v: defaultdict(Counter) for v in variants}
    per_image: list = []
    timings: list = []
    tmpdir = args.work_dir or tempfile.mkdtemp(prefix="fpcad_base_")
    os.makedirs(tmpdir, exist_ok=True)
    img_out_dir = os.path.join(tmpdir, "images")
    os.makedirs(img_out_dir, exist_ok=True)

    for stem in stems:
        src = image_path(img_dir, stem)
        with Image.open(src) as im:
            base_img = im.convert("RGB")
            W, H = base_img.size
        gt = load_yolo_labels(os.path.join(lbl_dir, stem + ".txt"), W, H)
        for variant in variants:
            img, vgt = apply_variant(base_img, gt, variant)
            path = os.path.join(img_out_dir, f"{stem}.{variant}.png")
            img.save(path)
            t0 = time.time()
            if args.preds_dir:
                preds = load_preds_file(os.path.join(args.preds_dir, f"{stem}.{variant}.json"))
            else:
                preds = infer(args.weights, path, args)
                if args.dump_preds:
                    os.makedirs(args.dump_preds, exist_ok=True)
                    with open(os.path.join(args.dump_preds, f"{stem}.{variant}.json"), "w") as f:
                        # NB: only the file name is stored, so the committed
                        # prediction files carry no machine path.
                        json.dump(
                            {
                                "image": f"{stem}.{variant}.png",
                                "width": img.size[0],
                                "height": img.size[1],
                                "preds": preds,
                            },
                            f,
                        )
            dt = time.time() - t0
            timings.append(dt)
            stats = match_predictions(vgt, preds, args.iou)
            for cls, s in stats.items():
                agg[variant][cls] += s
            row = {
                "stem": stem,
                "variant": variant,
                "width": img.size[0],
                "height": img.size[1],
                "seconds": round(dt, 3),
            }
            for cls, s in sorted(stats.items()):
                p, r = pr(s)
                row[f"{_cls_name(cls)}_p"] = round(p, 5)
                row[f"{_cls_name(cls)}_r"] = round(r, 5)
                row[f"{_cls_name(cls)}_tp"] = s["tp"]
                row[f"{_cls_name(cls)}_fp"] = s["fp"]
                row[f"{_cls_name(cls)}_fn"] = s["fn"]
                row[f"{_cls_name(cls)}_n_gt"] = s["n_gt"]
                row[f"{_cls_name(cls)}_n_pred"] = s["n_pred"]
            per_image.append(row)

    report = {
        "config": {
            "dataset_root": _rel_to_datasets(root),
            "weights": _rel_to_datasets(args.weights),
            "split": "eval",
            "n_images": len(stems),
            "subset_rule": "first N image stems in sorted order of the eval split",
            "iou": args.iou,
            "variants": list(variants),
            "subsets": {k: list(v) for k, v in SUBSETS.items()},
            "predictions_source": "regenerated" if not args.preds_dir else "precomputed",
            "inference": {
                "conf": args.conf,
                "tile": args.tile,
                "overlap": args.overlap,
                "imgsz": args.imgsz,
                "device": args.device,
                "sahi_infer": "detector/sahi_infer.py:infer_sheet",
                "nms": "detector/sahi_infer.py:nms (iou 0.5, contain 0.8)",
            },
            "transform_spec": {
                "scale_0.8": "resize image to round(W*0.8) x round(H*0.8); GT boxes scaled by 0.8",
                "scale_1.25": "resize image to round(W*1.25) x round(H*1.25); GT boxes scaled by 1.25",
                "rot_p15": "PIL rotate +15 deg CCW about the image centre, expand=True, white fill; "
                "GT box corners rotated about the centre, axis-aligned bounds kept",
                "rot_m15": "PIL rotate -15 deg CCW about the image centre, expand=True, white fill; "
                "GT box corners rotated about the centre, axis-aligned bounds kept",
            },
            "matching": "detector/eval_robustness.py:match_predictions "
            "(greedy IoU, same algorithm as eval_zero_shot.py)",
        },
        "variants": {v: summarize(agg[v]) for v in variants},
        "subsets": {
            name: summarize(
                defaultdict(
                    Counter,
                    {
                        cls: sum((agg[v][cls] for v in vs), Counter())
                        for cls in set().union(*[set(agg[v]) for v in vs])
                    },
                )
            )
            for name, vs in SUBSETS.items()
            if all(v in variants for v in vs)
        },
        "timing": {
            "n_inferences": len(timings),
            "total_seconds": round(sum(timings), 2),
            "mean_seconds": round(sum(timings) / max(1, len(timings)), 3),
            "max_seconds": round(max(timings) if timings else 0.0, 3),
        },
        "stems": stems,
    }
    return report, per_image


def print_tables(report: dict) -> None:
    keys = ["0", "1", "overall"]
    names = {"0": "door", "1": "window"}
    for subset, per in report["subsets"].items():
        print(f"\n== subset: {subset} ==")
        header = f"{'class':10s}{'P':>10s}{'R':>10s}{'tp':>8s}{'fp':>8s}{'fn':>8s}{'gt':>8s}{'pred':>8s}"
        print(header)
        print("-" * len(header))
        for k in keys:
            if k not in per:
                continue
            s = per[k]
            label = names.get(k, s["name"])
            print(
                f"{label:10s}{s['p']:10.5f}{s['r']:10.5f}{s['tp']:8d}{s['fp']:8d}"
                f"{s['fn']:8d}{s['n_gt']:8d}{s['n_pred']:8d}"
            )
    print("\n== per-variant ==")
    for variant, per in report["variants"].items():
        cells = "  ".join(
            f"{per[k]['name']}: P={per[k]['p']:.5f} R={per[k]['r']:.5f}"
            for k in ("0", "1", "overall")
            if k in per
        )
        print(f"  {variant:12s} {cells}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="floorplancad YOLO dataset root")
    ap.add_argument("--weights", default=None, help="YOLO weights (default $MATCHLINE_DATASETS/yolo11n.pt)")
    ap.add_argument("--n", type=int, default=50, help="subset size (first N sorted stems)")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--out", default=None, help="write the JSON report here")
    ap.add_argument("--images-out", default=None, help="write the image stem list here")
    ap.add_argument("--per-image-out", default=None, help="write per-image rows (CSV) here")
    ap.add_argument("--dump-preds", default=None, help="save per-image prediction JSONs here")
    ap.add_argument("--preds-dir", default=None, help="reuse precomputed preds (no inference)")
    ap.add_argument("--work-dir", default=None, help="where transformed images are written")
    args = ap.parse_args(argv)

    if args.weights is None:
        args.weights = default_weights()
    args.weights = os.path.abspath(os.path.expanduser(args.weights))
    if not args.preds_dir and not os.path.exists(args.weights):
        raise SystemExit(f"weights not found: {args.weights}")

    report, per_image = evaluate(args)
    print_tables(report)
    print("\ntiming:", json.dumps(report["timing"]))

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"report written to {args.out}")
    if args.images_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.images_out)), exist_ok=True)
        with open(args.images_out, "w") as f:
            f.write("\n".join(report["stems"]) + "\n")
        print(f"image list written to {args.images_out}")
    if args.per_image_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.per_image_out)), exist_ok=True)
        fields = ["stem", "variant", "width", "height", "seconds"]
        for c in ("door", "window"):
            fields += [f"{c}_{k}" for k in ("p", "r", "tp", "fp", "fn", "n_gt", "n_pred")]
        with open(args.per_image_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in per_image:
                w.writerow(row)
        print(f"per-image rows written to {args.per_image_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
