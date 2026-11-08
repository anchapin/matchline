#!/usr/bin/env python3
"""TempFit-style scale/rotation template-fit baseline for the door/window
taxonomy (issue #1323, first concrete step of ``docs/tempfit-evaluation.md`` §7).

What this does, end to end, on data Matchline already holds:

1. ``templates`` cuts one canonical template per FloorPlanCAD variant
   (single_door, double_door, sliding_door, window, bay_window, blind_window)
   out of the **local parquet** (the same bytes the converter writes to
   ``detector_yolo/floorplancad/images/eval``) and records the bank as a repo
   artefact with per-template hashes and provenance.
2. ``fit`` matches that bank against the eval PNGs with the repo's own NCC
   machinery (``hvac_trace.ncc_locate`` + ``hvac_trace.template_bank``) over a
   scale/turn bank that follows the ``TEMPLATE_SCALES`` / ``TIGHT_TURNS``
   pattern (``hvac_trace.py:105``, ``:118``).
3. ``yolo`` regenerates the YOLO baseline arm with ``detector/sahi_infer.py``
   on CPU (the archived per-image FloorPlanCAD predictions are not on this
   machine).
4. ``score`` scores both arms with the greedy IoU-0.5 matcher of
   ``detector/eval_zero_shot.py`` (same algorithm as
   ``detector.eval_robustness.match_predictions``) on the clean set and on a
   deterministic scale/rotated subset, and prints the P/R tables.

No GPU, no training, no new annotation, no new data: the scale/rotated subset
is a deterministic geometric transform of the eval PNGs with the truth boxes
transformed by the same affine (``detector/degrade_scans.py`` pattern).

Ground-truth source: the parquet ``objects.bbox`` field is **COCO-style
[x, y, w, h] in 0-1000 coordinates** -- the reading the corrected converter
(``detector/convert_floorplancad.py``, #1327 / PR #1330) now uses too. This
script reads the truth from the parquet itself, so the truth and the pose
transform come from one source.

Data locations are resolved at run time, never baked into the source (the
AGENTS.md never-list): the command line wins, else ``$MATCHLINE_DATASETS``
(falling back to ``~/workspace/datasets``):

    <datasets>/floorplancad/train-00000-of-00001.parquet   the test split
    <datasets>/detector_yolo/floorplancad/                 the YOLO export
    <datasets>/yolo11n.pt                                 YOLO11n COCO weights

A missing path is a hard error that names the env var and the flag to use.

Template bank: ``assets/templates/floorplancad`` (six PNGs + ``manifest.json``)
is a noncommercial (CC BY-NC 4.0) derivative of FloorPlanCAD; the
``floorplancad_templates`` entry of ``assets/license_ledger.json`` records the
licence. The ``templates`` stage regenerates the bank; the manifest carries
the sha256 of every PNG, the crop it was cut from and the selection rule.

Usage:
    python scripts/tempfit_baseline.py templates --out assets/templates/floorplancad
    python scripts/tempfit_baseline.py fit --limit 100
    python scripts/tempfit_baseline.py yolo --limit 100      # needs ultralytics
    python scripts/tempfit_baseline.py score --limit 100

The stages write their intermediates into ``--work`` (default
``data/tempfit_baseline``) and the score report into ``--report`` (default
``<work>/scores.json``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE_DIR = ROOT / "assets" / "templates" / "floorplancad"
WORK_DIR = ROOT / "data" / "tempfit_baseline"

# --- data locations -------------------------------------------------------
# Nothing about the local machine is baked into the source (AGENTS.md
# never-list: no machine paths in code). The datasets root is
# ``$MATCHLINE_DATASETS`` when that is set and ``~/workspace/datasets``
# otherwise -- the same convention as detector/floorplancad_yolo_baseline.py.
# Every path can be overridden on the command line.
DATASETS_ENV = "MATCHLINE_DATASETS"
FALLBACK_DATASETS_DIR = "~/workspace/datasets"
DATASET_RELPATH = ("detector_yolo", "floorplancad")
PARQUET_RELPATH = ("floorplancad", "train-00000-of-00001.parquet")
WEIGHTS_RELPATH = ("yolo11n.pt",)


def datasets_root() -> Path:
    """Root of the local datasets tree (``$MATCHLINE_DATASETS`` or ``~``)."""
    base = os.environ.get(DATASETS_ENV) or FALLBACK_DATASETS_DIR
    return Path(base).expanduser()


def default_dataset() -> Path:
    """FloorPlanCAD YOLO export (``<datasets>/detector_yolo/floorplancad``)."""
    return datasets_root().joinpath(*DATASET_RELPATH)


def default_parquet() -> Path:
    """FloorPlanCAD parquet export (the test split Matchline already holds)."""
    return datasets_root().joinpath(*PARQUET_RELPATH)


def default_weights() -> Path:
    """Ultralytics YOLO11n COCO weights (``<datasets>/yolo11n.pt``)."""
    return datasets_root().joinpath(*WEIGHTS_RELPATH)


# Scale/turn bank, the TEMPLATE_SCALES / TIGHT_TURNS pattern of hvac_trace.py
# (per-class dicts of scales and of quarter turns). Nominal first.
TEMPLATE_SCALES = {"door": (1.0, 0.8, 1.25), "window": (1.0, 0.8, 1.25)}
TEMPLATE_TURNS = {"door": (0, 1), "window": (0, 1)}
# NCC proposal floor per class (the NCC_PROPOSE role of hvac_trace.py).
NCC_THRESH = {"door": 0.55, "window": 0.55}
# Geometric perturbation of the "scale/rotated" subset (scale factor, angle in
# degrees). Both are inside the bank above, so the subset measures the bank and
# not an unreachable pose.
SCALE_ROT_SCALE = 0.8
SCALE_ROT_ANGLE = 90.0
SUBSETS = ("clean", "scaled", "rotated", "scale_rot")
CLASS_IDS = {"door": 0, "window": 1}
CLASS_NAMES = ["door", "window"]
# Sanity window (px, 1000x1000 sheet) for a template candidate: a door/window
# symbol is a few dozen pixels; a box bigger than a third of the sheet is a
# degenerate annotation, not a symbol.
MIN_SYMBOL_PX = 4
MAX_SYMBOL_PX = 300
# How many canonical candidates per variant are inspected (deterministic
# order: image_id, object position).
MAX_TEMPLATE_CANDIDATES = 64
#: How the canonical template of a variant is picked. The same wording goes
#: into the bank manifest (``selection_rule``), so the manifest can never
#: claim a rule the code does not implement.
SELECTION_RULE = (
    "over the first --scan parquet images, the instance of each variant whose "
    "box size is closest to the centre of that variant's modal box-size bucket "
    "(sizes rounded to 10 px); ties break on (image_id, object position)"
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ink_signal(gray, bright_is_ink: bool = True):
    """Grayscale raster -> ink signal in [0, 1] (1 = ink).

    FloorPlanCAD renders are white lines on a black ground, so the polarity is
    decided from the sheet (see ``polarity``) and applied to the templates too.
    """
    import numpy as np

    arr = np.asarray(gray, dtype=np.float64)
    return arr / 255.0 if bright_is_ink else (255.0 - arr) / 255.0


def polarity(gray) -> bool:
    """True when the bright pixels are the ink (black-ground CAD render)."""
    import numpy as np

    return float(np.median(np.asarray(gray, dtype=np.float64))) < 127.5


def variant_classes() -> dict:
    """FloorPlanCAD variant -> unified class (detector/classes.py)."""
    from detector.classes import FLOORPLANCAD_MAP

    return dict(FLOORPLANCAD_MAP)


def parquet_index(parquet: Path) -> dict:
    """image_id -> [{"variant", "cls", "box"}] with box = [x0, y0, x1, y1] px.

    The parquet bbox field is COCO-style [x, y, w, h] in 0-1000 coordinates
    (proved by the per-object mask PNG size); this converts it to pixels.
    """
    import pyarrow.parquet as pq

    classes = variant_classes()
    table = pq.ParquetFile(parquet).read(columns=["image_id", "objects"])
    index: dict = {}
    for row in range(table.num_rows):
        iid = table.column("image_id")[row].as_py()
        objs = table.column("objects")[row].as_py()
        items = []
        for cat, bb in zip(objs["category"], objs["bbox"]):
            cls = classes.get(cat)
            if cls is None:
                continue
            x, y, w, h = bb
            if w <= 0 or h <= 0:
                continue
            items.append(
                {
                    "variant": cat,
                    "cls": cls,
                    "box": [x, y, x + w, y + h],
                    "wh": [w, h],
                }
            )
        index[iid] = items
    return index


# ---------------------------------------------------------------------------
# 1. template cutting
# ---------------------------------------------------------------------------


def canonical_candidates(index: dict, scan_ids: list) -> dict:
    """Canonical *candidates* per FloorPlanCAD variant.

    Deterministic rule: over the first ``scan_ids`` images, keep the instances
    of a variant whose box size sits in the variant's **modal** size bucket
    (rounded to 10 px) -- the typical symbol, not an extreme or degenerate
    annotation -- and drop boxes that are not plausible symbols
    (``MIN_SYMBOL_PX`` .. ``MAX_SYMBOL_PX``). Returns
    {variant: (bucket, [candidate, ...])} with candidate =
    (image_id, position, box).
    """
    from collections import Counter

    per_variant: dict = {}
    for iid in scan_ids:
        for pos, item in enumerate(index.get(iid, [])):
            w, h = item["wh"]
            if not (MIN_SYMBOL_PX <= w <= MAX_SYMBOL_PX) or not (
                MIN_SYMBOL_PX <= h <= MAX_SYMBOL_PX
            ):
                continue  # degenerate / whole-sheet annotation, not a symbol
            per_variant.setdefault(item["variant"], []).append((iid, pos, item["box"], w, h))
    out = {}
    for variant, items in sorted(per_variant.items()):
        buckets = Counter((round(it[3] / 10.0) * 10, round(it[4] / 10.0) * 10) for it in items)
        bucket = min(buckets.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        in_bucket = [
            it for it in items if (round(it[3] / 10.0) * 10, round(it[4] / 10.0) * 10) == bucket
        ]
        in_bucket.sort(key=lambda it: (it[0], it[1]))
        out[variant] = (bucket, [(it[0], it[1], it[2]) for it in in_bucket])
    return out


def cut_templates(parquet: Path, index: dict, out_dir: Path, scan: int) -> dict:
    """Cut the template bank + write the manifest. Returns the manifest."""
    import pyarrow.parquet as pq
    from PIL import Image

    classes = variant_classes()
    scan_ids = sorted(index)[:scan]
    candidates = canonical_candidates(index, scan_ids)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = pq.ParquetFile(parquet).read(columns=["image", "image_id"])
    # the parquet row of an image id (NOT its rank in the sorted id list:
    # the two only agree while the parquet happens to be sorted by id)
    row_of = {iid: i for i, iid in enumerate(table.column("image_id").to_pylist())}
    entries = []
    for variant, (bucket, cands) in sorted(candidates.items()):
        if classes.get(variant) is None:
            continue
        best = None
        for iid, pos, box in cands[:MAX_TEMPLATE_CANDIDATES]:
            raw = table.column("image")[row_of[iid]].as_py()["bytes"]
            img = Image.open(io.BytesIO(raw)).convert("L")
            w, h = img.size
            x0 = max(0, min(w - 1, int(round(box[0] / 1000.0 * w))))
            y0 = max(0, min(h - 1, int(round(box[1] / 1000.0 * h))))
            x1 = max(x0 + 1, min(w, int(round(box[2] / 1000.0 * w))))
            y1 = max(y0 + 1, min(h, int(round(box[3] / 1000.0 * h))))
            crop = img.crop((x0, y0, x1, y1))
            arr = np.asarray(crop)
            ink = float((arr > 128).mean())
            if ink <= 0.0:
                continue  # an empty crop is not a template
            # Selection rule (see SELECTION_RULE): the instance whose box
            # size is closest to the centre of the variant's modal size
            # bucket, ties broken on (image_id, object position). The ink
            # fraction is recorded, not ranked: ranking on it would pick
            # the emptiest crop of the bucket, not the most typical symbol.
            key = (
                abs(box[2] - box[0] - bucket[0]) + abs(box[3] - box[1] - bucket[1]),
                iid,
                pos,
            )
            if best is None or key < best[0]:
                best = (key, (iid, pos, box, x0, y0, x1, y1, crop, arr))
        if best is None:
            continue
        _key, (iid, pos, box, x0, y0, x1, y1, crop, arr) = best
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        data = buf.getvalue()
        name = f"{variant}.png"
        (out_dir / name).write_bytes(data)
        entries.append(
            {
                "name": variant,
                "class": classes[variant],
                "file": name,
                "source_image_id": iid,
                "source_parquet_row": row_of[iid],
                "bbox_1000_x_y_w_h": [
                    round(v, 3)
                    for v in (
                        box[0],
                        box[1],
                        box[2] - box[0],
                        box[3] - box[1],
                    )
                ],
                "crop_px": [x0, y0, x1, y1],
                "size_px": [crop.size[0], crop.size[1]],
                "ink_fraction": round(float((arr > 128).mean()), 5),
                "sha256": _sha256(data),
            }
        )
    manifest = {
        "id": "floorplancad_door_window_templates",
        "description": (
            "One canonical door/window template per FloorPlanCAD variant, cut from the "
            "local FloorPlanCAD parquet (the test-split export Matchline already holds)."
        ),
        "dataset": "FloorPlanCAD (tilak1114/FloorPlanCAD), test split, local parquet export",
        "license": "CC BY-NC 4.0",
        "license_class": "noncommercial",
        "ledger_entry": "assets/license_ledger.json (id: floorplancad_templates)",
        "generated_by": "scripts/tempfit_baseline.py templates",
        "selection_rule": SELECTION_RULE,
        "templates": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_templates(template_dir: Path) -> dict:
    """manifest -> {class: [(variant, grayscale template array)]}."""
    import numpy as np
    from PIL import Image

    manifest = json.loads((template_dir / "manifest.json").read_text())
    out: dict = {}
    for entry in manifest["templates"]:
        img = Image.open(template_dir / entry["file"]).convert("L")
        arr = np.asarray(img, dtype=np.float64)
        out.setdefault(entry["class"], []).append((entry["name"], arr))
    return out


# ---------------------------------------------------------------------------
# 2. the scale/rotated subset (deterministic pose transform of the eval PNGs)
# ---------------------------------------------------------------------------


def pose_transform(img, scale: float, angle_deg: float):
    """Scale + rotate a raster about its centre; returns (image, matrix, offset).

    Forward map of a source pixel p: p_out = A @ p + t, with A = scale * R.
    """
    import numpy as np
    from PIL import Image

    w, h = img.size
    th = math.radians(angle_deg)
    rot = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    a_mat = scale * rot
    centre = np.array([w / 2.0, h / 2.0])
    t = centre - a_mat @ centre
    inv = np.linalg.inv(a_mat)
    tinv = -inv @ t
    coeffs = (inv[0, 0], inv[0, 1], tinv[0], inv[1, 0], inv[1, 1], tinv[1])
    out = img.convert("RGB").transform(
        (w, h), Image.AFFINE, coeffs, resample=Image.BILINEAR, fillcolor="white"
    )
    return out, a_mat, t


def pose_boxes(boxes, a_mat, t, size):
    """Axis-aligned bounding box of each transformed box.

    ``boxes`` are ``(x0, y0, x1, y1)`` in the continuous pixel frame of the
    source raster -- the box covers the pixels ``x0..x1-1`` / ``y0..y1-1``,
    the same frame ``gt_for`` writes. ``a_mat``/``t`` are the forward map of
    ``pose_transform`` (``p_out = a_mat @ p + t``), so a box follows its
    symbol exactly (see tests/test_tempfit_baseline.py).

    The result has **one entry per input box**: ``None`` where the
    transformed box degenerates (< 1 px) or leaves the frame. Keeping the
    alignment explicit stops a caller from zipping a shorter list onto the
    boxes and shifting a class onto another box's geometry.
    """
    import numpy as np

    w, h = size
    out = []
    for x0, y0, x1, y1 in boxes:
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
        tc = corners @ a_mat.T + t
        nx0 = max(0.0, float(tc[:, 0].min()))
        ny0 = max(0.0, float(tc[:, 1].min()))
        nx1 = min(float(w), float(tc[:, 0].max()))
        ny1 = min(float(h), float(tc[:, 1].max()))
        if nx1 - nx0 < 1.0 or ny1 - ny0 < 1.0:
            out.append(None)
            continue
        out.append((nx0, ny0, nx1, ny1))
    return out


def subset_params(subset: str) -> tuple:
    """(scale, angle_deg) of a subset; clean is the identity."""
    if subset == "clean":
        return 1.0, 0.0
    if subset == "scaled":
        return SCALE_ROT_SCALE, 0.0
    if subset == "rotated":
        return 1.0, SCALE_ROT_ANGLE
    if subset == "scale_rot":
        return SCALE_ROT_SCALE, SCALE_ROT_ANGLE
    raise ValueError(f"unknown subset {subset!r}")


# ---------------------------------------------------------------------------
# 3. the template-fit stage
# ---------------------------------------------------------------------------


def fit_image(gray, templates: dict, ncc_thresh: dict, bright_is_ink: bool = True) -> list:
    """NCC template fit of the whole bank over one raster.

    ``gray`` is the ink signal of the sheet. Returns predictions in the
    detector/sahi_infer.py format: {cls, conf, x0, y0, x1, y1}.
    """
    import numpy as np

    from detector.sahi_infer import nms
    from hvac_trace import ncc_locate, template_bank

    preds = []
    for cls, variants in sorted(templates.items()):
        for _variant, raw in variants:
            tmpl = ink_signal(raw, bright_is_ink)
            for turn in TEMPLATE_TURNS.get(cls, (0,)):
                turned = np.rot90(tmpl, turn) if turn else tmpl
                for scaled in template_bank(turned, TEMPLATE_SCALES.get(cls, (1.0,))):
                    th, tw = scaled.shape
                    hits = ncc_locate(gray, scaled, thresh=ncc_thresh.get(cls, 0.55))
                    for y, x, score in hits:
                        preds.append(
                            {
                                "cls": CLASS_IDS[cls],
                                "conf": float(score),
                                "x0": float(x),
                                "y0": float(y),
                                "x1": float(x + tw),
                                "y1": float(y + th),
                            }
                        )
    return nms(preds, iou_thr=0.5)


# ---------------------------------------------------------------------------
# 4. plumbing: eval images, subsets, prediction files
# ---------------------------------------------------------------------------


def eval_ids(index: dict, limit: int, exclude: set) -> list:
    """Deterministic eval subset: sorted image ids, minus the template sources."""
    ids = sorted(i for i in index if i not in exclude)
    return ids[:limit] if limit else ids


def gt_for(index: dict, image_id: str, width: int, height: int) -> list:
    """Ground-truth boxes (pixel frame) of one eval image."""
    gt = []
    for item in index.get(image_id, []):
        x0, y0, x1, y1 = item["box"]
        gt.append(
            {
                "cls": CLASS_IDS[item["cls"]],
                "x0": x0 / 1000.0 * width,
                "y0": y0 / 1000.0 * height,
                "x1": x1 / 1000.0 * width,
                "y1": y1 / 1000.0 * height,
            }
        )
    return gt


def write_labels(path: Path, boxes, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for cls, (x0, y0, x1, y1) in boxes:
        cx = (x0 + x1) / 2.0 / width
        cy = (y0 + y1) / 2.0 / height
        bw = (x1 - x0) / width
        bh = (y1 - y0) / height
        lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def subset_image(dataset_root: Path, work: Path, subset: str, image_id: str, index: dict):
    """Path of the (possibly transformed) subset image; writes the truth file."""
    from PIL import Image

    src = dataset_root / "images" / "eval" / f"{image_id}.png"
    img = Image.open(src).convert("RGB")
    scale, angle = subset_params(subset)
    if subset == "clean":
        out_img = img
        a_mat = None
        t = None
    else:
        out_img, a_mat, t = pose_transform(img, scale, angle)
    out_dir = work / "images" / subset
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{image_id}.png"
    out_img.save(out_path)
    w, h = out_img.size
    gt = gt_for(index, image_id, w, h)
    if subset != "clean":
        moved = pose_boxes([(g["x0"], g["y0"], g["x1"], g["y1"]) for g in gt], a_mat, t, (w, h))
        gt = [
            {"cls": g["cls"], "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
            for g, b in zip(gt, moved)
            if b is not None
        ]
    write_labels(
        work / "labels" / subset / f"{image_id}.txt",
        [(g["cls"], (g["x0"], g["y0"], g["x1"], g["y1"])) for g in gt],
        w,
        h,
    )
    return out_path, w, h


def write_preds(path: Path, image_path: Path, width: int, height: int, preds: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "image": str(image_path),
                "width": width,
                "height": height,
                "preds": preds,
            },
            indent=1,
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_templates(args) -> int:
    index = parquet_index(args.parquet)
    manifest = cut_templates(args.parquet, index, args.out, args.scan)
    total = sum((args.out / e["file"]).stat().st_size for e in manifest["templates"])
    print(f"cut {len(manifest['templates'])} templates into {args.out} ({total} bytes)")
    for e in manifest["templates"]:
        print(f"  {e['name']:16s} {e['class']:6s} {e['size_px']} sha256={e['sha256'][:12]}")
    return 0


def cmd_fit(args) -> int:
    import numpy as np
    from PIL import Image

    templates = load_templates(args.templates)
    index = parquet_index(args.parquet)
    ids = eval_ids(index, args.limit, args.exclude)
    n = 0
    for subset in args.subsets:
        for image_id in ids:
            out_path, w, h = subset_image(args.dataset, args.work, subset, image_id, index)
            gray = np.asarray(Image.open(out_path).convert("L"), dtype=np.float64)
            preds = fit_image(gray, templates, args.ncc_thresh, polarity(gray))
            write_preds(
                args.work / "preds" / "fit" / subset / f"{image_id}.json",
                out_path,
                w,
                h,
                preds,
            )
            n += 1
            if args.verbose:
                print(f"  fit {subset}/{image_id}: {len(preds)} preds")
    print(f"fit stage: {n} image runs, {len(ids)} images x {len(args.subsets)} subsets")
    return 0


def cmd_yolo(args) -> int:
    from detector.sahi_infer import infer_sheet

    index = parquet_index(args.parquet)
    ids = eval_ids(index, args.limit, args.exclude)
    n = 0
    for subset in args.subsets:
        for image_id in ids:
            out_path, w, h = subset_image(args.dataset, args.work, subset, image_id, index)
            preds, (pw, ph) = infer_sheet(
                str(args.weights),
                str(out_path),
                tile=args.tile,
                overlap=args.overlap,
                conf=args.conf,
                iou_thr=0.5,
                imgsz=args.imgsz,
                device="cpu",
            )
            write_preds(
                args.work / "preds" / "yolo" / subset / f"{image_id}.json",
                out_path,
                pw,
                ph,
                preds,
            )
            n += 1
            if args.verbose:
                print(f"  yolo {subset}/{image_id}: {len(preds)} preds")
    print(f"yolo baseline: {n} image runs")
    return 0


def cmd_score(args) -> int:
    from detector.eval_robustness import match_predictions

    index = parquet_index(args.parquet)
    ids = eval_ids(index, args.limit, args.exclude)
    out: dict = {}
    for arm in args.arms:
        for subset in args.subsets:
            agg = {cls: {"tp": 0, "fp": 0, "fn": 0, "n_gt": 0, "n_pred": 0} for cls in CLASS_NAMES}
            for image_id in ids:
                gt_path = args.work / "labels" / subset / f"{image_id}.txt"
                pred_path = args.work / "preds" / arm / subset / f"{image_id}.json"
                if not gt_path.exists() or not pred_path.exists():
                    continue
                data = json.loads(pred_path.read_text())
                w, h = data["width"], data["height"]
                gt = []
                for line in gt_path.read_text().splitlines():
                    if not line.strip():
                        continue
                    c, cx, cy, bw, bh = map(float, line.split())
                    gt.append(
                        {
                            "cls": int(c),
                            "x0": (cx - bw / 2) * w,
                            "y0": (cy - bh / 2) * h,
                            "x1": (cx + bw / 2) * w,
                            "y1": (cy + bh / 2) * h,
                        }
                    )
                stats = match_predictions(gt, data["preds"], iou_thr=args.iou)
                for cls, s in stats.items():
                    name = CLASS_NAMES[cls]
                    agg[name]["tp"] += s["tp"]
                    agg[name]["fp"] += s["fp"]
                    agg[name]["fn"] += s["fn"]
                    agg[name]["n_gt"] += s["n_gt"]
                    agg[name]["n_pred"] += s["n_pred"]
            out.setdefault(arm, {})[subset] = {}
            for cls, s in agg.items():
                prec = s["tp"] / max(1, s["tp"] + s["fp"])
                rec = s["tp"] / max(1, s["n_gt"])
                out[arm][subset][cls] = {
                    "precision": round(prec, 5),
                    "recall": round(rec, 5),
                    "tp": s["tp"],
                    "fp": s["fp"],
                    "fn": s["fn"],
                    "n_gt": s["n_gt"],
                    "n_pred": s["n_pred"],
                }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.report}")
    for arm in args.arms:
        for subset in args.subsets:
            if subset not in out.get(arm, {}):
                continue
            print(f"== {arm} / {subset} (IoU>={args.iou}) ==")
            for cls in CLASS_NAMES:
                s = out[arm][subset][cls]
                print(
                    f"  {cls:6s} P={s['precision']:.5f} R={s['recall']:.5f} "
                    f"tp={s['tp']} fp={s['fp']} fn={s['fn']} "
                    f"(gt={s['n_gt']}, pred={s['n_pred']})"
                )
    return 0


def cmd_all(args) -> int:
    rc = cmd_templates(args)
    if rc:
        return rc
    rc = cmd_fit(args)
    if rc:
        return rc
    try:
        rc = cmd_yolo(args)
    except ImportError as exc:
        print(f"yolo arm skipped: {exc}")
    if rc:
        return rc
    return cmd_score(args)


def _resolve_path(explicit, default: Path, what: str, flag: str, required: bool = True) -> Path:
    """Resolve a data path and fail clearly when it is missing.

    The path comes from the command line when given, else from the
    environment/datasets root. A missing file is a hard error with the two
    ways of pointing at the data -- no silent fallback to a machine path.
    """
    path = Path(explicit).expanduser() if explicit is not None else default
    if required and not path.exists():
        raise SystemExit(
            f"{what} not found: {path}\nset {DATASETS_ENV}=<datasets root> or pass {flag} <path>"
        )
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--parquet",
        type=Path,
        default=None,
        help=f"FloorPlanCAD parquet (default: ${DATASETS_ENV}/{PARQUET_RELPATH[0]}/"
        f"{PARQUET_RELPATH[1]})",
    )
    ap.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help=f"FloorPlanCAD YOLO export (default: ${DATASETS_ENV}/{'/'.join(DATASET_RELPATH)})",
    )
    ap.add_argument(
        "--weights",
        type=Path,
        default=None,
        help=f"YOLO11n weights (default: ${DATASETS_ENV}/{WEIGHTS_RELPATH[0]})",
    )
    ap.add_argument("--templates", type=Path, default=TEMPLATE_DIR)
    ap.add_argument("--out", type=Path, default=TEMPLATE_DIR, help="template out dir")
    ap.add_argument("--work", type=Path, default=WORK_DIR)
    ap.add_argument("--report", type=Path, default=WORK_DIR / "scores.json")
    ap.add_argument("--limit", type=int, default=100, help="eval images per subset")
    ap.add_argument("--scan", type=int, default=5308, help="parquet images scanned for templates")
    ap.add_argument("--subsets", nargs="+", default=list(SUBSETS))
    ap.add_argument("--arms", nargs="+", default=["fit", "yolo"])
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=float, default=0.2)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--ncc-thresh", type=float, default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "command",
        choices=["templates", "fit", "yolo", "score", "all"],
        help="stage to run",
    )
    args = ap.parse_args(argv)
    args.exclude = set()
    # data paths: command line > environment/datasets root, and a missing
    # file is a clear error instead of a silent fallback
    args.parquet = _resolve_path(args.parquet, default_parquet(), "parquet", "--parquet")
    needs_dataset = args.command in ("fit", "yolo", "all")
    needs_weights = args.command in ("yolo", "all")
    args.dataset = _resolve_path(
        args.dataset,
        default_dataset(),
        "dataset",
        "--dataset",
        required=needs_dataset,
    )
    args.weights = _resolve_path(
        args.weights,
        default_weights(),
        "weights",
        "--weights",
        required=needs_weights,
    )
    if args.command in ("fit", "yolo", "score", "all"):
        manifest_path = args.templates / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            args.exclude = {e["source_image_id"] for e in manifest["templates"]}
    if args.ncc_thresh is not None:
        args.ncc_thresh = {"door": args.ncc_thresh, "window": args.ncc_thresh}
    else:
        args.ncc_thresh = NCC_THRESH
    if args.command == "templates":
        return cmd_templates(args)
    if args.command == "fit":
        return cmd_fit(args)
    if args.command == "yolo":
        return cmd_yolo(args)
    if args.command == "score":
        return cmd_score(args)
    return cmd_all(args)


if __name__ == "__main__":
    sys.exit(main())
