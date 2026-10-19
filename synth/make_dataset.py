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

from synth.sheets import save_sheet
from synth.symbols import AEC_CLASSES, make_symbol_dataset, save_crops


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic CAD data")
    ap.add_argument("--sheets", type=int, default=5)
    ap.add_argument("--crops-per-class", type=int, default=200)
    ap.add_argument("--out", default="synth/out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--classes", nargs="*", default=AEC_CLASSES)
    ap.add_argument(
        "--px-per-m-list",
        default="50",
        help="comma-separated sheet scales in px/m (e.g. 40,50,65 renders each "
        "layout at several scales for multi-scale training tiles)",
    )
    args = ap.parse_args()

    scales = []
    for tok in args.px_per_m_list.split(","):
        tok = tok.strip()
        if not tok:
            continue
        v = float(tok)
        if v <= 0 or int(v) != v:
            ap.error(f"--px-per-m-list entries must be positive integers, got {tok!r}")
        scales.append(int(v))
    scales = list(dict.fromkeys(scales))
    if not scales:
        ap.error("--px-per-m-list produced no scales")

    out = Path(args.out)
    sheet_dir = out / "sheets"
    crop_dir = out / "crops"

    print(f"generating {args.sheets} sheets -> {sheet_dir}", flush=True)
    for i in range(args.sheets):
        for s in scales:
            png, js = save_sheet(args.seed + i, sheet_dir, px_per_m=float(s))
            print(f"  {png.name} + {js.name}", flush=True)

    print(
        f"generating {args.crops_per_class}/class x {len(args.classes)} crops -> {crop_dir}",
        flush=True,
    )
    X, y = make_symbol_dataset(args.classes, args.crops_per_class, seed=args.seed + 1000)
    manifest = save_crops(X, y, args.classes, crop_dir)
    print(f"  {len(y)} crops, manifest {manifest}", flush=True)

    (out / "README.txt").write_text(
        "Tier-1 synthetic CAD data.\n"
        f"sheets/: {args.sheets} floor plans (PNG + .gt.json ground truth)\n"
        f"crops/: {len(y)} symbol crops, manifest crops_manifest.csv\n"
        f"seed={args.seed} classes={','.join(args.classes)} "
        f"px_per_m={','.join(str(s) for s in scales)}\n",
        encoding="utf-8",
    )
    print("done", flush=True)


if __name__ == "__main__":
    main()
