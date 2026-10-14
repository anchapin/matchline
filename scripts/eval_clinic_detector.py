"""Score a detector provider on sheets drawn from the BSI Clinic IFC (#743).

Synthetic val F1 says nothing about real sheets. This draws the clinic's real
wall geometry the way matchline draws a plan (``validate_clinic_walls``: 1:100
cut outlines, window glass lines, door swings) and scores the provider's pixel
detections against the IFC openings. Both renderings are our drawing choices,
so this measures the detector on real geometry, not a real set's drafting.

    python scripts/eval_clinic_detector.py /path/Clinic_Architectural.ifc \
        --weights /path/ckpt.pt --json out/clinic_eval.json [--png-dir out/]

``--provider CONFIG.json`` runs any detection_provider config instead of the
scratch backend. The IFC is never committed; the development machine runs
this, CI never sees the file.

By default the eval rasterizes at a training-matched scale and slides
640 px tiles over the sheet (``--tile-px`` 0 restores the old whole-sheet
pass, which letterboxes a 1 m door down to a few pixels at the model's input
size, the scale mismatch the 2026-10-12 run 38337650412 measured).
``raster_sheet`` still draws no text.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detection_provider import provider_from_config  # noqa: E402
from scripts.validate_clinic_walls import (  # noqa: E402
    DOOR_PPM,
    M_PER_PT,
    PLAN_STOREYS,
    door_sheet,
    load,
    match_doors,
    match_windows,
    sheet_for,
)


class _Det:
    """Minimal Detection stand-in for tiled detections."""

    __slots__ = ("label", "bbox")

    def __init__(self, label, bbox):
        self.label = label
        self.bbox = bbox


PX_PER_M = 8.0  # raster scale of the old whole-sheet pass (1:100 at ~100 dpi)
TILE_PX = 640  # detector input size; tiles are this square
TILE_STRIDE = 512  # 128 px overlap between neighbouring tiles
TILE_PX_PER_M = 50.0  # training-matched raster scale (synth trains at ~77, DOOR_PPM 50)


def raster_sheet(sheet, px_per_pt, line_width=1):
    """Grayscale pixels of one vector sheet.

    Walls are thin strokes and glazing thin lines, the way ``sheet_for`` draws
    them; the detections come back in these pixels.
    """
    from PIL import Image, ImageDraw

    w = int(round(sheet["width_pt"] * px_per_pt))
    h = int(round(sheet["height_pt"] * px_per_pt))
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for prim in sheet["primitives"]:
        pts = []
        for kind, args in prim["segments"]:
            if kind != "Z":
                x, y = args[0]
                pts.append((x * px_per_pt, y * px_per_pt))
        if prim["kind"] == "line":
            if len(pts) >= 2:
                d.line(pts, fill=0, width=line_width)
        elif len(pts) >= 2:
            d.line(pts + [pts[0]], fill=0, width=line_width)
    return img


def tile_starts(n, tile_px, stride):
    """Window origins along one axis, tail-inclusive, deduplicated."""
    if tile_px <= 0 or n <= tile_px:
        return [0]
    starts = list(range(0, n - tile_px + 1, stride))
    if starts[-1] + tile_px < n:
        starts.append(n - tile_px)
    return sorted(set(starts))


def nms_dedupe(dets, iou=0.5):
    """Greedy per-label NMS over remapped detections; keeps the best score.

    Tiles overlap (stride < tile_px), so one opening can be spotted twice;
    the matchers count detections, so the duplicates must fold back into one.
    """
    keep = []
    for label in sorted({d["label"] for d in dets}):
        group = sorted(
            (d for d in dets if d["label"] == label),
            key=lambda d: -d["score"],
        )
        for d in group:
            if all(_iou(d["bbox"], k["bbox"]) < iou for k in keep if k["label"] == label):
                keep.append(d)
    return keep


def _iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area = (
        max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        - inter
    )
    return inter / area if area > 0 else 0.0


def detect_tiled(provider, img, sheet_id, tile_px=TILE_PX, stride=TILE_STRIDE, nms_iou=0.3):
    """Run the provider over sliding tiles; detections back in sheet pixels."""

    w, h = img.size
    out = []
    for y0 in tile_starts(h, tile_px, stride):
        for x0 in tile_starts(w, tile_px, stride):
            tw = min(tile_px, w - x0)
            th = min(tile_px, h - y0)
            tile = img.crop((x0, y0, x0 + tw, y0 + th))
            out.extend(_detect_tile(provider, tile, sheet_id, x0, y0))
    return nms_dedupe(out, iou=nms_iou)


def _detect_tile(provider, tile, sheet_id, x0, y0):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"tile_{x0}_{y0}.png"
        tile.convert("L").save(p)
        return [
            {
                "label": d.label,
                "score": d.score,
                "bbox": (d.bbox[0] + x0, d.bbox[1] + y0, d.bbox[2] + x0, d.bbox[3] + y0),
            }
            for d in provider.detect(str(p), sheet_id)
        ]


def _png(img, kind, storey, png_dir, tmp):
    """Save one raster for the provider to read; ``png_dir`` keeps it to look at."""
    name = f"{storey.lower().replace(' ', '_')}_{kind}.png"
    path = Path(png_dir) / name if png_dir else Path(tmp) / name
    if png_dir:
        Path(png_dir).mkdir(parents=True, exist_ok=True)
    img.save(path)
    return str(path)


def score_storeys(
    loaded,
    provider,
    px_per_m=PX_PER_M,
    png_dir=None,
    tile_px=TILE_PX,
    stride=TILE_STRIDE,
    window_px_per_m=0.0,
    raw_out=None,
    nms_iou=0.3,
):
    """Per storey: window and door scores of the provider's pixel detections.

    ``loaded`` is ``load(arch_path, with_windows=True, with_doors=True)``. With
    ``raw_out``, the raw detections (label, score, bbox) land there per storey
    so a conf sweep can rescore a dump offline without re-running the provider.
    ``nms_iou`` is the tiled-detection overlap threshold: the 2026-10-13 dump
    sweep showed recall 1.0 at every IoU while halving the false positives,
    so the tiled pass folds at 0.3 by default.
    The
    provider sees the window sheet (walls with glass lines) and the door sheet
    (walls with swings) once each per storey.
    """
    from PIL import Image

    walls, _wall_len, rooms, _bound, windows, doors = loaded
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for st in PLAN_STOREYS:
            # Window pass: --window-px-per-m > 0 rasters just the window
            # sheet at a higher zoom (small symbols render larger inside the
            # same 640 px tile); the door pass draws at DOOR_PPM regardless.
            win_ppm = window_px_per_m if window_px_per_m > 0 else px_per_m
            win_px_per_pt = win_ppm * M_PER_PT
            sheet, off = sheet_for(walls[st], rooms[st], glazing=windows[st])
            img = raster_sheet(sheet, win_px_per_pt)
            if tile_px > 0:
                raw_w = detect_tiled(provider, img, st, tile_px, stride, nms_iou=nms_iou)
                wdets = [_Det(d["label"], d["bbox"]) for d in raw_w]
            else:
                wdets = provider.detect(_png(img, "windows", st, png_dir, tmp), st)
                raw_w = [{"label": d.label, "score": d.score, "bbox": list(d.bbox)} for d in wdets]
            win = match_windows(windows[st], wdets, sheet["height_pt"], win_px_per_pt, off)
            dimg, to_px = door_sheet(walls[st], doors[st], px_per_m=DOOR_PPM)
            dimg_img = Image.fromarray(dimg)
            if tile_px > 0:
                raw_d = detect_tiled(provider, dimg_img, st, tile_px, stride, nms_iou=nms_iou)
                ddets = [_Det(d["label"], d["bbox"]) for d in raw_d]
            else:
                ddets = provider.detect(_png(dimg_img, "doors", st, png_dir, tmp), st)
                raw_d = [{"label": d.label, "score": d.score, "bbox": list(d.bbox)} for d in ddets]
            if raw_out is not None:
                raw_out[st] = {"windows": raw_w, "doors": raw_d}
            out[st] = {
                "windows": win,
                "doors": match_doors(
                    doors[st],
                    [{"bbox_px": d.bbox} for d in ddets if d.label == "door"],
                    to_px,
                ),
            }
    return out


def mean_metric(rows, key):
    """Mean of one metric across storeys that have it (None = no truth there)."""
    vals = [r[key] for r in rows if r[key] is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def summarize(out):
    """The headline: window and door recall/precision averaged over the storeys."""
    win = [v["windows"] for v in out.values()]
    door = [v["doors"] for v in out.values()]
    return {
        "windows": {k: mean_metric(win, k) for k in ("recall", "precision")},
        "doors": {k: mean_metric(door, k) for k in ("recall", "precision")},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arch", help="path to Clinic_Architectural.ifc (never committed)")
    ap.add_argument("--provider", help="detection provider config JSON (overrides --weights)")
    ap.add_argument("--weights", help="scratch checkpoint for the default provider")
    ap.add_argument("--px-per-m", type=float, default=TILE_PX_PER_M)
    ap.add_argument("--tile-px", type=int, default=TILE_PX, help="0 = old whole-sheet pass")
    ap.add_argument("--stride", type=int, default=TILE_STRIDE)
    ap.add_argument(
        "--window-px-per-m",
        type=float,
        default=0.0,
        help="zoom for the window pass in px/m (0 = same as --px-per-m)",
    )
    ap.add_argument("--json", type=Path, help="write the summary and per-storey scores here")
    ap.add_argument("--png-dir", type=Path, help="keep the rendered eval sheets here")
    ap.add_argument(
        "--dump",
        type=Path,
        help="write raw detections (label, score, bbox) per storey here",
    )
    ap.add_argument(
        "--nms-iou",
        type=float,
        default=0.3,
        help="tiled-detection NMS IoU (0.5 restores the pre-sweep behavior)",
    )
    args = ap.parse_args(argv)
    if not args.provider and not args.weights:
        ap.error("pass --provider CONFIG.json or --weights CKPT.pt")
    prov = provider_from_config(args.provider or {"provider": "scratch", "weights": args.weights})
    loaded = load(args.arch, with_windows=True, with_doors=True)
    raw_out = {} if args.dump else None
    out = score_storeys(
        loaded,
        prov,
        args.px_per_m,
        args.png_dir,
        args.tile_px,
        args.stride,
        args.window_px_per_m,
        raw_out=raw_out,
        nms_iou=args.nms_iou,
    )
    print(json.dumps(summarize(out), indent=2))
    for st, row in out.items():
        print(st, json.dumps(row))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "summary": summarize(out),
                    "storeys": out,
                    "config": {
                        "px_per_m": args.px_per_m,
                        "tile_px": args.tile_px,
                        "stride": args.stride,
                        "window_px_per_m": args.window_px_per_m,
                        "nms_iou": args.nms_iou,
                    },
                },
                indent=2,
            )
            + "\n"
        )
    if args.dump:
        args.dump.parent.mkdir(parents=True, exist_ok=True)
        args.dump.write_text(
            json.dumps(
                {
                    "config": {
                        "px_per_m": args.px_per_m,
                        "tile_px": args.tile_px,
                        "stride": args.stride,
                        "window_px_per_m": args.window_px_per_m,
                    },
                    "storeys": raw_out,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
