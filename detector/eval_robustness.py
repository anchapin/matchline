#!/usr/bin/env python3
"""Robustness split for detector validation: metric deltas under synthetic degradation.

For each validation image, runs inference on the clean image and on deterministic
degraded variants (see degrade.py), matches predictions to ground truth with greedy
IoU matching (same algorithm as eval_zero_shot.py), and reports per-class
precision/recall deltas vs clean. Implements issue #499.

Inference goes through sahi_infer.infer_sheet (ultralytics is imported lazily
there), so this module imports cleanly without the detector venv; it fails with
a clear message only if inference is actually attempted without ultralytics.
Alternatively, pass --preds-dir with precomputed per-image prediction JSONs in
the sahi_infer.py output format
({"image", "width", "height", "preds": [...]}, one file per "<stem>.<variant>.json")
to evaluate without weights -- e.g. inference done once on a GPU box, eval
re-run anywhere.

Usage:
    python3 eval_robustness.py --root ~/datasets/detector_yolo/cubicasa \
        --weights runs/detector/weights/best.pt --n 50 --seed 7 \\
        --out /tmp/robust.json
    python3 eval_robustness.py --root ~/datasets/detector_yolo/cubicasa \
        --preds-dir /tmp/preds --out /tmp/robust.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict

try:  # package import: python -m detector.eval_robustness, pytest
    from detector.degrade import VARIANTS, degrade, variant_seed
except ImportError:  # direct script execution from inside detector/
    from degrade import VARIANTS, degrade, variant_seed

try:
    from detector.classes import CLASS_NAMES
except ImportError:
    try:
        from classes import CLASS_NAMES
    except ImportError:
        CLASS_NAMES = {}


def _cls_name(cls: int) -> str:
    # CLASS_NAMES is a list in detector/classes.py; tolerate a dict fallback.
    try:
        if isinstance(CLASS_NAMES, dict):
            return CLASS_NAMES.get(cls, f"cls{cls}")
        return CLASS_NAMES[cls]
    except (IndexError, TypeError):
        return f"cls{cls}"


def iou(a: dict, b: dict) -> float:
    xx0, yy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    xx1, yy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0, xx1 - xx0) * max(0, yy1 - yy0)
    a1 = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"])
    a2 = (b["x1"] - b["x0"]) * (b["y1"] - b["y0"])
    return inter / max(1e-9, a1 + a2 - inter)


def match_predictions(gt: list, preds: list, iou_thr: float = 0.5) -> dict:
    """Greedy IoU matching of predictions to ground truth, per class.

    Returns {cls: Counter(tp, fp, fn, n_gt, n_pred)}. Same algorithm as
    eval_zero_shot.py: predictions sorted by descending confidence, each
    matched to the best unmatched GT box at IoU >= iou_thr.
    """
    stats: dict = {}
    classes = {g["cls"] for g in gt} | {p["cls"] for p in preds}
    for cls in classes:
        g = [x for x in gt if x["cls"] == cls]
        p = sorted([x for x in preds if x["cls"] == cls], key=lambda x: -x["conf"])
        matched: set = set()
        tp = 0
        for pr in p:
            best, best_iou = -1, 0.0
            for i, gg in enumerate(g):
                if i in matched:
                    continue
                v = iou(pr, gg)
                if v > best_iou:
                    best, best_iou = i, v
            if best_iou >= iou_thr:
                matched.add(best)
                tp += 1
        stats[cls] = Counter(tp=tp, fp=len(p) - tp, fn=len(g) - tp, n_gt=len(g), n_pred=len(p))
    return stats


def load_yolo_labels(label_path: str, width: int, height: int) -> list:
    """Load a YOLO-format label file into pixel-space box dicts."""
    gt = []
    with open(label_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c, cx, cy, w, h = map(float, line.split())
            gt.append(
                {
                    "cls": int(c),
                    "x0": (cx - w / 2) * width,
                    "y0": (cy - h / 2) * height,
                    "x1": (cx + w / 2) * width,
                    "y1": (cy + h / 2) * height,
                }
            )
    return gt


def _infer(weights: str, image_path: str, args) -> list:
    try:
        from detector.sahi_infer import infer_sheet
    except ImportError:
        from sahi_infer import infer_sheet
    preds, (_w, _h) = infer_sheet(
        weights,
        image_path,
        tile=args.tile,
        overlap=args.overlap,
        conf=args.conf,
        device=args.device,
    )
    return preds


def _load_preds(preds_dir: str, stem: str, variant: str) -> list:
    path = os.path.join(preds_dir, f"{stem}.{variant}.json")
    with open(path) as f:
        return json.load(f)["preds"]


def evaluate(args) -> dict:
    """Run the robustness split; return the serializable report dict."""
    from PIL import Image

    img_dir = os.path.join(args.root, "images", args.split)
    lbl_dir = os.path.join(args.root, "labels", args.split)
    stems = sorted(
        os.path.splitext(f)[0] for f in os.listdir(img_dir) if f.endswith((".png", ".jpg", ".jpeg"))
    )[: args.n]

    agg: dict = {v: defaultdict(Counter) for v in VARIANTS}
    n_images = 0
    with tempfile.TemporaryDirectory(prefix="robust_") as tmp:
        for idx, stem in enumerate(stems):
            img_path = next(
                os.path.join(img_dir, stem + ext)
                for ext in (".png", ".jpg", ".jpeg")
                if os.path.exists(os.path.join(img_dir, stem + ext))
            )
            with Image.open(img_path) as im:
                W, H = im.size
            gt = load_yolo_labels(os.path.join(lbl_dir, stem + ".txt"), W, H)
            n_images += 1
            for variant in VARIANTS:
                if variant == "clean":
                    path = img_path
                else:
                    with Image.open(img_path) as im:
                        degraded = degrade(im, variant, variant_seed(args.seed, idx))
                    path = os.path.join(tmp, f"{stem}.{variant}.png")
                    degraded.save(path)
                if args.weights:
                    preds = _infer(args.weights, path, args)
                    if args.dump_preds:
                        os.makedirs(args.dump_preds, exist_ok=True)
                        with open(
                            os.path.join(args.dump_preds, f"{stem}.{variant}.json"), "w"
                        ) as f:
                            json.dump(
                                {"image": path, "width": W, "height": H, "preds": preds},
                                f,
                            )
                else:
                    preds = _load_preds(args.preds_dir, stem, variant)
                for cls, s in match_predictions(gt, preds, args.iou).items():
                    agg[variant][cls] += s

    def pr(s: Counter) -> tuple:
        return (s["tp"] / max(1, s["tp"] + s["fp"]), s["tp"] / max(1, s["n_gt"]))

    report = {
        "config": {
            "root": args.root,
            "split": args.split,
            "n_images": n_images,
            "seed": args.seed,
            "iou": args.iou,
            "variants": list(VARIANTS),
            "weights": args.weights,
            "preds_dir": args.preds_dir,
        },
        "variants": {},
    }
    clean_stats = {}
    for variant in VARIANTS:
        per_class = {}
        tot = Counter()
        for cls, s in sorted(agg[variant].items()):
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
        report["variants"][variant] = per_class
        if variant == "clean":
            clean_stats = per_class

    deltas = {}
    for variant in VARIANTS:
        if variant == "clean":
            continue
        deltas[variant] = {
            k: {
                "dp": v["p"] - clean_stats[k]["p"],
                "dr": v["r"] - clean_stats[k]["r"],
            }
            for k, v in report["variants"][variant].items()
        }
    report["deltas_vs_clean"] = deltas
    return report


def print_table(report: dict) -> None:
    variants = report["config"]["variants"]
    keys = [k for k in report["variants"]["clean"] if k != "overall"] + ["overall"]
    header = f"{'class':10s}" + "".join(f"{v + ' P':>12s}{v + ' R':>12s}" for v in variants)
    print(header)
    print("-" * len(header))
    for k in keys:
        name = report["variants"]["clean"][k]["name"]
        row = f"{name:10s}"
        for v in variants:
            s = report["variants"][v][k]
            row += f"{s['p']:12.3f}{s['r']:12.3f}"
        print(row)
    print("\nDeltas vs clean (percentage points):")
    for v, per in report["deltas_vs_clean"].items():
        cells = "  ".join(
            f"{report['variants'][v][k]['name']}: "
            f"dP={per[k]['dp'] * 100:+.1f} dR={per[k]['dr'] * 100:+.1f}"
            for k in keys
        )
        print(f"  {v}: {cells}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="YOLO dataset root")
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=50, help="max images (sorted order)")
    ap.add_argument("--seed", type=int, default=7, help="degradation base seed")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--weights", default=None, help="YOLO weights for inference")
    ap.add_argument(
        "--preds-dir", default=None, help="precomputed preds: <stem>.<variant>.json files"
    )
    ap.add_argument(
        "--dump-preds", default=None, help="with --weights, save per-variant preds here for reuse"
    )
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", default="robustness_report.json")
    args = ap.parse_args(argv)

    if bool(args.weights) == bool(args.preds_dir):
        ap.error("pass exactly one of --weights or --preds-dir")
    if args.weights:
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            ap.error("--weights requires ultralytics (install detector/requirements-detector.txt)")

    report = evaluate(args)
    print_table(report)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nreport written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
