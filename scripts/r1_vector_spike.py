"""R1 vector spike (#973): what a real plan sheet exposes, and what parses.

Repro for the spike outcome recorded in docs/r1_vector_spike.md. Three probes
on one PDF page:

1. ``inventory`` counts the vector primitives (lines, rects, curves, chars)
   pdfplumber sees, the input alphabet R1 starts from.
2. ``vector_rooms`` builds a stroke mask from those primitives, dilates and
   closes it into wall thickness, flood-fills the white space, and reports
   the regions that are plausible room interiors (area between 0.1% and 12%
   of the page). This is the "rooms as closed loops" spike prototype.
3. ``raster_doors`` renders the page and runs the shipped raster door-swing
   detector across the paper scales (px/m at 144 dpi: ~59 at 1/8"=1'-0",
   ~118 at 1/4"=1'-0"), the raster counterpart.

Usage:
    python scripts/r1_vector_spike.py --pdf sheet.pdf --page 3
    python scripts/r1_vector_spike.py --pdf sheet.pdf --page 3 --probes inventory,vector_rooms
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOM_MIN_FRAC = 0.001
ROOM_MAX_FRAC = 0.12
# px/m at 144 dpi for common paper scales: px_per_m = 144 / (inches per m)
SCALE_PPM = {"1/8": 59.0, "1/4": 118.0}


def _stroke_mask(page, scale: float = 3.0) -> np.ndarray:
    h_pt = int(page.height * scale)
    w_pt = int(page.width * scale)
    mask = np.zeros((h_pt, w_pt), dtype=bool)

    def seg(a, b):
        n = max(2, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * scale))
        xs = np.linspace(a[0], b[0], n)
        ys = np.linspace(a[1], b[1], n)
        xi = np.clip((xs * scale).astype(int), 0, w_pt - 1)
        yi = np.clip((ys * scale).astype(int), 0, h_pt - 1)
        mask[yi, xi] = True

    for line in page.lines:
        seg((line["x0"], line["top"]), (line["x1"], line["bottom"]))
    for rect in page.rects:
        seg((rect["x0"], rect["top"]), (rect["x1"], rect["top"]))
        seg((rect["x1"], rect["top"]), (rect["x1"], rect["bottom"]))
        seg((rect["x1"], rect["bottom"]), (rect["x0"], rect["bottom"]))
        seg((rect["x0"], rect["bottom"]), (rect["x0"], rect["top"]))
    for curve in page.curves:
        pts = curve.get("pts") or []
        for i in range(len(pts) - 1):
            seg(pts[i], pts[i + 1])
    return mask


def probe_inventory(page) -> dict:
    return {
        "lines": len(page.lines),
        "rects": len(page.rects),
        "curves": len(page.curves),
        "chars": len(page.chars),
        "images": len(page.images),
    }


def probe_vector_rooms(page) -> dict:
    from scipy import ndimage

    mask = _stroke_mask(page)
    st = ndimage.generate_binary_structure(2, 1)
    fat = ndimage.binary_closing(
        ndimage.binary_dilation(mask, structure=st, iterations=2), structure=st, iterations=2
    )
    labels, n = ndimage.label(~fat)
    sizes = np.bincount(labels.ravel())[1:]
    total = mask.size
    room_like = sorted(int(s) for s in sizes if total * ROOM_MIN_FRAC < s < total * ROOM_MAX_FRAC)
    return {
        "mask_px": int(mask.sum()),
        "mask_frac": round(float(mask.mean()), 4),
        "regions": int(n),
        "room_like": len(room_like),
        "room_like_px_desc": room_like[:8],
    }


def probe_raster_doors(pdf_path: Path, page_index: int) -> list[dict]:
    import pypdfium2 as pdfium

    from door_detect import detect_door_swings

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        bmp = pdf[page_index].render(scale=2).to_numpy()
    finally:
        pdf.close()
    gray = (0.299 * bmp[..., 0] + 0.587 * bmp[..., 1] + 0.114 * bmp[..., 2]).astype(np.uint8)
    del bmp
    out = []
    for label, ppm in SCALE_PPM.items():
        doors = detect_door_swings(gray, ppm, ink_max=100)
        out.append({"scale": label, "px_per_m": ppm, "n_doors": len(doors)})
    return out


def main() -> None:
    # runnable as a file: put the repo root on sys.path for door_detect
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--page", type=int, required=True, help="1-based page number")
    ap.add_argument(
        "--probes",
        default="inventory,vector_rooms,raster_doors",
        help="comma list of probes to run",
    )
    args = ap.parse_args()

    import pdfplumber

    wanted = {p.strip() for p in args.probes.split(",")}
    result: dict = {"pdf": str(args.pdf), "page": args.page, "probes": {}}
    with pdfplumber.open(args.pdf) as pdf:
        page = pdf.pages[args.page - 1]
        if "inventory" in wanted:
            result["probes"]["inventory"] = probe_inventory(page)
        if "vector_rooms" in wanted:
            result["probes"]["vector_rooms"] = probe_vector_rooms(page)
    if "raster_doors" in wanted:
        result["probes"]["raster_doors"] = probe_raster_doors(args.pdf, args.page - 1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
