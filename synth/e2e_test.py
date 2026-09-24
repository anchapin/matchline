#!/usr/bin/env python3
"""End-to-end pipeline test on synthetic sheets.

For each of 3+ generated sheets, runs the full takeoff chain and asserts
against the sheet's perfect ground truth:

  1. detections: GT bboxes -> crop -> WiSARD (synthetic-trained) classify
  2. schedule:   GT schedule -> parse_schedule_csv
  3. rollup:     rollup_takeoff -> window/door areas within 1% of GT
  4. floor area: room polygons -> measure_takeoff within 1% of GT
  5. labels:     GT text boxes -> room_labels.assign -> names/numbers match
  6. simplify:   footprint -> simplify_ring(tol=2%) keeps area in budget

Detection tags come from GT (v1: no tag-OCR yet -- documented gap).
Room-label text boxes come from GT (deterministic); a best-effort real-OCR
spot check runs if rapidocr is importable.

Exit 0 iff every sheet passes every check.
"""

from __future__ import annotations

import csv
import io
import sys

from datasets_adapter import (
    Detection,
    DrawingScale,
    Region,
    measure_takeoff,
    normalize_crop,
    parse_schedule_csv,
    rollup_takeoff,
)
from geometry_simplify import footprint_from_regions, simplify_ring
from jesse import WisardClassifier, make_tuple_indices
from room_labels import LabeledSpace, TextBox, label_spaces_from_sheet
from synth.sheets import PX_PER_M, generate_sheet
from synth.symbols import AEC_CLASSES, make_symbol_dataset

SEEDS = [101, 102, 103]
TOL = 0.01  # 1% takeoff tolerance


def sheet_schedule_csv(gt) -> io.StringIO:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["tag", "category", "width_m", "height_m"])
    for s in gt["schedule"]:
        wr.writerow([s["tag"], s["category"], s["width_m"], s["height_m"]])
    buf.seek(0)
    return buf


