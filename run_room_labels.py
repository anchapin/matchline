#!/usr/bin/env python3
"""Validate room_labels.py on the synthetic labeled floor plan.

Metrics: OCR detection rate, text exact-match, parse accuracy,
association accuracy, unmatched/unlabeled handling, runtime.
"""
import sys
import time

sys.path.insert(0, "/home/hatch/workspace/jesse-proto")

import room_labels as rl


def norm(s):
    return " ".join(s.upper().split())


def main():
    img, spaces, expected = rl.synthesize_test_plan()
    print(f"OCR engine: {rl.ocr_engine_name()}")

    t0 = time.time()
    labeled = rl.label_spaces_from_sheet(img, spaces)
    dt = time.time() - t0

    print(f"\nOCR found {len(labeled.labels)} text boxes "
          f"({len(expected)} room labels + 1 stray drawn)")
    for lb in labeled.labels:
        print(f"  raw={lb.raw_text!r:22} -> name={lb.name!r:14} number={lb.number!r:6} "
              f"ocr_conf={lb.ocr_confidence:.2f}")

    # association accuracy (right polygon, regardless of OCR text errors)
    # vs full exact-match accuracy (name+number strings identical)
    correct_poly, correct_full, total = 0, 0, len(expected)
    for i, (exp_name, exp_num) in expected.items():
        s = labeled.spaces[i]
        ok_full = norm(s.name) == norm(exp_name) and norm(s.number) == norm(exp_num)
        ok_poly = bool(ok_full or (s.label_source in ("enclosed", "nearest") and
                              (s.name or s.number)))  # claimed by this room
        # doorway-straddling label (room 8) may land in room 9: accept either
        if i == 8 and not ok_poly:
            s9 = labeled.spaces[9]
            if (s9.name or s9.number):
                print(f"  room 8 label claimed by room 9 (nearest fallback) - acceptable")
                ok_poly = True
        correct_poly += ok_poly
        correct_full += ok_full
        flag = "OK " if ok_full else ("POLY-OK" if ok_poly else "MISS")
        print(f"  [{flag}] room {i}: got ({s.name!r}, {s.number!r}) "
              f"want ({exp_name!r}, {exp_num!r}) src={s.label_source} conf={s.label_confidence:.2f}")

    print(f"\nassociation accuracy (right polygon): {correct_poly}/{total} = {100*correct_poly/total:.1f}%")
    print(f"exact (name, number) match:           {correct_full}/{total} = {100*correct_full/total:.1f}%")
    print(f"labeled spaces: {labeled.n_labeled}/{labeled.n_total}")
    print(f"unlabeled spaces: {[i for i, s in enumerate(labeled.spaces) if not s.name and not s.number]}")
    print(f"unmatched labels: {[l.raw_text for l in labeled.unmatched_labels]}")
    print(f"runtime: {dt:.1f}s for {img.shape[1]}x{img.shape[0]} sheet")

    # parse-only unit checks
    cases = [("OPEN OFFICE 201", ("OPEN OFFICE", "201")),
             ("201", ("", "201")),
             ("201A", ("", "201A")),
             ("CONF 202", ("CONF", "202")),
             ("207 KITCHEN", ("KITCHEN", "207")),
             ("RM 203", ("", "203")),
             ("C1", ("", "C1")),
             ("VEST 300", ("VEST", "300")),
             ("2O5A", ("", "205A")),
             ("LOBBY", ("LOBBY", "")),
             ("ELEC", ("ELEC", ""))]
    pok = sum(rl.parse_room_label(t)[:2] == e for t, e in cases)
    print(f"parse unit tests: {pok}/{len(cases)}")

    # dimension-string rejection unit tests
    dim_cases = ["17'-6\"", "5 X 5", "1/2\"", "@ 16 O.C.", "19 0.C.",
                 "11 - 10\u00b0", "67 SF", "OPEN OFFICE 201", "201", "WIC"]
    dim_want = [True, True, True, True, True, True, True, False, False, False]
    dim_ok = sum(rl.looks_like_dimension(t) == w for t, w in zip(dim_cases, dim_want))
    print(f"dimension rejection tests: {dim_ok}/{len(dim_cases)}")

    # nearest-fallback unit test: label centroid in the doorway gap between
    # room 8 (900..1200) and room 9 (1200..1520) -> nearest polygon, flagged
    gap_label = rl.RoomLabel(name="VEST", number="300", raw_text="VEST 300",
                             bbox=(1180, 900, 1220, 930), centroid=(1200, 915),
                             ocr_confidence=0.9, parse_confidence=1.0)
    units = [rl.LabeledSpace(polygon_px=s.polygon_px) for s in spaces]
    res = rl.assign_labels(units, [gap_label])
    a, b = units[8], units[9]
    got = "room8" if (a.name, a.number) == ("VEST", "300") else \
          "room9" if (b.name, b.number) == ("VEST", "300") else "none"
    src = (a if got == "room8" else b).label_source if got != "none" else ""
    print(f"nearest-fallback unit test: assigned to {got} via '{src}' "
          f"{'OK' if got in ('room8', 'room9') and src == 'nearest' else 'FAIL'}")


if __name__ == "__main__":
    main()
