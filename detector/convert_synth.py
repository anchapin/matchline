#!/usr/bin/env python3
"""Convert synthetic sheet ground truth to Ultralytics YOLO format (#743).

Input: a synth out/ tree as produced by ``python -m synth.make_dataset``
  sheets/sheet_<seed>.png + sheets/sheet_<seed>.gt.json  (Tier-1)

Output (default ~/workspace/datasets/detector_yolo/synth/):
  images/{train,val}/sheet_<seed>.png          (whole sheets, --tile-size 0)
  images/{train,val}/sheet_<seed>_t<r>x<c>.png (tiles, --tile-size N)
  labels/{train,val}/sheet_<seed>[_t<r>x<c>].txt   (YOLO rows: cls cx cy w h, normalized)
  data.yaml                             (Ultralytics dataset spec)

Class mapping follows the unified taxonomy (detector/classes.py): synth
symbol types are looked up in AEC_MAP, so door variants collapse to "door"
and windows to "window"; unmapped types (fixtures) are skipped and counted.
The split is a deterministic seeded shuffle of sheet ids, so the same
in_dir + seed always yields the same train/val split.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import numpy as np

try:  # package import: python -m detector.convert_synth, pytest
    from detector.classes import AEC_MAP, CLASS_IDS
except ImportError:  # direct script execution from inside detector/
    from classes import AEC_MAP, CLASS_IDS

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
    """Map each symbol to the FIRST tile whose window holds its centre.

    Overlapping strides can put a centre inside two windows, so the first
    (smallest origin) tile claims it; the neighbour sees the fragment without
    a label, the standard sliding-window convention.
    """
    tiles = {}
    for sym in symbols:
        cls = SYMBOL_CLASS.get(sym.get("type"))
        if cls is None:
            continue
        bx0, by0, bx1, by1 = sym["bbox_px"]
        bx0, bx1 = sorted((float(bx0), float(bx1)))
        by0, by1 = sorted((float(by0), float(by1)))
        cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
        for r, y0 in enumerate(y_starts):
            placed = False
            for c, x0 in enumerate(x_starts):
                if x0 <= cx < x0 + tile_size and y0 <= cy < y0 + tile_size:
                    tiles.setdefault((r, c), []).append((cls, bx0, by0, bx1, by1, x0, y0))
                    placed = True
                    break
            if placed:
                break
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


def convert(
    in_dir, out_dir, val_fraction=0.2, seed=0, include_empty=False, tile_size=0, stride=512
):
    """Convert a synth sheets tree to a YOLO dataset. Returns a summary dict."""
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    sheets = sorted(in_dir.glob("sheet_*.gt.json"))
    if not sheets:
        raise FileNotFoundError(f"no sheet_*.gt.json under {in_dir}")

    # Deterministic split: fixed-seed shuffle of sorted sheet stems.
    stems = [p.name[: -len(".gt.json")] for p in sheets]
    order = np.random.default_rng(seed).permutation(len(stems))
    n_val = int(round(val_fraction * len(stems)))
    val_stems = {stems[i] for i in order[:n_val]}

    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    summary = {
        "sheets_total": len(sheets),
        "sheets_train": 0,
        "sheets_val": 0,
        "sheets_skipped_missing_png": 0,
        "boxes_written": 0,
        "boxes_skipped_unmapped": 0,
        "boxes_skipped_degenerate": 0,
        "sheets_kept_empty": 0,
        "tiles_written": 0,
        "tiles_kept_empty": 0,
    }
    for js in sheets:
        stem = js.name[: -len(".gt.json")]
        png = in_dir / f"{stem}.png"
        if not png.exists():
            summary["sheets_skipped_missing_png"] += 1
            continue
        gt = json.loads(js.read_text())
        sheet_w, sheet_h = gt["sheet_size_px"]
        split = "val" if stem in val_stems else "train"
        if tile_size > 0:
            from PIL import Image

            symbols = gt.get("symbols", [])
            summary["boxes_skipped_unmapped"] += sum(
                1 for sym in symbols if SYMBOL_CLASS.get(sym.get("type")) is None
            )
            img = Image.open(png)
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
        shutil.copyfile(png, out_dir / "images" / split / f"{stem}.png")
        (out_dir / "labels" / split / f"{stem}.txt").write_text("".join(r + "\n" for r in rows))
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
    args = ap.parse_args()
    summary = convert(args.in_dir, args.out, args.val_fraction, args.seed, args.include_empty)
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
