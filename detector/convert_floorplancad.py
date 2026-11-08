#!/usr/bin/env python3
"""Convert FloorPlanCAD parquet (test split) to YOLO format.

The parquet has columns: image {bytes, path}, image_id, objects {id, bbox,
category, mask}. Images are 1000x1000 PNGs; bboxes are COCO-style
[x, y, w, h] in 0-1000 coordinates (top-left corner plus size), so the
YOLO box is cx=(x+w/2)/1000, cy=(y+h/2)/1000, w=w/1000, h=h/1000.

NOTE: the bboxes are *not* corners. Sorting the four numbers (as an
[x0,y0,x1,y1] reading would) silently produces wrong centres and wrong
sizes whenever w < x or h < y.

This is the *test* split only (5,308 samples) -- it serves as a second
zero-shot / cross-dataset eval set, not training data.

Output (default ~/workspace/datasets/detector_yolo/floorplancad/):
    images/eval/<image_id>.png
    labels/eval/<image_id>.txt
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # package import: python -m detector.convert_floorplancad, pytest
    from detector.classes import CLASS_IDS, FLOORPLANCAD_MAP
except ImportError:  # direct script execution from inside detector/
    from classes import CLASS_IDS, FLOORPLANCAD_MAP


def coco_bbox_to_yolo(bb):
    """Convert a FloorPlanCAD COCO bbox to a normalized YOLO box.

    FloorPlanCAD bboxes are COCO-style ``[x, y, w, h]`` (top-left corner
    plus width/height) in 0-1000 coordinates. Returns ``(cx, cy, w, h)``
    with all four values normalized to 0-1, or ``None`` when the box has a
    non-positive size (caller skips it).
    """
    x, y, w, h = bb
    if w <= 0 or h <= 0:
        return None
    return (x + w / 2) / 1000, (y + h / 2) / 1000, w / 1000, h / 1000


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
            yolo = coco_bbox_to_yolo(bb)
            if yolo is None:
                continue
            cx, cy, w, h = yolo
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
