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
    python scripts/validate_clinic_hvac_detection.py clinic.json --vary 1  # #745

``--vary SEED`` redraws the terminals the way a different firm or a scan would
(scale, weight, rotation, an alternative glyph per class, duct and text ink over
the symbol, scan noise, JPEG, a small raster shift). The detector and its
thresholds are unchanged, and nothing is tuned on the varied Clinic numbers.
``--vary-only scale,text`` (with ``--vary``) draws just the named variations
with the same seeded values, to find which one costs recall; ``--vary-only ""``
draws none of them through the varied path.
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


# --- symbol variation (#745) -------------------------------------------------
# A clean render draws every terminal with the exact glyph the NCC templates are
# cut from, so a true terminal scores NCC 1.00. ``render_storey(..., vary=seed)``
# draws them the way another firm's or a scanned sheet would. These ranges are
# fixed up front from the issue, never tuned on Clinic results.
VARY_SCALE = (0.85, 1.15)  # glyph scale, +-15%
VARY_LW = (2, 4)  # outline weight, px (clean: 3)
VARY_TILT_DEG = 5.0  # small rotation on top of a random multiple of 90 deg
VARY_SKEW = 0.06  # horizontal shear, fraction of height
VARY_ALT_STYLE = 0.5  # share of terminals drawn in the class's other style
VARY_NO_KNOCKOUT = 0.5  # share drawn without the white box: duct ink runs through
VARY_TEXT_OVER = 0.3  # share with a tag label overlapping the symbol
VARY_JPEG_Q = (55, 80)
VARY_SHIFT_PX = 2  # raster misregistration, px each axis (0.04 m)
VARIED_STYLES = {"diffuser": 2, "grille": 2}  # classes with an alternative glyph
# each variation ``render_storey(..., only=...)`` can switch on by itself, so the
# loss can be put down to one of them (#745 ablation)
VARY_ASPECTS = (
    "scale", "weight", "turn", "tilt", "skew", "style", "knockout", "text", "scan", "shift",
)  # fmt: skip


def vary_aspects(only):
    """The set of variations to draw: all of them for ``None``, else the named
    ones (a comma list or an iterable). An unknown name is an error."""
    if only is None:
        return set(VARY_ASPECTS)
    names = only.split(",") if isinstance(only, str) else list(only)
    on = {n.strip() for n in names if n.strip()}
    bad = sorted(on - set(VARY_ASPECTS))
    if bad:
        raise ValueError(f"unknown variation {', '.join(bad)}; known: {', '.join(VARY_ASPECTS)}")
    return on


_TAG_TEXT = {"diffuser": "SD-1", "grille": "RG-1", "sensor": "T-1"}


def _draw_diffuser_arrows(d, cx, cy, lw=3):
    """Supply diffuser as a square with four-way throw arrows (no X)."""
    from synth.mech import DIF_S, PX_PER_M

    s = DIF_S * PX_PER_M
    h, a = s / 2, s / 7
    d.rectangle([cx - h, cy - h, cx + h, cy + h], outline=0, width=lw)
    for ux, uy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tx, ty = cx + ux * (h - 5), cy + uy * (h - 5)
        d.line([cx + ux * a * 0.6, cy + uy * a * 0.6, tx, ty], fill=0, width=2)
        px, py = -uy, ux  # perpendicular
        d.polygon(
            [
                (tx, ty),
                (tx - ux * a + px * a / 2, ty - uy * a + py * a / 2),
                (tx - ux * a - px * a / 2, ty - uy * a - py * a / 2),
            ],
            fill=0,
        )


def _draw_grille_hatched(d, cx, cy, lw=3):
    """Return grille as a square with dense parallel blade lines (no diagonal)."""
    from synth.mech import GRI_S, PX_PER_M

    s = GRI_S * PX_PER_M
    h = s / 2
    d.rectangle([cx - h, cy - h, cx + h, cy + h], outline=0, width=lw)
    n = 5
    for i in range(1, n + 1):
        y = cy - h + i * s / (n + 1)
        d.line([cx - h + 2, y, cx + h - 2, y], fill=0, width=1)


