#!/usr/bin/env python3
"""Integrity check for a YOLO dataset tree: image/label pairing, label format,
class-id range, and basic box stats. Exits nonzero on any error."""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes import CLASS_IDS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--splits', nargs='+', default=['train', 'val', 'test'])
    ap.add_argument('--expect-classes', type=int, nargs='+', default=[0, 1])
    args = ap.parse_args()

    n_classes = max(CLASS_IDS.values()) + 1
    errors = []
    stats = Counter()
    for split in args.splits:
        img_dir = os.path.join(args.root, 'images', split)
        lbl_dir = os.path.join(args.root, 'labels', split)
        if not os.path.isdir(img_dir):
            print(f'skip {split}: no images dir')
            continue
        imgs = sorted(f for f in os.listdir(img_dir)
                      if f.endswith(('.png', '.jpg', '.jpeg')))
        for im in imgs:
            base = os.path.splitext(im)[0]
            lp = os.path.join(lbl_dir, base + '.txt')
            if not os.path.exists(lp):
                errors.append(f'{split}/{im}: missing label file')
                continue
            with open(lp) as f:
                lines = [l.strip() for l in f if l.strip()]
            stats[f'{split}_images'] += 1
            for ln in lines:
                p = ln.split()
                if len(p) != 5:
                    errors.append(f'{split}/{base}: bad line {ln!r}')
                    continue
                c, cx, cy, w, h = int(p[0]), *map(float, p[1:])
                if c not in args.expect_classes:
                    errors.append(f'{split}/{base}: class {c} out of range')
                if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < w <= 1 and 0 < h <= 1):
                    errors.append(f'{split}/{base}: bad coords {ln!r}')
                stats[f'{split}_box_c{c}'] += 1
    print('stats:', dict(stats))
    if errors:
        print(f'{len(errors)} ERRORS (first 10):')
        for e in errors[:10]:
            print('  ', e)
        sys.exit(1)
    print('OK: all checks passed')


if __name__ == '__main__':
    main()
