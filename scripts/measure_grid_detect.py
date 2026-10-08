"""Measure grid detection (#742) on randomized synthetic vector sheet pairs.

Each seed writes a plan and a south elevation as real PDFs (tests/pdf_fixtures),
reads them back through ``pdf_ingest`` and scores ``grid_detect``:

* grid lines: label + position recall and precision against what was drawn;
* registration: plan<->elevation through ``register_elevation_grid``, max
  error of the drawn grid positions in metres, and how often it registers.

Randomized: grid counts and spacing, label schemes (letters, numbers,
intermediate ``A.1``), bubble size, line style (dashed path, dash-dot pieces,
solid), bubbles at one or both ends, clutter (walls, door swings, room tags
with numbers, dimension strings, a dashed hidden line without bubbles), and an
elevation that shows only some of the bubbles. No real benchmark sheets with
grids are available yet (the Clinic IFC has no IfcGrid), so this is the
synthetic half of the acceptance measurement.

    python3 scripts/measure_grid_detect.py --seeds 100 [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

import grid_detect as G  # noqa: E402
from pdf_ingest import ingest_pdf  # noqa: E402
from registration import Facade, register_elevation_grid  # noqa: E402

K = 0.5523
M_PER_PT = 0.05


def circle(cx, cy, r, w=1.0):
    k = K * r
    return (
        f"{w} w {cx + r} {cy} m {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
        f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
        f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
        f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c h S\n"
    )


def gline(style, x0, y0, x1, y1):
    if style == "dashed":
        return f"[12 3 2 3] 0 d 0.5 w {x0} {y0} m {x1} {y1} l S [] 0 d\n"
    if style == "solid":
        return line(x0, y0, x1, y1, 0.5)
    out, t, n = "", 0.0, max(abs(x1 - x0), abs(y1 - y0))
    dx, dy = (x1 - x0) / n, (y1 - y0) / n
    while t < n:
        a, b = t, min(t + 10, n)
        out += line(x0 + dx * a, y0 + dy * a, x0 + dx * b, y0 + dy * b, 0.5)
        if t + 13 < n:
            out += line(
                x0 + dx * (t + 12), y0 + dy * (t + 12), x0 + dx * (t + 13), y0 + dy * (t + 13), 0.5
            )
        t += 15
    return out


def bubble(cx, cy, lab, r, size):
    return circle(cx, cy, r) + text(cx - 0.3 * size * len(lab), cy - 0.36 * size, lab, size)


def labels(rng, n, letters):
    if letters:
        base = [chr(65 + i) for i in range(26) if chr(65 + i) not in "IO"][:n]
    else:
        base = [str(i + 1) for i in range(n)]
    if rng.random() < 0.3 and n >= 3:  # an intermediate grid
        i = rng.randrange(1, n)
        base.insert(i, f"{base[i - 1]}.1")
        base = base[:n]
    return base


def make_pair(seed):
    rng = random.Random(seed)
    nv, nh = rng.randint(3, 8), rng.randint(2, 5)
    r = rng.uniform(7, 14)
    size = r * rng.uniform(0.9, 1.2)
    style = rng.choice(["dashed", "pieces", "solid"])
    letters_v = rng.random() < 0.6
    lv, lh = labels(rng, nv, letters_v), labels(rng, nh, not letters_v)
    xs, x = [], 110.0
    for _ in lv:
        xs.append(round(x, 2))
        x += rng.uniform(60, 120)
    ys, y = [], 100.0
    for _ in lh:
        ys.append(round(y, 2))
        y += rng.uniform(60, 110)
    W, H = xs[-1] + 140, ys[-1] + 160
    c = ""
    both = rng.random() < 0.5
    for lab, gx in zip(lv, xs):
        c += gline(style, gx, ys[0] - 30, gx, ys[-1] + 30) + bubble(
            gx, ys[-1] + 30 + 1.6 * r, lab, r, size
        )
        if both:
            c += bubble(gx, ys[0] - 30 - 1.6 * r, lab, r, size)
    for lab, gy in zip(lh, ys):
        c += gline(style, xs[0] - 30, gy, xs[-1] + 30, gy) + bubble(
            xs[0] - 30 - 1.6 * r, gy, lab, r, size
        )
    # clutter
    bx0, by0, bx1, by1 = xs[0] - 10, ys[0] - 10, xs[-1] + 10, ys[-1] + 10
    c += f"2 w {bx0} {by0} m {bx1} {by0} l {bx1} {by1} l {bx0} {by1} l h S\n"
    for _ in range(rng.randint(1, 4)):
        dx = rng.uniform(xs[0] + 20, xs[-1] - 40)
        y0 = ys[0] - 10
        c += f"1 w {dx} {y0} m {dx} {y0 + 30} l S {dx} {y0 + 30} m "
        c += f"{dx + 16.6} {y0 + 30} {dx + 30} {y0 + 16.6} {dx + 30} {y0} c S\n"
    for k in range(rng.randint(1, 4)):
        tx, ty = rng.uniform(xs[0] + 20, xs[-1] - 20), rng.uniform(ys[0] + 20, ys[-1] - 20)
        c += circle(tx, ty, rng.uniform(8, 13)) + text(tx - 8, ty - 3, str(101 + k), 8)
    c += line(xs[0], ys[0] - 55, xs[-1], ys[0] - 55, 0.3) + text(xs[0] + 20, ys[0] - 51, "12500", 7)
    c += f"[4 4] 0 d 0.5 w {xs[0]} {ys[0] + 15} m {xs[-1]} {ys[0] + 15} l S [] 0 d\n"
    plan = PageSpec(W, H, c)

    shown = [i for i in range(nv) if rng.random() < 0.7]
    if len(shown) < 2:
        shown = sorted(rng.sample(range(nv), 2))
    sc, u0 = rng.uniform(0.5, 1.2), 80.0
    e = f"2 w 50 100 m {u0 + (xs[-1] - xs[0]) * sc + 40} 100 l S\n"
    u1 = u0 + (xs[-1] - xs[0]) * sc
    e += f"1.5 w {u0} 100 m {u1} 100 l {u1} 260 l {u0} 260 l h S\n"
    for i in shown:
        u = u0 + (xs[i] - xs[0]) * sc
        stub = rng.uniform(3, 6) * r
        e += gline(style, u, 260, u, 260 + stub) + bubble(u, 260 + stub + r + 4, lv[i], r, size)
    elev = PageSpec(u0 + (xs[-1] - xs[0]) * sc + 120, 420, e)
    truth = {
        "v": dict(zip(lv, xs)),
        "h": dict(zip(lh, ys)),
        "shown": [lv[i] for i in shown],
        "x0": xs[0],
        "style": style,
    }
    return plan, elev, truth


def _ingest(tmp, name, page):
    out = Path(tmp) / name
    ingest_pdf(write_pdf(Path(tmp) / f"{name}.pdf", [page]), out_dir=out)
    return json.loads((out / "sheet_001.json").read_text())


def score(seed):
    plan_p, elev_p, truth = make_pair(seed)
    with tempfile.TemporaryDirectory() as tmp:
        plan_s, elev_s = _ingest(tmp, "plan", plan_p), _ingest(tmp, "elev", elev_p)
    # pdf_ingest flips y to sheet-down; compare horizontals by label only + spacing
    plan, elev = G.detect_grids(plan_s, "plan"), G.detect_grids(elev_s, "elev")
    tp = fp = fn = 0
    for orient in ("v", "h"):
        got = plan.by_label(orient)
        want = truth[orient]
        for lab, pos in want.items():
            g = got.get(lab)
            if g is None:
                fn += 1
            elif orient == "v" and abs(g.coord_pt - pos) > 1.0:
                fn += 1
                fp += 1
            else:
                tp += 1
        fp += sum(1 for lab in got if lab not in want)
    ev = elev.by_label("v")
    etp = sum(1 for lab in truth["shown"] if lab in ev)
    efp = sum(1 for lab in ev if lab not in truth["shown"])
    reg_ok, err = False, None
    try:
        pg = G.plan_grid_m(plan, "v", M_PER_PT, origin_pt=truth["x0"])
        fac = Facade(name="south", ref_corner_m=(0.0, 0.0), length_m=30.0, fixed_coord_m=0.0)
        reg = register_elevation_grid("elev", fac, pg, G.elevation_bubbles(elev), 100.0, 40.0, 1)
        reg_ok = True
        sc_true = {lab: (x - truth["x0"]) * M_PER_PT for lab, x in truth["v"].items()}
        err = max(
            abs(reg.to_facade(g.coord_pt, 0)[0] - sc_true[lab])
            for lab, g in ev.items()
            if lab in sc_true
        )
    except ValueError:
        pass
    return {
        "seed": seed,
        "style": truth["style"],
        "plan": (tp, fp, fn),
        "elev": (etp, efp, len(truth["shown"]) - etp),
        "registered": reg_ok,
        "max_err_m": err,
        "review": len(plan.review) + len(elev.review),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [score(s) for s in range(a.seeds)]
    tot = {}
    for key in ("plan", "elev"):
        tp, fp, fn = (sum(r[key][i] for r in rows) for i in range(3))
        tot[key] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(tp / max(tp + fp, 1), 4),
            "recall": round(tp / max(tp + fn, 1), 4),
        }
    errs = [r["max_err_m"] for r in rows if r["max_err_m"] is not None]
    tot["registered"] = f"{sum(r['registered'] for r in rows)}/{len(rows)}"
    tot["max_err_m"] = round(max(errs), 4) if errs else None
    by_style = {}
    for r in rows:
        s = by_style.setdefault(r["style"], [0, 0, 0])
        s[0] += r["plan"][0]
        s[1] += r["plan"][1]
        s[2] += r["plan"][2]
    tot["plan_by_style_tp_fp_fn"] = by_style
    print(json.dumps(tot, indent=1))
    bad = [
        r
        for r in rows
        if r["plan"][1] or r["plan"][2] or r["elev"][1] or r["elev"][2] or not r["registered"]
    ]
    for r in bad[:15]:
        print("miss", r)
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": tot, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