def varied_glyph(cls, style=0, scale=1.0, lw=None, angle_deg=0.0, skew=0.0):
    """One terminal symbol on its own white patch (L image), centred.

    ``style`` 0 is matchline's own glyph; 1 is the class's alternative style
    (``VARIED_STYLES``). ``lw`` None draws the glyph at its own clean weight.
    The patch is drawn at nominal size, then scaled, sheared and rotated about
    its centre.
    """
    from PIL import Image, ImageDraw

    from synth.mech import _GLYPH_EXT, _GLYPH_FN, PX_PER_M

    alt = {"diffuser": _draw_diffuser_arrows, "grille": _draw_grille_hatched}
    fn = alt[cls] if style == 1 and cls in alt else _GLYPH_FN[cls]
    side = int(math.ceil(max(_GLYPH_EXT[cls]) * PX_PER_M * 1.8)) + 8
    side += side % 2
    patch = Image.new("L", (side, side), 255)
    if lw is None:
        fn(ImageDraw.Draw(patch), side / 2, side / 2)
    else:
        fn(ImageDraw.Draw(patch), side / 2, side / 2, lw=lw)
    if scale != 1.0:
        n = max(2, int(round(side * scale)))
        n += n % 2
        patch = patch.resize((n, n), Image.BILINEAR)
    if skew:
        w, h = patch.size
        patch = patch.transform(
            (w, h), Image.AFFINE, (1, skew, -skew * h / 2, 0, 1, 0), Image.BILINEAR, fillcolor=255
        )
    if angle_deg:
        patch = patch.rotate(angle_deg, resample=Image.BILINEAR, fillcolor=255)
    return patch


def _paste_min(img, patch, cx, cy):
    """Darken ``img`` with ``patch`` centred at (cx, cy), clipped to the sheet."""
    import numpy as np
    from PIL import Image

    W, H = img.size
    w, h = patch.size
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    ax0, ay0, ax1, ay1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if ax0 >= ax1 or ay0 >= ay1:
        return
    box = (ax0, ay0, ax1, ay1)
    a = np.asarray(img.crop(box))
    b = np.asarray(patch.crop((ax0 - x0, ay0 - y0, ax1 - x0, ay1 - y0)))
    img.paste(Image.fromarray(np.minimum(a, b)), box)


def _degrade_sheet(img, rng, on=None):
    """Scan degradation: blur + noise (detector/degrade.py), JPEG, misregistration.

    ``on`` holds which of "scan" (noise and JPEG) and "shift" to apply; the
    random numbers are drawn either way, so a seed gives the same values."""
    import io

    import numpy as np
    from PIL import Image

    det = str(ROOT / "detector")
    if det not in sys.path:
        sys.path.insert(0, det)
    from degrade import degrade

    on = set(VARY_ASPECTS) if on is None else on
    noise_seed = int(rng.integers(2**31 - 1))
    q = int(rng.integers(VARY_JPEG_Q[0], VARY_JPEG_Q[1] + 1))
    if "scan" in on:
        img = degrade(img, "scan_noise", seed=noise_seed).convert("L")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    dx, dy = (int(v) for v in rng.integers(-VARY_SHIFT_PX, VARY_SHIFT_PX + 1, size=2))
    if "shift" not in on:
        dx = dy = 0
    a = np.full((img.size[1], img.size[0]), 255, np.uint8)
    src = np.asarray(img)
    H, W = src.shape
    a[max(0, dy) : H + min(0, dy), max(0, dx) : W + min(0, dx)] = src[
        max(0, -dy) : H + min(0, -dy), max(0, -dx) : W + min(0, -dx)
    ]
    return Image.fromarray(a), (dx, dy)


