#!/usr/bin/env python3
"""Demo: synthetic sheet GT -> takeoff pipeline -> gbXML + IFC4 export.

Builds a TakeoffResult from sheet_007's ground truth (detections with GT
tags -> schedule CSV -> rollup), labels rooms from GT text boxes, simplifies
the envelope, then exports gbXML (validated against the 6.01 XSD) and IFC4
(validated structurally) and prints the headline numbers.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

from bem_export import model_from_takeoff, validate_gbxml, validate_ifc4, write_gbxml, write_ifc4
from datasets_adapter import Detection, DrawingScale, parse_schedule_csv, rollup_takeoff
from geometry_simplify import footprint_from_regions, simplify_report, simplify_ring
from room_labels import LabeledSpace, TextBox, label_spaces_from_sheet

WALL_H_M = 3.0
REPO_ROOT = Path(__file__).resolve().parent
OUT = REPO_ROOT / "bem_out"


def sheet_schedule_csv(gt) -> io.StringIO:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["tag", "category", "width_m", "height_m"])
    for s in gt["schedule"]:
        wr.writerow([s["tag"], s["category"], s["width_m"], s["height_m"]])
    buf.seek(0)
    return buf


def main(sheet_id: str = "sheet_007"):
    OUT.mkdir(exist_ok=True)
    gt = json.load(open(REPO_ROOT / "synth" / "out" / "sheets" / f"{sheet_id}.gt.json"))
    s = 1.0 / gt["scale_px_per_m"]
    src = f"synth:{sheet_id}"

    # 1-3: detections (GT tags) -> schedule -> rollup
    dets = [
        Detection(
            label=x["type"],
            tag=x["tag"],
            score=1.0,
            bbox=tuple(x["bbox_px"]),
            source=src,
            drawing_type="floor_plan",
        )
        for x in gt["symbols"]
    ]
    sched = parse_schedule_csv(sheet_schedule_csv(gt))
    takeoff = rollup_takeoff(dets, sched)
    takeoff.scale = DrawingScale(s, "synthetic sheet ground truth")

    # 4: room labels from GT text boxes
    spaces = [LabeledSpace(polygon_px=r["polygon_px"], source=src) for r in gt["rooms"]]
    pre = [TextBox(text=l["text"], bbox=tuple(l["bbox_px"]), confidence=1.0) for l in gt["labels"]]
    labeled = label_spaces_from_sheet(None, spaces, pre_detected=pre)

    # 5: envelope simplification
    ring = footprint_from_regions([r["polygon_px"] for r in gt["rooms"]])
    sres = simplify_ring(ring, tol=0.02, wall_height=WALL_H_M)
    rep = simplify_report(sres)

    # 6: BEM model + exports
    model = model_from_takeoff(
        takeoff, labeled, sres, wall_height_m=WALL_H_M, building_name=f"Jesse Demo {sheet_id}"
    )
    gbxml_path = write_gbxml(model, OUT / f"{sheet_id}.xml")
    gb_ok, gb_errors = validate_gbxml(gbxml_path)
    ifc_path = write_ifc4(model, OUT / f"{sheet_id}.ifc")
    ifc_ok, ifc_errors = validate_ifc4(ifc_path)

    # 7: report
    n_surf = gbxml_path.read_text().count("<Surface ")
    n_open = gbxml_path.read_text().count("<Opening ")
    print(f"sheet: {sheet_id}")
    print(f"spaces: {len(model.spaces)} (labeled {labeled.n_labeled}/{labeled.n_total})")
    print(f"gbXML surfaces: {n_surf} (walls {len(model.ring_m)} + roof + slab)")
    print(
        f"gbXML openings: {n_open} "
        f"(windows {sum(1 for o in model.openings if o.category == 'window')}, "
        f"doors {sum(1 for o in model.openings if o.category == 'door')})"
    )
    print(
        f"envelope area delta: {rep['area_delta_pct']:+.3f}% "
        f"(tol {rep['tolerance_pct']:.1f}%, "
        f"surface reduction {rep['reduction_pct']:.0f}%)"
    )
    print(
        f"floor area total: {sum(sp.area_m2 for sp in model.spaces):.1f} m2 "
        f"(GT {gt['expected']['floor_area_m2']:.1f} m2)"
    )
    print(f"gbXML XSD validation: {'PASS' if gb_ok else 'FAIL'}")
    for e in gb_errors[:10]:
        print(f"    {e}")
    print(f"IFC4 structural validation: {'PASS' if ifc_ok else 'FAIL'}")
    for e in ifc_errors[:10]:
        print(f"    {e}")
    print(f"files: {gbxml_path}, {ifc_path}")
    return gb_ok and ifc_ok


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "sheet_007"
    sys.exit(0 if main(sid) else 1)
