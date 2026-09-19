#!/usr/bin/env python3
"""CLI: generate Tier-1 synthetic data into synth/out/.

  python -m synth.make_dataset --sheets 5 --crops-per-class 200 \
      --out synth/out --seed 0

Writes:
  out/sheets/sheet_<seed>.png + sheet_<seed>.gt.json
  out/crops/<Class_Name>/crop_<i>.png
  out/crops_manifest.csv   (path,label,source)
  out/README.txt
"""
from __future__ import annotations

import argparse
from pathlib import Path

import sys as _sys
sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in _sys.path:
    _sys.path.insert(0, sys_path)

from synth.symbols import make_symbol_dataset, save_crops, AEC_CLASSES
from synth.sheets import save_sheet


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic CAD data")
    ap.add_argument("--sheets", type=int, default=5)
    ap.add_argument("--crops-per-class", type=int, default=200)
    ap.add_argument("--out", default="synth/out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--classes", nargs="*", default=AEC_CLASSES)
    args = ap.parse_args()

    out = Path(args.out)
    sheet_dir = out / "sheets"
    crop_dir = out / "crops"

    print(f"generating {args.sheets} sheets -> {sheet_dir}", flush=True)
    for i in range(args.sheets):
        png, js = save_sheet(args.seed + i, sheet_dir)
        print(f"  {png.name} + {js.name}", flush=True)

    print(f"generating {args.crops_per_class}/class x {len(args.classes)} "
          f"crops -> {crop_dir}", flush=True)
    X, y = make_symbol_dataset(args.classes, args.crops_per_class,
                               seed=args.seed + 1000)
    manifest = save_crops(X, y, args.classes, crop_dir)
    print(f"  {len(y)} crops, manifest {manifest}", flush=True)

    (out / "README.txt").write_text(
        "Tier-1 synthetic CAD data.\n"
        f"sheets/: {args.sheets} floor plans (PNG + .gt.json ground truth)\n"
        f"crops/: {len(y)} symbol crops, manifest crops_manifest.csv\n"
        f"seed={args.seed} classes={','.join(args.classes)}\n")
    print("done", flush=True)


if __name__ == "__main__":
    main()
