#!/usr/bin/env python3
"""Convert FloorPlanCAD parquet (test split) to YOLO format.

The parquet has columns: image {bytes, path}, image_id, objects {id, bbox,
category, mask}. Images are 1000x1000 PNGs; bboxes are [x0,y0,x1,y1] in 0-1000
coordinates, so normalization is a straight /1000.

This is the *test* split only (5,308 samples) -- it serves as a second
zero-shot / cross-dataset eval set, not training data.

Output (default ~/workspace/datasets/detector_yolo/floorplancad/):
    images/eval/<image_id>.png
    labels/eval/<image_id>.txt
"""
import argparse
import io
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes import CLASS_IDS, FLOORPLANCAD_MAP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.expanduser(
        '~/workspace/datasets/floorplancad/train-00000-of-00001.parquet'))
    ap.add_argument('--out', default=os.path.expanduser(
        '~/workspace/datasets/detector_yolo/floorplancad'))
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    import pyarrow.parquet as pq

    img_dir = os.path.join(args.out, 'images', 'eval')
    lbl_dir = os.path.join(args.out, 'labels', 'eval')
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)

    t = pq.read_table(args.src, columns=['image', 'image_id', 'objects'])
    stats = Counter()
    n = t.num_rows if not args.limit else min(args.limit, t.num_rows)
    for i in range(n):
        iid = t.column('image_id')[i].as_py()
        img_bytes = t.column('image')[i].as_py()['bytes']
        with open(os.path.join(img_dir, iid + '.png'), 'wb') as f:
            f.write(img_bytes)
        lines = []
        for cat, bb in zip(t.column('objects')[i].as_py()['category'],
                           t.column('objects')[i].as_py()['bbox']):
            uni = FLOORPLANCAD_MAP.get(cat)
            if uni is None:
                stats['skipped_' + cat] += 1
                continue
            x0, y0, x1, y1 = bb
            x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
            cx, cy = ((x0 + x1) / 2) / 1000, ((y0 + y1) / 2) / 1000
            w, h = (x1 - x0) / 1000, (y1 - y0) / 1000
            if w <= 0 or h <= 0:
                continue
            lines.append(f"{CLASS_IDS[uni]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            stats['box_' + uni] += 1
        with open(os.path.join(lbl_dir, iid + '.txt'), 'w') as f:
            f.write('\n'.join(lines))
        stats['images'] += 1
    print('converter stats: images =', stats['images'],
          'box_door =', stats['box_door'], 'box_window =', stats['box_window'])
    top_skip = sorted(((k, v) for k, v in stats.items() if k.startswith('skipped')),
                      key=lambda x: -x[1])[:5]
    print('top skipped:', top_skip)


if __name__ == '__main__':
    main()
