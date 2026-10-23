#!/usr/bin/env python3
"""Convert synthetic sheet ground truth to Ultralytics YOLO format (#743).

Input: a synth out/ tree as produced by ``python -m synth.make_dataset``
  sheets/sheet_<seed>.png + sheets/sheet_<seed>.gt.json  (Tier-1)

Output (default ~/workspace/datasets/detector_yolo/synth/):
  images/{train,val}/sheet_<seed>.png          (whole sheets, --tile-size 0)
  images/{train,val}/sheet_<seed>[_s<pxm>]_t<r>x<c>.png (tiles, --tile-size N;
      the _s<pxm> suffix marks multi-scale renders of the same layout)
  labels/{train,val}/sheet_<seed>[_t<r>x<c>].txt   (YOLO rows: cls cx cy w h, normalized)
  labels/{train,val}/sheet_<seed>[...].wallcls.txt (wall-class rows, #1113:
      cls cx cy w h normalized, own namespace: 0 = partition/interior,
      1 = structural/exterior; empty or absent for older gt.json files)
  data.yaml                             (Ultralytics dataset spec)

Class mapping follows the unified taxonomy (detector/classes.py): synth
symbol types are looked up in AEC_MAP, so door variants collapse to "door"
and windows to "window"; unmapped types (fixtures) are skipped and counted.
The split is a deterministic seeded shuffle of sheet ids, so the same
in_dir + seed always yields the same train/val split.

With --degrade-family-seed N (issue #1241), the scan-degradation family
(detector/degrade_scans.py) is applied per training sheet: one member and one
severity sampled per sheet from a seeded rng, with the gt geometry moved by
the same transform so labels stay aligned. The val split is left clean and
undegraded by construction.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import numpy as np

try:  # package import: python -m detector.convert_synth, pytest
    from detector.classes import AEC_MAP, CLASS_IDS
    from detector.degrade_scans import apply_family_with_boxes, transform_gt, variant_seed
except ImportError:  # direct script execution from inside detector/
    from classes import AEC_MAP, CLASS_IDS
    from degrade_scans import apply_family_with_boxes, transform_gt, variant_seed

# Inverse map: synth symbol type -> unified class id (or None to skip).
SYMBOL_CLASS = {k: (CLASS_IDS[v] if v is not None else None) for k, v in AEC_MAP.items()}


def _rows_from_symbols(symbols, sheet_w, sheet_h):
    """YOLO label rows for one sheet. Returns (rows, n_unmapped, n_degenerate)."""
    rows = []
    n_unmapped = 0
    n_degenerate = 0
    for sym in symbols:
        cls = SYMBOL_CLASS.get(sym.get("type"))
        if cls is None:
            n_unmapped += 1
            continue
        x0, y0, x1, y1 = sym["bbox_px"]
        # Clip to the sheet, then normalize to [0, 1].
        x0, x1 = sorted((max(0.0, min(float(x0), sheet_w)), max(0.0, min(float(x1), sheet_w))))
        y0, y1 = sorted((max(0.0, min(float(y0), sheet_h)), max(0.0, min(float(y1), sheet_h))))
        w = (x1 - x0) / sheet_w
        h = (y1 - y0) / sheet_h
        if w <= 0.0 or h <= 0.0:
            n_degenerate += 1
            continue
        cx = (x0 + x1) / 2.0 / sheet_w
        cy = (y0 + y1) / 2.0 / sheet_h
        rows.append(f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return rows, n_unmapped, n_degenerate


def _tile_starts(n, tile_size, stride):
    """Window origins along one axis; the tail tile always covers the edge."""
    if n <= tile_size:
        return [0]
    starts = list(range(0, n - tile_size + 1, stride))
    if starts[-1] + tile_size < n:
        starts.append(n - tile_size)
    return sorted(set(starts))


def _assign_to_tiles(symbols, tile_size, x_starts, y_starts):
    """Map each symbol to the tile that shows it best.

    A symbol that fits fully inside some tile's view is labelled there,
    unclipped; symbols smaller than the stride always fit in the overlap
    band of a neighbouring tile. Only a symbol too large for any single
    view falls back to the tile showing the most of it, labelled clipped.
    The old centre-claim rule wrote sliver labels the model cannot learn
    and left border fragments unlabeled in the overlap band.
    """
    tiles = {}
    for sym in symbols:
        cls = SYMBOL_CLASS.get(sym.get("type"))
        if cls is None:
            continue
        bx0, by0, bx1, by1 = sym["bbox_px"]
        bx0, bx1 = sorted((float(bx0), float(bx1)))
        by0, by1 = sorted((float(by0), float(by1)))
        full_area = (bx1 - bx0) * (by1 - by0)
        placement = None
        fallback = None  # (visible_area, r, c, x0, y0); first max wins
        for r, y0 in enumerate(y_starts):
            for c, x0 in enumerate(x_starts):
                vx0, vy0 = max(bx0, x0), max(by0, y0)
                vx1, vy1 = min(bx1, x0 + tile_size), min(by1, y0 + tile_size)
                va = max(0.0, vx1 - vx0) * max(0.0, vy1 - vy0)
                if va <= 0.0:
                    continue
                if va >= full_area:
                    placement = (r, c, x0, y0)
                    break
                if fallback is None or va > fallback[0]:
                    fallback = (va, r, c, x0, y0)
            if placement is not None:
                break
        if placement is None:
            if fallback is None:
                continue  # symbol sits outside every tile view
            placement = fallback[1:]
        r, c, x0, y0 = placement
        tiles.setdefault((r, c), []).append((cls, bx0, by0, bx1, by1, x0, y0))
    return tiles


def _tile_rows(claimed, tile_size):
    """YOLO rows for one tile from its claimed symbols; degenerate boxes drop."""
    rows, n_dropped = [], 0
    for cls, bx0, by0, bx1, by1, x0, y0 in claimed:
        tw = th = tile_size
        cx0, cy0 = max(bx0, x0), max(by0, y0)
        cx1, cy1 = min(bx1, x0 + tw), min(by1, y0 + th)
        w, h = (cx1 - cx0) / tw, (cy1 - cy0) / th
        if w <= 0.0 or h <= 0.0:
            n_dropped += 1
            continue
        rows.append(
            f"{cls} {((cx0 + cx1) / 2.0 - x0) / tw:.6f} {((cy0 + cy1) / 2.0 - y0) / th:.6f}"
            f" {w:.6f} {h:.6f}"
        )
    return rows, n_dropped


# Wall-class supervision (#1113): coarse wall TYPE per grid cell, separate
# namespace from the detector classes. 0 = partition (interior), 1 =
# structural (exterior). The detector head consumes these rows rasterized to
# its stride-8 grid; physical width regression stays out of scope (#1113).
WALL_CLASS_IDS = {"interior": 0, "exterior": 1}


def _wall_box_px(w):
    """Axis-aligned bbox (x0, y0, x1, y1) of one gt wall segment, sheet px."""
    ax, ay = w["a_px"]
    bx, by = w["b_px"]
    t = float(w.get("t_px", 0.0))
    if abs(ay - by) <= 1e-9:  # horizontal wall
        x0, x1 = sorted((float(ax), float(bx)))
        y0, y1 = ay - t / 2.0, ay + t / 2.0
    else:  # vertical wall
        y0, y1 = sorted((float(ay), float(by)))
        x0, x1 = ax - t / 2.0, ax + t / 2.0
    return x0, y0, x1, y1


def _wall_rows(walls, sheet_w, sheet_h, x0=0.0, y0=0.0, tw=None, th=None):
    """wallcls rows (cls cx cy w h, normalized) for walls inside a view.

    Unlike symbols, a wall is labelled in EVERY tile whose view it crosses,
    clipped to that view: wall-class supervision is dense per-cell, so each
    tile needs its own slice of every wall it shows. tw/th None means the
    whole sheet. Degenerate (zero-area) clips are skipped.
    """
    rows = []
    vw = float(tw) if tw is not None else float(sheet_w)
    vh = float(th) if th is not None else float(sheet_h)
    for w in walls:
        cls = WALL_CLASS_IDS.get(w.get("kind"))
        if cls is None:
            continue
        bx0, by0, bx1, by1 = _wall_box_px(w)
        cx0, cy0 = max(bx0, x0), max(by0, y0)
        cx1 = min(bx1, x0 + vw) if tw is not None else bx1
        cy1 = min(by1, y0 + vh) if th is not None else by1
        w_n, h_n = (cx1 - cx0) / vw, (cy1 - cy0) / vh
        if w_n <= 0.0 or h_n <= 0.0:
            continue
        rows.append(
            f"{cls} {((cx0 + cx1) / 2.0 - x0) / vw:.6f} {((cy0 + cy1) / 2.0 - y0) / vh:.6f}"
            f" {w_n:.6f} {h_n:.6f}"
        )
    return rows


def convert(
    in_dir,
    out_dir,
    val_fraction=0.2,
    seed=0,
    include_empty=False,
    tile_size=0,
    stride=512,
    degrade_family_seed=None,
):
    """Convert a synth sheets tree to a YOLO dataset. Returns a summary dict."""
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    sheets = sorted(in_dir.glob("sheet_*.gt.json"))
    if not sheets:
        raise FileNotFoundError(f"no sheet_*.gt.json under {in_dir}")

    # Deterministic split: fixed-seed shuffle of sorted sheet stems. Sheets
    # rendered at several scales (multi-scale training, stem suffix _s<N>)
    # are the same building, so the split groups by base stem: every scale of
    # a seed stays on one side, no cross-scale leakage.
    stems = [p.name[: -len(".gt.json")] for p in sheets]
    bases = sorted({re.sub(r"_s\d+$", "", s) for s in stems})
    order = np.random.default_rng(seed).permutation(len(bases))
    n_val = int(round(val_fraction * len(bases)))
    val_bases = {bases[i] for i in order[:n_val]}

    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    scales_seen = set()
    summary = {
        "sheets_total": len(sheets),
        "sheets_train": 0,
        "sheets_val": 0,
        "sheets_skipped_missing_png": 0,
        "boxes_written": 0,
        "wall_rows_written": 0,
        "boxes_skipped_unmapped": 0,
        "boxes_skipped_degenerate": 0,
        "sheets_kept_empty": 0,
        "tiles_written": 0,
        "tiles_kept_empty": 0,
        "sheets_degraded_train": 0,
        "degrade_member_counts": {},
    }
    for sheet_idx, js in enumerate(sheets):
        stem = js.name[: -len(".gt.json")]
        png = in_dir / f"{stem}.png"
        if not png.exists():
            summary["sheets_skipped_missing_png"] += 1
            continue
        gt = json.loads(js.read_text())
        scales_seen.add(gt.get("scale_px_per_m"))
        walls = gt.get("walls", [])
        sheet_w, sheet_h = gt["sheet_size_px"]
        split = "val" if re.sub(r"_s\d+$", "", stem) in val_bases else "train"
        # Issue #1241: degrade TRAIN sheets only, per-sheet member + severity,
        # truth transformed with the image so labels stay aligned. Val is the
        # clean, undegraded reference set by construction.
        symbols = gt.get("symbols", [])
        degraded_img = None
        if degrade_family_seed is not None and split == "train":
            from PIL import Image

            img_d, _aligned, meta = apply_family_with_boxes(
                Image.open(png).convert("RGB"),
                [sym["bbox_px"] for sym in symbols if "bbox_px" in sym],
                variant_seed(degrade_family_seed, sheet_idx),
            )
            transform_gt(
                gt,
                meta["member"],
                meta["severity"],
                variant_seed(degrade_family_seed, sheet_idx),
                img_d.size,
            )
            degraded_img = img_d
            member_counts = summary["degrade_member_counts"]
            member_counts[meta["member"]] = member_counts.get(meta["member"], 0) + 1
            summary["sheets_degraded_train"] += 1
        if tile_size > 0:
            from PIL import Image

            summary["boxes_skipped_unmapped"] += sum(
                1 for sym in symbols if SYMBOL_CLASS.get(sym.get("type")) is None
            )
            img = degraded_img if degraded_img is not None else Image.open(png)
            x_starts = _tile_starts(sheet_w, tile_size, stride)
            y_starts = _tile_starts(sheet_h, tile_size, stride)
            claimed = _assign_to_tiles(symbols, tile_size, x_starts, y_starts)
            n_tiles = 0
            for r, y0 in enumerate(y_starts):
                for c, x0 in enumerate(x_starts):
                    tw = min(tile_size, sheet_w - x0)
                    th = min(tile_size, sheet_h - y0)
                    rows, n_deg = _tile_rows(claimed.get((r, c), []), tile_size)
                    summary["boxes_skipped_degenerate"] += n_deg
                    if not rows and not include_empty:
                        continue
                    name = f"{stem}_t{r}x{c}"
                    img.crop((x0, y0, x0 + tw, y0 + th)).save(
                        out_dir / "images" / split / f"{name}.png"
                    )
                    (out_dir / "labels" / split / f"{name}.txt").write_text(
                        "".join(rr + "\n" for rr in rows)
                    )
                    wall_rows = _wall_rows(walls, sheet_w, sheet_h, x0, y0, tw, th)
                    (out_dir / "labels" / split / f"{name}.wallcls.txt").write_text(
                        "".join(rr + "\n" for rr in wall_rows)
                    )
                    summary["wall_rows_written"] += len(wall_rows)
                    summary["boxes_written"] += len(rows)
                    n_tiles += 1
                    if not rows:
                        summary["tiles_kept_empty"] = summary.get("tiles_kept_empty", 0) + 1
            summary["tiles_written"] = summary.get("tiles_written", 0) + n_tiles
            summary[f"sheets_{split}"] += 1
            continue
        rows, n_unmapped, n_degenerate = _rows_from_symbols(gt.get("symbols", []), sheet_w, sheet_h)
        summary["boxes_skipped_unmapped"] += n_unmapped
        summary["boxes_skipped_degenerate"] += n_degenerate
        if not rows and not include_empty:
            summary["sheets_kept_empty"] += 1
            continue
        if degraded_img is not None:
            degraded_img.save(out_dir / "images" / split / f"{stem}.png")
        else:
            shutil.copyfile(png, out_dir / "images" / split / f"{stem}.png")
        (out_dir / "labels" / split / f"{stem}.txt").write_text("".join(r + "\n" for r in rows))
        wall_rows = _wall_rows(walls, sheet_w, sheet_h)
        (out_dir / "labels" / split / f"{stem}.wallcls.txt").write_text(
            "".join(rr + "\n" for rr in wall_rows)
        )
        summary["wall_rows_written"] += len(wall_rows)
        summary["boxes_written"] += len(rows)
        summary[f"sheets_{split}"] += 1
        if not rows:
            summary["sheets_kept_empty"] += 1

    names = sorted(CLASS_IDS.items(), key=lambda kv: kv[1])
    yaml_path = out_dir / "data.yaml"
    yaml_path.write_text(
        f"path: {out_dir.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n" + "".join(f"  {i}: {n}\n" for n, i in names)
    )
    summary["scales"] = sorted(x for x in scales_seen if x is not None)
    summary["data_yaml"] = str(yaml_path)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="in_dir", required=True, help="synth out/sheets dir")
    ap.add_argument("--out", default=os.path.expanduser("~/workspace/datasets/detector_yolo/synth"))
    ap.add_argument("--val-fraction", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--include-empty",
        action="store_true",
        help="keep sheets with zero mapped boxes as background images",
    )
    ap.add_argument(
        "--tile-size",
        type=int,
        default=0,
        help="cut sheets into tile_size px square tiles (0 = whole sheets)",
    )
    ap.add_argument(
        "--stride",
        type=int,
        default=512,
        help="tile stride in px (used when --tile-size > 0)",
    )
    ap.add_argument(
        "--degrade-family-seed",
        type=int,
        default=None,
        help="apply the scan-degradation family (#1241) to TRAIN sheets only, "
        "one member + severity sampled per sheet; the value is the base seed",
    )
    args = ap.parse_args()
    summary = convert(
        args.in_dir,
        args.out,
        args.val_fraction,
        args.seed,
        args.include_empty,
        args.tile_size,
        args.stride,
        args.degrade_family_seed,
    )
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