def render_storey(storey, frame=None, vary=None, only=None):
    """Mechanical plan sheet for one storey, in the synthetic sheets' style.

    Walls are 3 px room outlines with a room label (as ``render_mech_sheet``),
    ducts are filled plan footprints, and terminals are drawn last over a white
    box, the way a symbol occludes the duct it sits on in a real plan.

    ``vary`` (an int seed) turns on symbol variation (#745): per terminal a
    random scale, line weight, skew, 90-degree turn plus small tilt, sometimes
    the class's alternative glyph, sometimes no white knockout and a tag label
    over the symbol; then the sheet gets scan noise, JPEG and a 0-2 px shift.
    The same seed always gives the same pixels. ``None`` is the clean render,
    unchanged. ``only`` (names from ``VARY_ASPECTS``) draws just those
    variations and leaves the rest as the clean render draws them; each kept
    variation takes the same random value it gets with all of them on.
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
    if vary is None:
        for t in storey["terminals"]:
            cx, cy = frame.to_px(t["x"], t["y"])
            w, h = (e * frame.p for e in _GLYPH_EXT[t["cls"]])
            d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=255)
            _GLYPH_FN[t["cls"]](d, cx, cy)
        return img, frame

    import numpy as np

    on = vary_aspects(only)
    rng = np.random.default_rng(vary)
    ft = _font(15)
    for t in storey["terminals"]:
        cls = t["cls"]
        # draw every random number in a fixed order so a seed is stable
        u = rng.random(4)
        scale = float(rng.uniform(*VARY_SCALE))
        lw = int(rng.integers(VARY_LW[0], VARY_LW[1] + 1))
        turn = 90.0 * int(rng.integers(4))
        tilt = float(rng.uniform(-VARY_TILT_DEG, VARY_TILT_DEG))
        skew = float(rng.uniform(-VARY_SKEW, VARY_SKEW))
        tdx, tdy = rng.uniform(-0.5, 0.5, size=2)
        scale = scale if "scale" in on else 1.0
        lw = lw if "weight" in on else None  # the glyph's own clean weight
        angle = (turn if "turn" in on else 0.0) + (tilt if "tilt" in on else 0.0)
        skew = skew if "skew" in on else 0.0
        style = 1 if "style" in on and cls in VARIED_STYLES and u[0] < VARY_ALT_STYLE else 0
        cx, cy = frame.to_px(t["x"], t["y"])
        w, h = (e * frame.p * scale for e in _GLYPH_EXT[cls])
        if u[1] >= VARY_NO_KNOCKOUT or "knockout" not in on:
            d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=255)
        _paste_min(img, varied_glyph(cls, style, scale, lw, angle, skew), cx, cy)
        if u[2] < VARY_TEXT_OVER and "text" in on:
            # tag label straddling the symbol's lower edge
            d.text(
                (cx + tdx * w - 15, cy + h / 2 - 8 + tdy * 6),
                _TAG_TEXT.get(cls, "X-1"),
                fill=0,
                font=ft,
            )
    img, shift = _degrade_sheet(img, rng, on)
    import copy

    frame = copy.copy(frame)  # never record this render's shift on the caller's frame
    frame.shift_px = shift
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


def run(data, png_dir=None, vary=None, only=None):
    import numpy as np

    from hvac_trace import detect_components

    clf, templates = build_detector()
    results = {}
    for i, (name, storey) in enumerate(sorted(data.items())):
        if not storey["terminals"]:
            continue
        seed = None if vary is None else vary * 1000 + i
        img, frame = render_storey(storey, vary=seed, only=only)
        if png_dir:
            Path(png_dir).mkdir(parents=True, exist_ok=True)
            tag = "" if vary is None else f"_vary{vary}"
            if only is not None:
                tag += "_only-" + ("-".join(sorted(vary_aspects(only))) or "none")
            img.save(Path(png_dir) / f"clinic_{name.replace(' ', '_')}{tag}.png")
        gray = np.asarray(img).astype(np.float64)
        raw = detect_components(gray, templates, clf)
        dets = []
        for dt in raw:
            x, y = frame.to_m(dt["cx"], dt["cy"])
            dets.append(dict(dt, x=x, y=y))
        results[name] = score_storey(storey, dets)
        results[name]["stages"] = stage_check(gray, frame, storey, clf, templates)
        results[name]["sheet_px"] = list(frame.size)
        if vary is not None:
            results[name]["vary_seed"] = seed
            results[name]["shift_px"] = list(frame.shift_px)
            results[name]["vary_only"] = sorted(vary_aspects(only))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cache", help="JSON from scripts/clinic_sheet_data.py")
    ap.add_argument("--png-dir", help="also save the rendered sheets here")
    ap.add_argument("--out", help="write the results JSON here")
    ap.add_argument(
        "--vary",
        type=int,
        metavar="SEED",
        help="draw terminals with seeded symbol variation and scan degradation (#745)",
    )
    ap.add_argument(
        "--vary-only",
        metavar="NAMES",
        help="with --vary, draw only these variations (comma list of "
        + ", ".join(VARY_ASPECTS)
        + "; empty for none), to find which one costs recall",
    )
    a = ap.parse_args(argv)
    if a.vary_only is not None:
        if a.vary is None:
            ap.error("--vary-only needs --vary SEED")
        try:
            vary_aspects(a.vary_only)
        except ValueError as e:
            ap.error(str(e))
    res = run(json.loads(Path(a.cache).read_text()), a.png_dir, a.vary, a.vary_only)
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
