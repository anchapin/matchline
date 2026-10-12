"""Score the door-swing detector on AEC-geometric-bench sheets.

Usage:
    python scripts/eval_door_swings.py --aec-bench <dataset dir> [--dpi 200]
        [--ink-max 100] [--out door_swing_eval.json]

For each sheet: rasterize the PDF, estimate the drawing scale from the
annotated door boxes (title blocks are redacted, so a 0.9 m leaf is the only
reference; the annotations are used for scale only), run
``door_detect.detect_door_swings`` on the grayscale page, and match each
detection to an annotated door ("Single Swing Door" / "Double Swing Door").
A detection matches when its opening centre falls inside an annotated box
grown by 15 % of the box's longer side; each box matches at most once.

Prints one line per sheet plus totals and writes the JSON report.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

GROW = 0.15


def _box(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def match(dets: list[dict], boxes: list[tuple]) -> tuple[int, int, int]:
    """(true positives, false positives, misses)."""
    used: set[int] = set()
    tp = 0
    for d in sorted(dets, key=lambda d: -d["score"]):
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            if i in used:
                continue
            g = GROW * max(x1 - x0, y1 - y0)
            if x0 - g <= d["x_px"] <= x1 + g and y0 - g <= d["y_px"] <= y1 + g:
                used.add(i)
                tp += 1
                break
    return tp, len(dets) - tp, len(boxes) - tp


def evaluate(root: Path, dpi: int = 200, ink_max: int = 100) -> dict:
    from datasets_adapter import _rasterize_pdf, load_aec_bench, scale_from_reference
    from door_detect import detect_door_swings

    _, takeoff = load_aec_bench(root, dpi=dpi)
    by_sheet: dict[str, list] = {}
    for r in takeoff.regions:
        if r.category == "door":
            by_sheet.setdefault(r.source.split(":", 1)[1], []).append(r)
    sheets = []
    for stem in sorted(by_sheet):
        regions = by_sheet[stem]
        scale = scale_from_reference(regions, "door", 0.9)
        if not scale.m_per_px:
            continue
        page = _rasterize_pdf(root / "pdf" / f"{stem}.pdf", dpi)
        gray = page[..., :3].mean(axis=2).astype(np.uint8) if page.ndim == 3 else page
        ppm = 1.0 / scale.m_per_px
        t = time.time()
        dets = detect_door_swings(gray, ppm, ink_max=ink_max)
        secs = time.time() - t
        tp, fp, fn = match(dets, [_box(r.polygon_px) for r in regions])
        sheets.append(
            {
                "sheet": stem,
                "px_per_m": round(ppm, 2),
                "annotated": len(regions),
                "detected": len(dets),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "seconds": round(secs, 1),
            }
        )
    tot = {k: sum(s[k] for s in sheets) for k in ("annotated", "detected", "tp", "fp", "fn")}
    tot["recall"] = round(tot["tp"] / tot["annotated"], 3) if tot["annotated"] else None
    tot["precision"] = round(tot["tp"] / tot["detected"], 3) if tot["detected"] else None
    return {"dpi": dpi, "ink_max": ink_max, "grow": GROW, "sheets": sheets, "total": tot}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score door-swing detection on AEC-Bench sheets.")
    ap.add_argument("--aec-bench", type=Path, required=True)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--ink-max", type=int, default=100)
    ap.add_argument("--out", type=Path, default=Path("door_swing_eval.json"))
    a = ap.parse_args(argv)
    rep = evaluate(a.aec_bench, a.dpi, a.ink_max)
    for s in rep["sheets"]:
        print(
            f"{s['sheet']}: {s['tp']}/{s['annotated']} found, {s['fp']} false, "
            f"{s['px_per_m']} px/m, {s['seconds']} s"
        )
    t = rep["total"]
    print(
        f"TOTAL recall {t['recall']} precision {t['precision']} "
        f"({t['tp']}/{t['annotated']}, {t['fp']} false)"
    )
    a.out.write_text(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
