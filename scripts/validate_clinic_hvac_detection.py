"""Detection half of the real-model HVAC check (#707).

Draws one mechanical plan sheet per storey from the BSI Medical-Dental Clinic
HVAC IFC (CC BY 4.0: BSI (2020) "Medical-Dental Test Files," buildingSMART
International), then runs ``hvac_trace.detect_components`` on it and scores
the result against the designer's own model:

* detection: per-class precision/recall for supply diffusers and return/exhaust
  grilles, matched within 0.5 m (25 px, the same tolerance as the synthetic
  validation);
* end to end: each terminal the designer placed in a space is followed through
  detection and then ``_assign_diffuser_room`` at the *detected* position.

What is real: room shapes and names, terminal positions and types, and the duct
runs (plan footprints of every IfcFlowSegment, pipes excluded). What is not:
the symbols. Terminals are drawn with matchline's own glyphs
(``synth.mech``), so this measures detection in a real layout's density and
clutter, not on another firm's drafting symbology.

Input is the JSON cache written by ``scripts/clinic_sheet_data.py``. Neither
the IFC nor the cache is committed.

Usage:
    python scripts/clinic_sheet_data.py Clinic_HVAC.ifc clinic.json
    python scripts/validate_clinic_hvac_detection.py clinic.json [--png-dir out/]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATCH_TOL_M = 0.5  # 25 px at 50 px/m, as in hvac_trace._match_detections
SCORED_CLASSES = ("diffuser", "grille")


class SheetFrame:
    """World metres (plan y up) <-> sheet pixels (image y down)."""

    def __init__(self, xmin, xmax, ymin, ymax, px_per_m, margin_m):
        self.xmin, self.ymax = xmin, ymax
        self.p, self.m = px_per_m, margin_m
        self.size = (
            int(math.ceil((xmax - xmin + 2 * margin_m) * px_per_m)),
            int(math.ceil((ymax - ymin + 2 * margin_m) * px_per_m)),
        )

    def to_px(self, x, y):
        return ((x - self.xmin + self.m) * self.p, (self.ymax - y + self.m) * self.p)

    def to_m(self, px, py):
        return (px / self.p - self.m + self.xmin, self.ymax - (py / self.p - self.m))


def frame_for(storey):
    from synth.mech import MARGIN_M, PX_PER_M

    xs = [p[0] for r in storey["rooms"] for p in r["polygon_m"]] + [
        t["x"] for t in storey["terminals"]
    ]
    ys = [p[1] for r in storey["rooms"] for p in r["polygon_m"]] + [
        t["y"] for t in storey["terminals"]
    ]
    return SheetFrame(min(xs), max(xs), min(ys), max(ys), PX_PER_M, MARGIN_M)


def render_storey(storey, frame=None):
    """Mechanical plan sheet for one storey, in the synthetic sheets' style.

    Walls are 3 px room outlines with a room label (as ``render_mech_sheet``),
    ducts are filled plan footprints, and terminals are drawn last over a white
    box, the way a symbol occludes the duct it sits on in a real plan.
    """
    from PIL import Image, ImageDraw

    from synth.mech import _GLYPH_EXT, _GLYPH_FN, _font

    frame = frame or frame_for(storey)
    img = Image.new("L", frame.size, 255)
    d = ImageDraw.Draw(img)
    f = _font(18)
    for r in storey["rooms"]:
        pts = [frame.to_px(x, y) for x, y in r["polygon_m"]]
        d.polygon(pts, outline=0, width=3)
        label = " ".join(s for s in (r.get("name"), r.get("long_name")) if s)
        d.text((min(p[0] for p in pts) + 8, min(p[1] for p in pts) + 6), label, fill=0, font=f)
    for hull in storey["ducts"]:
        d.polygon([frame.to_px(x, y) for x, y in hull], fill=0)
    for t in storey["terminals"]:
        cx, cy = frame.to_px(t["x"], t["y"])
        w, h = (e * frame.p for e in _GLYPH_EXT[t["cls"]])
        d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=255)
        _GLYPH_FN[t["cls"]](d, cx, cy)
    return img, frame


def match(dets, terminals, cls, tol_m=MATCH_TOL_M):
    """Greedy nearest matching within tol_m. Returns {terminal index: det}."""
    pairs = []
    for di, dt in enumerate(dets):
        if dt["label"] != cls:
            continue
        for ti, t in enumerate(terminals):
            if t["cls"] == cls:
                dd = math.hypot(dt["x"] - t["x"], dt["y"] - t["y"])
                if dd <= tol_m:
                    pairs.append((dd, di, ti))
    pairs.sort()
    used_d, out = set(), {}
    for _, di, ti in pairs:
        if di not in used_d and ti not in out:
            used_d.add(di)
            out[ti] = dets[di]
    return out


def score_storey(storey, dets):
    """Detection P/R per scored class plus end-to-end room outcomes."""
    from hvac_trace import _assign_diffuser_room

    terms = storey["terminals"]
    det = {}
    matched = {}
    for cls in SCORED_CLASSES:
        m = match(dets, terms, cls)
        matched.update(m)
        n_pred = sum(1 for dt in dets if dt["label"] == cls)
        n_gt = sum(1 for t in terms if t["cls"] == cls)
        tp = len(m)
        det[cls] = {"tp": tp, "fp": n_pred - tp, "fn": n_gt - tp}
    other = Counter(dt["label"] for dt in dets if dt["label"] not in SCORED_CLASSES)
    # wrong-class: a missed terminal with a detection of the other class on it
    confused = 0
    for ti, t in enumerate(terms):
        if ti in matched:
            continue
        if any(
            dt["label"] in SCORED_CLASSES
            and dt["label"] != t["cls"]
            and math.hypot(dt["x"] - t["x"], dt["y"] - t["y"]) <= MATCH_TOL_M
            for dt in dets
        ):
            confused += 1
    e2e = Counter()
    for ti, t in enumerate(terms):
        if t.get("gt_space") is None:
            continue
        if ti not in matched:
            e2e["missed"] += 1
            continue
        dt = matched[ti]
        rid, _, _ = _assign_diffuser_room(dt["x"], dt["y"], storey["rooms"])
        e2e["review" if rid is None else ("correct" if rid == t["gt_space"] else "wrong")] += 1
    return {
        "det": det,
        "other_labels": dict(other),
        "wrong_class": confused,
        "end_to_end": dict(e2e),
    }


def stage_check(gray, frame, storey, clf, templates):
    """Where terminals are lost: NCC proposal stage vs the WiSARD decision.

    For each scored class: how many terminals have an NCC proposal of their
    own class within the match tolerance, and what WiSARD says about the crop
    cut at the terminal's true position.
    """
    import numpy as np

    from hvac_trace import MECH_CLASSES, NCC_PROPOSE, _logodds_scores, ncc_locate
    from synth.mech import detection_crop

    names = list(MECH_CLASSES) + ["background"]
    out = {}
    tol_px = MATCH_TOL_M * frame.p
    for cls in SCORED_CLASSES:
        terms = [t for t in storey["terminals"] if t["cls"] == cls]
        th, tw = templates[cls].shape
        props = [
            (x + tw / 2, y + th / 2)
            for y, x, _ in ncc_locate(gray, templates[cls], thresh=NCC_PROPOSE[cls])
        ]
        hit = 0
        crops = []
        for t in terms:
            cx, cy = frame.to_px(t["x"], t["y"])
            hit += any(math.hypot(px - cx, py - cy) <= tol_px for px, py in props)
            crops.append(detection_crop(gray, cx, cy, cls))
        verdict = Counter()
        if crops:
            for i in _logodds_scores(clf, np.stack(crops)).argmax(axis=1):
                verdict[names[int(i)]] += 1
        out[cls] = {
            "n": len(terms),
            "ncc_proposals": len(props),
            "proposal_recall": hit,
            "wisard_at_truth": dict(verdict),
        }
    return out


def build_detector():
    from hvac_trace import MECH_CLASSES, WisardClassifier
    from synth.mech import (
        CONTEXT_BG,
        CONTEXT_PER_CLASS,
        render_template,
        training_crops_from_sheets,
    )

    # same recipe as hvac_trace.main()
    X, y, names = training_crops_from_sheets(
        n_per_class=200,
        seed=0,
        bg_per_sheet=8,
        context_per_class=CONTEXT_PER_CLASS,
        context_bg=CONTEXT_BG,
    )
    clf = WisardClassifier(len(names), seed=42)
    clf.fit(X, y)
    return clf, {c: render_template(c) for c in MECH_CLASSES}


def run(data, png_dir=None):
    import numpy as np

    from hvac_trace import detect_components

    clf, templates = build_detector()
    results = {}
    for name, storey in sorted(data.items()):
        if not storey["terminals"]:
            continue
        img, frame = render_storey(storey)
        if png_dir:
            Path(png_dir).mkdir(parents=True, exist_ok=True)
            img.save(Path(png_dir) / f"clinic_{name.replace(' ', '_')}.png")
        gray = np.asarray(img).astype(np.float64)
        raw = detect_components(gray, templates, clf)
        dets = []
        for dt in raw:
            x, y = frame.to_m(dt["cx"], dt["cy"])
            dets.append(dict(dt, x=x, y=y))
        results[name] = score_storey(storey, dets)
        results[name]["stages"] = stage_check(gray, frame, storey, clf, templates)
        results[name]["sheet_px"] = list(frame.size)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cache", help="JSON from scripts/clinic_sheet_data.py")
    ap.add_argument("--png-dir", help="also save the rendered sheets here")
    ap.add_argument("--out", help="write the results JSON here")
    a = ap.parse_args(argv)
    res = run(json.loads(Path(a.cache).read_text()), a.png_dir)
    for name, r in res.items():
        parts = []
        for c, v in r["det"].items():
            prec = v["tp"] / max(1, v["tp"] + v["fp"])
            rec = v["tp"] / max(1, v["tp"] + v["fn"])
            parts.append(f"{c} P={prec:.3f} R={rec:.3f} ({v['tp']}/{v['fp']}/{v['fn']})")
        dp = ", ".join(parts)
        print(f"{name}: {dp}; other labels {r['other_labels']}; wrong class {r['wrong_class']}")
        print(f"  end to end: {r['end_to_end']}")
        for c, v in r["stages"].items():
            print(
                f"  {c}: NCC proposal at {v['proposal_recall']}/{v['n']} terminals "
                f"({v['ncc_proposals']} proposals); WiSARD at true position {v['wisard_at_truth']}"
            )
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
