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

Known limits (the scores are honest within these): the scratch provider reads
a whole sheet in one letterboxed pass, so a 1 m door shrinks to a few pixels
at the model's input size, and ``raster_sheet`` draws no text. Both are
provider/rendering design, not detector quality; point ``--provider`` at a
tiling config to compare.
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

PX_PER_M = 8.0  # raster scale of the window sheets (1:100 at ~100 dpi)


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


def _png(img, kind, storey, png_dir, tmp):
    """Save one raster for the provider to read; ``png_dir`` keeps it to look at."""
    name = f"{storey.lower().replace(' ', '_')}_{kind}.png"
    path = Path(png_dir) / name if png_dir else Path(tmp) / name
    if png_dir:
        Path(png_dir).mkdir(parents=True, exist_ok=True)
    img.save(path)
    return str(path)


def score_storeys(loaded, provider, px_per_m=PX_PER_M, png_dir=None):
    """Per storey: window and door scores of the provider's pixel detections.

    ``loaded`` is ``load(arch_path, with_windows=True, with_doors=True)``. The
    provider sees the window sheet (walls with glass lines) and the door sheet
    (walls with swings) once each per storey.
    """
    from PIL import Image

    walls, _wall_len, rooms, _bound, windows, doors = loaded
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for st in PLAN_STOREYS:
            px_per_pt = px_per_m * M_PER_PT
            sheet, off = sheet_for(walls[st], rooms[st], glazing=windows[st])
            img = raster_sheet(sheet, px_per_pt)
            wdets = provider.detect(_png(img, "windows", st, png_dir, tmp), st)
            win = match_windows(windows[st], wdets, sheet["height_pt"], px_per_pt, off)
            dimg, to_px = door_sheet(walls[st], doors[st], px_per_m=DOOR_PPM)
            ddets = provider.detect(_png(Image.fromarray(dimg), "doors", st, png_dir, tmp), st)
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
    ap.add_argument("--px-per-m", type=float, default=PX_PER_M)
    ap.add_argument("--json", type=Path, help="write the summary and per-storey scores here")
    ap.add_argument("--png-dir", type=Path, help="keep the rendered eval sheets here")
    args = ap.parse_args(argv)
    if not args.provider and not args.weights:
        ap.error("pass --provider CONFIG.json or --weights CKPT.pt")
    prov = provider_from_config(args.provider or {"provider": "scratch", "weights": args.weights})
    loaded = load(args.arch, with_windows=True, with_doors=True)
    out = score_storeys(loaded, prov, args.px_per_m, args.png_dir)
    print(json.dumps(summarize(out), indent=2))
    for st, row in out.items():
        print(st, json.dumps(row))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"summary": summarize(out), "storeys": out}, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
