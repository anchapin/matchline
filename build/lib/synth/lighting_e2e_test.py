#!/usr/bin/env python3
"""End-to-end lighting takeoff test on synthetic lighting sheets.

For each of 2+ generated sheets, runs the lighting pipeline and asserts
against perfect ground truth:

  1. fixtures: GT bboxes -> crop -> WiSARD (synthetic-trained) classify
     (>=90%; residual confusion is between conventionally near-identical
     glyph pairs disambiguated by tag text on real drawings)
  2. schedule:  GT schedule -> parse_lighting_schedule_csv
  3. takeoff:   lighting_takeoff -> total watts EXACT, per-room watts and
     LPD EXACT vs GT
  4. robustness: bogus-tag detection -> unmatched; off-plan detection ->
     unassigned (reported, never dropped)

Detection tags come from GT (v1: no tag-OCR yet -- documented gap).

Exit 0 iff every sheet passes every check.
"""

from __future__ import annotations

import sys

import numpy as np

from datasets_adapter import Detection, DrawingScale, normalize_crop, parse_lighting_schedule_csv
from jesse import WisardClassifier, make_tuple_indices
from lighting import lighting_takeoff, summarize_lighting
from room_labels import LabeledSpace
from synth.lighting import LIGHTING_CLASSES, LIGHTING_GLYPHS, lighting_schedule_csv
from synth.lighting_sheets import PX_PER_M, generate_lighting_sheet
from synth.symbols import render_symbol

SEEDS = [201, 202]


def train_lighting_classifier():
    X, y = [], []
    rng = np.random.default_rng(11)
    for i, name in enumerate(LIGHTING_CLASSES):
        for _ in range(250):
            X.append(render_symbol(name, rng, glyphs=LIGHTING_GLYPHS))
            y.append(i)
    X = np.stack(X)
    y = np.asarray(y, dtype=np.int64)
    clf = WisardClassifier(len(LIGHTING_CLASSES), tuple_idx=make_tuple_indices(28, 28, seed=42))
    clf.fit(X, y)
    return clf


def run_sheet(seed: int, clf) -> tuple[bool, dict]:
    img, gt = generate_lighting_sheet(seed)
    H, W = img.shape
    report = {"sheet": gt["sheet_id"], "checks": {}}
    ok = True

    # -- 1: classify fixture crops -------------------------------------------
    dets = []
    correct = 0
    for f in gt["fixtures"]:
        xtl, ytl, xbr, ybr = [int(round(v)) for v in f["bbox_px"]]
        xtl, ytl = max(0, xtl), max(0, ytl)
        xbr, ybr = min(W, xbr), min(H, ybr)
        crop = normalize_crop(img[ytl:ybr, xtl:xbr], size=28)
        pred = clf.predict_logodds(crop[None], alpha=0.1)[0]
        label = LIGHTING_CLASSES[pred]
        correct += label == f["type"]
        dets.append(
            Detection(
                label=label,
                tag=f["tag"],
                score=1.0,
                bbox=tuple(f["bbox_px"]),
                source=f"synth:{gt['sheet_id']}",
                drawing_type="floor_plan",
            )
        )
    acc = correct / len(dets)
    report["checks"]["classification_acc"] = round(acc, 4)
    # Bar is 90%, not 100%: at 28px, downlight-vs-pendant and troffer-vs-exit
    # are conventionally near-identical glyph pairs that real drawings
    # disambiguate via the adjacent schedule tag (v2 tag-OCR item). The
    # takeoff assertions below use GT tags -- the true pipeline contract.
    if acc < 0.90:
        ok = False
        report["checks"]["classification_FAIL"] = f"{correct}/{len(dets)}"

    # -- 2+3: schedule -> takeoff ----------------------------------------------
    sched = parse_lighting_schedule_csv(lighting_schedule_csv())
    assert len(sched) == 6 and sched["A"].watts == 45.0, "schedule parse"

    spaces = [
        LabeledSpace(
            polygon_px=r["polygon_px"],
            name=r["name"],
            number=r["number"],
            label_confidence=1.0,
            label_source="enclosed",
        )
        for r in gt["rooms"]
    ]
    areas = [r["area_m2"] for r in gt["rooms"]]
    scale = DrawingScale(1.0 / PX_PER_M, "synthetic sheet")
    res = lighting_takeoff(dets, sched, spaces, scale, space_areas_m2=areas)

    want_total = gt["expected"]["total_w"]
    report["checks"]["total_w"] = (round(res.total_w, 6), round(want_total, 6))
    if abs(res.total_w - want_total) > 1e-9:
        ok = False
        report["checks"]["total_w_FAIL"] = "mismatch"

    for rl, pr in zip(res.rooms, gt["expected"]["per_room"]):
        if (
            rl.name != pr["name"]
            or rl.number != pr["number"]
            or abs(rl.watts - pr["watts"]) > 1e-9
            or abs(rl.lpd_w_m2 - pr["lpd_w_m2"]) > 1e-12
        ):
            ok = False
            report["checks"][f"room_{pr['number']}_FAIL"] = (rl.watts, pr["watts"])
    # fixture counts per room from GT
    for i, pr in enumerate(gt["expected"]["per_room"]):
        n_gt = sum(1 for f in gt["fixtures"] if f["room_idx"] == i)
        if res.rooms[i].fixture_count != n_gt:
            ok = False
            report["checks"][f"room_{pr['number']}_count_FAIL"] = (res.rooms[i].fixture_count, n_gt)
    report["checks"]["rooms_ok"] = all(
        "FAIL" not in k for k in report["checks"] if k.startswith("room_")
    )

    # -- 4: unmatched + unassigned handling --------------------------------------
    bad = Detection(
        label="Troffer 2x4", tag="ZZ", score=1.0, bbox=(10, 10, 40, 40), source="synth:test"
    )
    off = Detection(
        label="Downlight",
        tag="C",
        score=1.0,
        bbox=(W - 60, 10, W - 10, 60),  # schedule-table area
        source="synth:test",
    )
    res2 = lighting_takeoff(dets + [bad, off], sched, spaces, scale, space_areas_m2=areas)
    if not (len(res2.unmatched) == 1 and res2.unmatched[0].tag == "ZZ"):
        ok = False
        report["checks"]["unmatched_FAIL"] = len(res2.unmatched)
    if not (len(res2.unassigned) == 1 and res2.unassigned[0].tag == "C"):
        ok = False
        report["checks"]["unassigned_FAIL"] = len(res2.unassigned)
    # bogus tag must not pollute per-tag lines; the off-plan "C" fixture is
    # real installed power (unassigned to any room) so it belongs in the
    # building total.
    if abs(res2.total_w - (want_total + sched["C"].watts)) > 1e-9:
        ok = False
        report["checks"]["unmatched_pollution_FAIL"] = res2.total_w

    report["summary"] = summarize_lighting(res)
    return ok, report


def main() -> int:
    clf = train_lighting_classifier()
    all_ok = True
    for seed in SEEDS:
        ok, report = run_sheet(seed, clf)
        all_ok &= ok
        print(f"sheet {report['sheet']}: {'PASS' if ok else 'FAIL'}")
        for k, v in report["checks"].items():
            print(f"    {k}: {v}")
        print(report["summary"])
    print("LIGHTING E2E:", "ALL PASS" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
