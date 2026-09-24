#!/usr/bin/env python3
"""Zero-shot / cross-dataset eval: greedy IoU matching of predictions to GT.

Reads a predictions JSON (from sahi_infer.py) and a YOLO label file, matches
greedily at IoU 0.5 per class, and reports per-class precision/recall/counts.
Honest about the synthetic->real gap: no score inflation, unmatched GT and
unmatched predictions are both reported.

Usage:
    python3 eval_zero_shot.py --preds /tmp/sheet_01_preds.json \
        --labels ~/workspace/datasets/detector_yolo/aec/labels/eval
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes import CLASS_NAMES


def iou(a, b):
    xx0, yy0 = max(a['x0'], b['x0']), max(a['y0'], b['y0'])
    xx1, yy1 = min(a['x1'], b['x1']), min(a['y1'], b['y1'])
    inter = max(0, xx1 - xx0) * max(0, yy1 - yy0)
    a1 = (a['x1'] - a['x0']) * (a['y1'] - a['y0'])
    a2 = (b['x1'] - b['x0']) * (b['y1'] - b['y0'])
    return inter / max(1e-9, a1 + a2 - inter)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preds', required=True)
    ap.add_argument('--labels', required=True, help='YOLO labels dir')
    ap.add_argument('--iou', type=float, default=0.5)
    args = ap.parse_args()

    with open(args.preds) as f:
        d = json.load(f)
    W, H = d['width'], d['height']
    base = os.path.splitext(os.path.basename(d['image']))[0]
    gt = []
    with open(os.path.join(args.labels, base + '.txt')) as f:
        for line in f:
            c, cx, cy, w, h = map(float, line.split())
            gt.append({'cls': int(c),
                       'x0': (cx - w / 2) * W, 'y0': (cy - h / 2) * H,
                       'x1': (cx + w / 2) * W, 'y1': (cy + h / 2) * H})

    stats = defaultdict(Counter)
    for cls in set([g['cls'] for g in gt] + [p['cls'] for p in d['preds']]):
        g = [x for x in gt if x['cls'] == cls]
        p = sorted([x for x in d['preds'] if x['cls'] == cls],
                   key=lambda x: -x['conf'])
        matched_g = set()
        tp = 0
        for pr in p:
            best, best_iou = -1, 0
            for i, gg in enumerate(g):
                if i in matched_g:
                    continue
                v = iou(pr, gg)
                if v > best_iou:
                    best, best_iou = i, v
            if best_iou >= args.iou:
                matched_g.add(best)
                tp += 1
        stats[cls] = Counter(tp=tp, fp=len(p) - tp, fn=len(g) - tp,
                             n_gt=len(g), n_pred=len(p))

    print(f"== {base} (IoU>={args.iou}) ==")
    for cls, s in sorted(stats.items()):
        prec = s['tp'] / max(1, s['tp'] + s['fp'])
        rec = s['tp'] / max(1, s['n_gt'])
        print(f"  {CLASS_NAMES[cls]:6s} P={prec:.3f} R={rec:.3f} "
              f"tp={s['tp']} fp={s['fp']} fn={s['fn']} "
              f"(gt={s['n_gt']}, pred={s['n_pred']})")


if __name__ == '__main__':
    main()
