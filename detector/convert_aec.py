#!/usr/bin/env python3
"""Convert AEC Geometric Bench annotations to YOLO format (eval / zero-shot set).

Reads dataset/annotations_15_scoring_ready.xml (CVAT format), renders each
sheet_*.pdf to PNG at the exact pixel dimensions the XML annotations use
(300 dpi per manifest.json; page geometry unchanged by redaction), and emits
YOLO labels for the door/window classes in detector/classes.py.

Wall boxes (3484) and Area polygons are counted but skipped: the Monday
baseline taxonomy is door + window. Polygons (Area regions, a few Wall/Window
polygons) are not converted to boxes -- they belong to a segmentation track.

Output (default ~/workspace/datasets/detector_yolo/aec/):
    images/eval/sheet_01.png ...
    labels/eval/sheet_01.txt ...
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes import CLASS_IDS, AEC_MAP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.expanduser(
        '~/workspace/datasets/aec-geometric-bench/dataset'))
    ap.add_argument('--out', default=os.path.expanduser(
        '~/workspace/datasets/detector_yolo/aec'))
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--render-only', nargs='*', default=None,
                    help='only render these sheet basenames (e.g. sheet_01)')
    args = ap.parse_args()

    import pymupdf
    from PIL import Image

    img_dir = os.path.join(args.out, 'images', 'eval')
    lbl_dir = os.path.join(args.out, 'labels', 'eval')
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)

    tree = ET.parse(os.path.join(args.src, 'annotations_15_scoring_ready.xml'))
    stats = Counter()
    for im in tree.getroot():
        if im.tag != 'image':
            continue
        name = im.attrib['name']            # sheet_01.png
        base = os.path.splitext(name)[0]
        if args.render_only and base not in args.render_only:
            continue
        W, H = int(float(im.attrib['width'])), int(float(im.attrib['height']))
        pdf = os.path.join(args.src, 'pdf', base + '.pdf')
        doc = pymupdf.open(pdf)
        page = doc[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(args.dpi / 72, args.dpi / 72))
        png_path = os.path.join(img_dir, base + '.png')
        pix.save(png_path)
        doc.close()
        if (pix.width, pix.height) != (W, H):
            # exact resize so CVAT coords stay valid
            with Image.open(png_path) as im_pil:
                im_pil.resize((W, H), Image.LANCZOS).save(png_path)
            stats['resized'] += 1
        lines = []
        for el in im:
            if el.tag == 'polygon':
                stats['skipped_polygon_' + el.attrib.get('label', '?')] += 1
                continue
            if el.tag != 'box':
                continue
            label = el.attrib.get('label')
            uni = AEC_MAP.get(label, 'UNKNOWN')
            if uni is None:
                stats['skipped_' + label.replace(' ', '_')] += 1
                continue
            if uni == 'UNKNOWN':
                stats['unknown_label_' + str(label)] += 1
                continue
            x0 = float(el.attrib['xtl']); y0 = float(el.attrib['ytl'])
            x1 = float(el.attrib['xbr']); y1 = float(el.attrib['ybr'])
            cx, cy = ((x0 + x1) / 2) / W, ((y0 + y1) / 2) / H
            w, h = (x1 - x0) / W, (y1 - y0) / H
            lines.append(f"{CLASS_IDS[uni]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            stats['box_' + uni] += 1
        with open(os.path.join(lbl_dir, base + '.txt'), 'w') as f:
            f.write('\n'.join(lines))
        stats['sheets'] += 1
    print('converter stats:', dict(stats))


if __name__ == '__main__':
    main()
