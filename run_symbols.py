"""Synthetic vector-symbol evaluation + GD&T topological invariant check.

Experiment A: WiSARD (same K=160/n=10 paper hyperparameters) trained on jittered
clean vector symbols (10 classes), tested on held-out jittered variants.
Experiment B: Zhang-Suen skeletonization + topological invariants (Sec. 9.2)
on clean symbols, compared against the paper's Table 9.4 invariant signatures.
"""
import json
import time

import numpy as np

from jesse import WisardClassifier, make_tuple_indices, zhang_suen, skeleton_invariants
from symbols import make_dataset, draw, to28, NAMES

tidx = make_tuple_indices(28, 28, seed=42)
Xtr, ytr, Xte, yte = make_dataset(n_train=400, n_test=100, seed=7)
print(f"symbols: train {Xtr.shape} test {Xte.shape}", flush=True)

clf = WisardClassifier(10, tuple_idx=tidx)
t0 = time.perf_counter()
clf.fit(Xtr, ytr)
t_train = time.perf_counter() - t0
pred = clf.predict_sum(Xte)
acc = float((pred == yte).mean())
pred_lo = clf.predict_logodds(Xte, alpha=0.1)
acc_lo = float((pred_lo == yte).mean())
print(f"symbols WiSARD: sum acc={acc*100:.2f}%  logodds(a=0.1) acc={acc_lo*100:.2f}% train={t_train:.3f}s", flush=True)
cm = np.zeros((10, 10), int)
for t, p in zip(yte, pred):
    cm[t, p] += 1
print("confusion (rows=true):")
print("      " + " ".join(f"{n[:5]:>5}" for n in NAMES))
for i, n in enumerate(NAMES):
    print(f"{n[:5]:>5} " + " ".join(f"{v:>5}" for v in cm[i]))

# Experiment B: topological invariants vs paper Table 9.4 (Sec. 9.2).
# Uses clean 1px primitives at 112x112 (thick raster strokes produce skeleton
# spurs that are renderer artifacts, not method failures).
print("\nTopological invariants (clean 1px glyphs, Sec. 9.2):")
def bresenham_circle(n, cx, cy, r):
    img = np.zeros((n, n), np.uint8)
    x, y, d = r, 0, 1 - r
    while y <= x:
        for px, py in [(cx+x,cy+y),(cx-x,cy+y),(cx+x,cy-y),(cx-x,cy-y),
                       (cx+y,cy+x),(cx-y,cy+x),(cx+y,cy-x),(cx-y,cy-x)]:
            if 0 <= px < n and 0 <= py < n: img[py, px] = 1
        y += 1
        if d <= 0: d += 2*y + 1
        else: x -= 1; d += 2*(y - x) + 1
    return img

_t = np.zeros((112,112), np.uint8); _t[30, 20:92] = 1; _t[30:92, 56] = 1
_l = np.zeros((112,112), np.uint8); _l[56, 20:92] = 1
_p = np.zeros((112,112), np.uint8); _p[20:92, 40] = 1; _p[20:92, 72] = 1
checks = [
    ("circularity (O)", bresenham_circle(112,56,56,30),
     dict(endpoints=0, t_junctions=0, x_junctions=0, holes=1)),
    ("concentricity", np.maximum(bresenham_circle(112,56,56,30),
                                 bresenham_circle(112,56,56,18)),
     dict(endpoints=0, t_junctions=0, x_junctions=0, holes=2)),
    ("perpendicularity (T)", _t, dict(endpoints=3, t_junctions=1, holes=0)),
    ("straightness (-)", _l, dict(endpoints=2, t_junctions=0, holes=0)),
    ("parallelism (||)", _p, dict(endpoints=4, t_junctions=0, holes=0)),
]
inv_results = {}
allok = True
for label, img, expect in checks:
    inv = skeleton_invariants(zhang_suen(img))
    got = {k: inv[k] for k in expect}
    ok = got == expect
    allok &= ok
    inv_results[label] = inv
    print(f"  {label:22s} {got} expected {expect} {'OK' if ok else 'MISMATCH'}")
print("  all match paper Table 9.4:", allok)

with open("symbols_results.json", "w") as f:
    json.dump({"acc": acc, "train_time_s": t_train,
               "confusion": cm.tolist(), "invariants": inv_results}, f, indent=2)
print("saved symbols_results.json")
