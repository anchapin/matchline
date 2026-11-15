#!/usr/bin/env python3
"""Demo: synthetic sheet GT -> takeoff pipeline -> gbXML + IFC4 export.

Builds a TakeoffResult from sheet_007's ground truth (detections with GT
tags -> schedule CSV -> rollup), labels rooms from GT text boxes, simplifies
the envelope, then exports gbXML (validated against the 8.01 XSD) and IFC4
(validated structurally) and prints the headline numbers.

Since #1404 the export is a *gated transaction*: both files are written to
staged files in the output directory, the validators and the geometric
checks run against them, and only a run that passes everything is published
(atomically, with a rollback). A failed run publishes nothing and leaves the
previous good export untouched. A successful run also writes a checksummed
``<sheet>.export.json`` manifest beside the exports.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

from bem.bem_export import model_from_takeoff
from bem.export_gate import publish_exports
from bem.export_manifest import (
    build_manifest,
    normalised_ifc_digest,
    sha256_bytes,
    sha256_file,
    write_manifest,
)
from datasets_adapter import Detection, DrawingScale, parse_schedule_csv, rollup_takeoff
from geometry.geometry_simplify import footprint_from_regions, simplify_report, simplify_ring
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


def _build_model(gt, sheet_id: str):
    """GT -> takeoff -> labels -> simplified envelope -> BEM model."""
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

    # 6: BEM model
    model = model_from_takeoff(
        takeoff, labeled, sres, wall_height_m=WALL_H_M, building_name=f"Jesse Demo {sheet_id}"
    )
    return model, rep


def _manifest_inputs(gt_path: Path, schedule_csv: str) -> dict:
    """The inputs of the export, hashed (the GT file and the schedule)."""
    return {
        "sheet_gt_json": {
            "path": str(gt_path),
            "sha256": sha256_file(gt_path),
            "bytes": gt_path.stat().st_size,
        },
        "schedule_csv": {
            "content": schedule_csv,
            "sha256": sha256_bytes(schedule_csv.encode("utf-8")),
            "bytes": len(schedule_csv.encode("utf-8")),
        },
    }


def _manifest_outputs(gate, out_dir: Path, sheet_id: str) -> dict:
    gbxml = out_dir / f"{sheet_id}.xml"
    ifc4 = out_dir / f"{sheet_id}.ifc"
    return {
        "gbxml": {
            "path": str(gbxml),
            "sha256": sha256_file(gbxml),
            "bytes": gbxml.stat().st_size,
            "digest_kind": "sha256-bytes",
        },
        "ifc4": {
            "path": str(ifc4),
            "normalised_sha256": normalised_ifc_digest(ifc4),
            "bytes": ifc4.stat().st_size,
            "digest_kind": "normalised-semantic",
        },
    }


def main(sheet_id: str = "sheet_007", gt_path: str | Path | None = None, out_dir=None):
    """Export one sheet through the gate. Returns True when it published."""
    out = Path(out_dir) if out_dir is not None else OUT
    out.mkdir(parents=True, exist_ok=True)
    gt_file = Path(gt_path) if gt_path is not None else (
        REPO_ROOT / "synth" / "out" / "sheets" / f"{sheet_id}.gt.json"
    )
    gt = json.load(open(gt_file))
    model, rep = _build_model(gt, sheet_id)

    # gated transaction: staged files -> validators + geometric checks ->
    # atomic publish (or nothing published at all)
    gate = publish_exports(model, out, sheet_id)
    schedule_csv = sheet_schedule_csv(gt).getvalue()

    print(f"sheet: {sheet_id}")
    print(f"spaces: {len(model.spaces)}")
    print(
        f"envelope area delta: {rep['area_delta_pct']:+.3f}% "
        f"(tol {rep['tolerance_pct']:.1f}%, "
        f"surface reduction {rep['reduction_pct']:.0f}%)"
    )
    print(f"gbXML XSD validation: {'PASS' if gate.gbxml_ok else 'FAIL'}")
    for e in gate.gbxml_errors[:10]:
        print(f"    {e}")
    print(f"IFC4 structural validation: {'PASS' if gate.ifc4_ok else 'FAIL'}")
    for e in gate.ifc4_errors[:10]:
        print(f"    {e}")
    for check in gate.checks:
        print(f"geometric check {check.name}: {check.status}")
        if check.status != "pass":
            print(f"    {check.detail}")
    if not gate.ok:
        print("export refused: nothing published")
        for e in gate.errors[:20]:
            print(f"    {e}")
        return False

    manifest = build_manifest(
        sheet=sheet_id,
        settings={
            "simplify_tolerance_pct": float(model.simplify_tolerance),
            "wall_height_m": float(model.wall_height_m),
            "building_name": model.building_name,
        },
        inputs=_manifest_inputs(gt_file, schedule_csv),
        outputs=_manifest_outputs(gate, out, sheet_id),
        validation={
            "gbxml": {"ok": bool(gate.gbxml_ok), "errors": list(gate.gbxml_errors)},
            "ifc4": {"ok": bool(gate.ifc4_ok), "errors": list(gate.ifc4_errors)},
            "geometric_checks": [c.as_dict() for c in gate.checks],
            "area_delta_pct": float(rep["area_delta_pct"]),
            "area_delta_tolerance_pct": float(rep["tolerance_pct"]),
        },
        replay_command=f"matchline bem-export --sheet-id {sheet_id}",
        repo_root=REPO_ROOT,
    )
    manifest_path = write_manifest(manifest, out / f"{sheet_id}.export.json")
    print(f"files: {gate.published['gbxml']}, {gate.published['ifc4']}")
    print(f"manifest: {manifest_path}")
    return True


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "sheet_007"
    sys.exit(0 if main(sid) else 1)