def run_sheet(seed: int, clf) -> tuple[bool, dict]:
    img, gt = generate_sheet(seed)
    H, W = img.shape
    report = {"sheet": gt["sheet_id"], "checks": {}}
    ok = True

    # -- 1+2+3: detections -> schedule -> rollup -------------------------------
    dets = []
    correct = 0
    for s in gt["symbols"]:
        xtl, ytl, xbr, ybr = [int(round(v)) for v in s["bbox_px"]]
        xtl, ytl = max(0, xtl), max(0, ytl)
        xbr, ybr = min(W, xbr), min(H, ybr)
        crop = normalize_crop(img[ytl:ybr, xtl:xbr], size=28)
        pred = clf.predict_logodds(crop[None], alpha=0.1)[0]
        label = AEC_CLASSES[pred]
        correct += label == s["type"]
        dets.append(
            Detection(
                label=label,
                tag=s["tag"],
                score=1.0,
                bbox=tuple(s["bbox_px"]),
                source=f"synth:{gt['sheet_id']}",
                drawing_type="floor_plan",
            )
        )
    cls_acc = correct / len(dets)
    report["checks"]["classification_acc"] = round(cls_acc, 4)
    if cls_acc < 1.0:
        ok = False
        report["checks"]["classification_FAIL"] = f"{correct}/{len(dets)} symbols correct"

    sched = parse_schedule_csv(sheet_schedule_csv(gt))
    res = rollup_takeoff(dets, sched)
    for cat, key in (("window", "window_area_m2"), ("door", "door_area_m2")):
        got = res.area_m2.get(cat, 0.0)
        want = gt["expected"][key]
        err = abs(got - want) / want if want else (0.0 if got == 0 else 1.0)
        report["checks"][f"{cat}_area_m2"] = (round(got, 3), round(want, 3), f"err={err:.4%}")
        if err > TOL:
            ok = False
            report["checks"][f"{cat}_area_FAIL"] = f"err {err:.2%} > 1%"
    if res.unmatched:
        ok = False
        report["checks"]["unmatched_FAIL"] = len(res.unmatched)

    # -- 4: floor area from room polygons ---------------------------------------
    regions = [
        Region(
            "floor_area", r["polygon_px"], f"synth:{gt['sheet_id']}", "floor_plan", label=r["name"]
        )
        for r in gt["rooms"]
    ]
    tres = measure_takeoff(regions, "floor_plan", DrawingScale(1.0 / PX_PER_M, "synthetic"))
    got = tres.area_m2["floor_area"]
    want = gt["expected"]["floor_area_m2"]
    err = abs(got - want) / want
    report["checks"]["floor_area_m2"] = (round(got, 2), round(want, 2), f"err={err:.4%}")
    if err > TOL:
        ok = False
        report["checks"]["floor_area_FAIL"] = f"err {err:.2%} > 1%"

    # -- 5: room labels from GT text boxes --------------------------------------
    spaces = [
        LabeledSpace(polygon_px=r["polygon_px"], source=f"synth:{gt['sheet_id']}")
        for r in gt["rooms"]
    ]
    pre = [TextBox(text=l["text"], bbox=tuple(l["bbox_px"]), confidence=1.0) for l in gt["labels"]]
    labeled = label_spaces_from_sheet(img, spaces, pre_detected=pre)
    mism = 0
    for sp, r in zip(labeled.spaces, gt["rooms"]):
        if sp.name != r["name"] or sp.number != r["number"]:
            mism += 1
    report["checks"]["room_labels"] = (
        f"{labeled.n_labeled}/{labeled.n_total} labeled, {mism} mismatches"
    )
    if mism or labeled.n_labeled != labeled.n_total:
        ok = False
        report["checks"]["room_labels_FAIL"] = True

    # -- 6: geometry simplification ----------------------------------------------
    ring = footprint_from_regions([r["polygon_px"] for r in gt["rooms"]])
    sres = simplify_ring(ring, tol=0.02, wall_height=3.0)
    report["checks"]["simplify"] = (
        f"{sres.original_count}->{sres.simplified_count} surfaces, dA={sres.area_delta_pct:.3f}%"
    )
    if not sres.valid or abs(sres.area_delta_pct) > 2.0:
        ok = False
        report["checks"]["simplify_FAIL"] = True

    # -- optional: real OCR spot check --------------------------------------------
    try:
        from room_labels import detect_text

        boxes = detect_text(img, min_conf=0.30)
        n_roomy = sum(
            1
            for b in boxes
            if any(
                k in b.text.upper()
                for k in (
                    "OFFICE",
                    "CONF",
                    "LOBBY",
                    "ROOM",
                    "KITCHEN",
                    "STORAGE",
                    "CORRIDOR",
                    "MECH",
                    "ELEC",
                    "RECEPTION",
                    "FILE",
                    "BREAK",
                )
            )
        )
        report["checks"]["ocr_spot"] = f"{len(boxes)} text boxes, {n_roomy} room-like"
    except Exception as e:  # rapidocr missing -> informational only
        report["checks"]["ocr_spot"] = f"skipped ({type(e).__name__})"

    report["pass"] = ok
    return ok, report


def main():
    print("training WiSARD on synthetic crops ...", flush=True)
    X, y = make_symbol_dataset(AEC_CLASSES, n_per_class=400, seed=5)
    clf = WisardClassifier(len(AEC_CLASSES), tuple_idx=make_tuple_indices(28, 28, seed=42))
    clf.fit(X, y)
    print(f"trained on {len(y)} crops", flush=True)

    all_ok = True
    for seed in SEEDS:
        ok, rep = run_sheet(seed, clf)
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {rep['sheet']}", flush=True)
        for k, v in rep["checks"].items():
            print(f"    {k}: {v}", flush=True)
    print("E2E:", "ALL PASS" if all_ok else "FAILURES PRESENT", flush=True)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
