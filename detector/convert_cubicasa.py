#!/usr/bin/env python3
"""Convert CubiCasa5K SVG annotations to Ultralytics YOLO detection format.

For each floorplan in the train/val/test splits:
  - parse model.svg, walking the element tree while accumulating 2D affine
    transforms (matrix/translate/scale),
  - take every leaf <g> whose class is in CUBICASA_MAP (door/window symbol
    groups; container groups like "Doors" are excluded),
  - bounding-box all descendant geometry (polygon/polyline/rect/circle/
    line/path; bezier control points included conservatively),
  - map SVG viewBox coordinates to F1_scaled.png pixels with a per-axis scale
    (the PNGs are not exactly aspect-preserving vs the viewBox),
  - emit YOLO label files and copy the PNGs into a dataset tree.

Output (default ~/workspace/datasets/detector_yolo/cubicasa/):
    images/{train,val,test}/<id>.png
    labels/{train,val,test}/<id>.txt     # "cls cx cy w h" normalized

Images are rendered from model.svg with PyMuPDF at a uniform scale
(longest side = --render-size, default 1408), so SVG coordinates map to
pixels exactly by construction. We do NOT use F1_scaled.png: its
rasterization geometry does not match the SVG viewBox (verified empirically).

The door bbox includes the swing arc (standard for floorplan detection;
documented so takeoff code knows the box is the *symbol*, not the opening).
"""
import argparse
import math
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes import CLASS_IDS, CUBICASA_MAP

TARGET = set(CUBICASA_MAP)

# ---------------------------------------------------------------- transforms

def _parse_matrix(s):
    s = s.strip()
    m = re.match(r'matrix\(\s*([^)]+)\)', s)
    if m:
        v = [float(x) for x in re.split(r'[,\s]+', m.group(1).strip())]
        return tuple(v)  # a,b,c,d,e,f
    m = re.match(r'translate\(\s*([^)]+)\)', s)
    if m:
        v = [float(x) for x in re.split(r'[,\s]+', m.group(1).strip())]
        tx = v[0]
        ty = v[1] if len(v) > 1 else 0.0
        return (1, 0, 0, 1, tx, ty)
    m = re.match(r'scale\(\s*([^)]+)\)', s)
    if m:
        v = [float(x) for x in re.split(r'[,\s]+', m.group(1).strip())]
        sx = v[0]
        sy = v[1] if len(v) > 1 else sx
        return (sx, 0, 0, sy, 0, 0)
    return (1, 0, 0, 1, 0, 0)


def _compose(p, q):
    """Return p @ q for (a,b,c,d,e,f) with x'=a*x+c*y+e, y'=b*x+d*y+f."""
    a1, b1, c1, d1, e1, f1 = p
    a2, b2, c2, d2, e2, f2 = q
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply(t, x, y):
    a, b, c, d, e, f = t
    return (a * x + c * y + e, b * x + d * y + f)


# ------------------------------------------------------------------ geometry

def _poly_points(s):
    pts = []
    for tok in s.strip().split():
        if ',' in tok:
            x, y = tok.split(',')
            pts.append((float(x), float(y)))
    return pts


_PATH_RE = re.compile(r'([MmLlHhVvCcSsQqTtZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)')


def _path_points(d):
    """Collect points from an SVG path, including bezier control points
    (conservative bbox for the swing arcs on doors)."""
    toks = _PATH_RE.findall(d)
    pts = []
    cx = cy = 0.0
    sx = sy = 0.0  # subpath start
    i = 0
    cmd = None
    # flatten to token list
    flat = [a if a else b for a, b in toks]
    n = len(flat)
    i = 0
    while i < n:
        t = flat[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in 'Zz':
                cx, cy = sx, sy
                continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.upper()
        def num():
            nonlocal i
            v = float(flat[i]); i += 1
            return v
        if c == 'M':
            x, y = num(), num()
            if rel: x, y = cx + x, cy + y
            cx, cy = x, y; sx, sy = x, y
            pts.append((x, y)); cmd = 'l' if rel else 'L'
        elif c == 'L':
            x, y = num(), num()
            if rel: x, y = cx + x, cy + y
            cx, cy = x, y; pts.append((x, y))
        elif c == 'H':
            x = num()
            if rel: x = cx + x
            cx = x; pts.append((x, cy))
        elif c == 'V':
            y = num()
            if rel: y = cy + y
            cy = y; pts.append((cx, y))
        elif c == 'Q':
            x1, y1 = num(), num(); x, y = num(), num()
            if rel: x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            pts += [(x1, y1), (x, y)]; cx, cy = x, y
        elif c == 'C':
            x1, y1 = num(), num(); x2, y2 = num(), num(); x, y = num(), num()
            if rel:
                x1, y1, x2, y2, x, y = (cx + x1, cy + y1, cx + x2, cy + y2,
                                        cx + x, cy + y)
            pts += [(x1, y1), (x2, y2), (x, y)]; cx, cy = x, y
        elif c in 'ST':
            # smooth curves: approximate with endpoint only (rare in this data)
            k = 4 if c == 'C' else 2
            vals = [num() for _ in range(k)]
            x, y = vals[-2], vals[-1]
            if rel: x, y = cx + x, cy + y
            cx, cy = x, y; pts.append((x, y))
        else:
            i += 1
    return pts


def _shape_points(el):
    tag = el.tag.split('}')[-1]
    if tag == 'polygon' or tag == 'polyline':
        return _poly_points(el.get('points', ''))
    if tag == 'rect':
        x = float(el.get('x', 0)); y = float(el.get('y', 0))
        w = float(el.get('width', 0)); h = float(el.get('height', 0))
        return [(x, y), (x + w, y + h)]
    if tag == 'circle':
        cx = float(el.get('cx', 0)); cy = float(el.get('cy', 0))
        r = float(el.get('r', 0))
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if tag == 'line':
        return [(float(el.get('x1', 0)), float(el.get('y1', 0))),
                (float(el.get('x2', 0)), float(el.get('y2', 0)))]
    if tag == 'path':
        return _path_points(el.get('d', ''))
    return []


# ------------------------------------------------------------------ convert

def _hidden(el):
    style = (el.get('style') or '').replace(' ', '')
    return 'display:none' in style


def convert_plan(svg_path):
    """Return (viewBox_w, viewBox_h, [(cls_name, x0,y0,x1,y1), ...]) in SVG coords."""
    try:
        tree = ET.parse(svg_path)
    except ET.ParseError:
        return None
    root = tree.getroot()
    vb = root.get('viewBox')
    if vb:
        _, _, vbw, vbh = [float(x) for x in vb.split()]
    else:
        vbw = float(root.get('width', 0)); vbh = float(root.get('height', 0))
    out = []

    def walk(el, mat, hidden):
        hidden = hidden or _hidden(el)
        t = el.get('transform')
        if t:
            mat = _compose(mat, _parse_matrix(t))
        cls = el.get('class', '')
        tag = el.tag.split('}')[-1]
        # leaf symbol group?
        if (tag == 'g' and cls in TARGET and not hidden
                and not any(d.tag.split('}')[-1] == 'g'
                            and d.get('class', '') in TARGET for d in el.iter()
                            if d is not el)):
            xs, ys = [], []
            for d in el.iter():
                if d is el:
                    continue
                for (x, y) in _shape_points(d):
                    X, Y = _apply(mat, x, y)
                    xs.append(X); ys.append(Y)
            if xs:
                out.append((CUBICASA_MAP[cls], min(xs), min(ys), max(xs), max(ys)))
            return  # do not descend further (avoid double count)
        for child in el:
            walk(child, mat, hidden)

    walk(root, (1, 0, 0, 1, 0, 0), False)
    return vbw, vbh, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.expanduser(
        '~/workspace/datasets/cubicasa5k/cubicasa5k'))
    ap.add_argument('--out', default=os.path.expanduser(
        '~/workspace/datasets/detector_yolo/cubicasa'))
    ap.add_argument('--splits', nargs='+', default=['train', 'val', 'test'])
    ap.add_argument('--limit', type=int, default=0, help='max plans per split (0=all)')
    ap.add_argument('--render-size', type=int, default=1408,
                    help='longest image side in px (uniform SVG render scale)')
    args = ap.parse_args()

    import pymupdf
    from PIL import Image

    stats = Counter()
    for split in args.splits:
        img_dir = os.path.join(args.out, 'images', split)
        lbl_dir = os.path.join(args.out, 'labels', split)
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lbl_dir, exist_ok=True)
        plans = []
        with open(os.path.join(args.src, f'{split}.txt')) as f:
            for line in f:
                plans.append(line.strip().lstrip('/'))
        if args.limit:
            plans = plans[:args.limit]
        for rel in plans:
            plan_dir = os.path.join(args.src, rel)
            svg = os.path.join(plan_dir, 'model.svg')
            pid = rel.strip('/').replace('/', '_')
            if not os.path.exists(svg):
                stats['missing'] += 1
                continue
            res = convert_plan(svg)
            if res is None:
                stats['parse_fail'] += 1
                continue
            vbw, vbh, boxes = res
            # uniform render scale: exact SVG->pixel mapping by construction
            rscale = args.render_size / max(vbw, vbh)
            try:
                doc = pymupdf.open(svg)
                pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(rscale, rscale))
                pw, ph = pix.width, pix.height
                pix.save(os.path.join(img_dir, pid + '.png'))
                doc.close()
            except Exception:
                stats['render_fail'] += 1
                continue
            lines = []
            for cls_name, x0, y0, x1, y1 in boxes:
                x0, x1 = sorted((x0 * rscale, x1 * rscale))
                y0, y1 = sorted((y0 * rscale, y1 * rscale))
                x0 = max(0, x0); y0 = max(0, y0); x1 = min(pw, x1); y1 = min(ph, y1)
                if x1 <= x0 or y1 <= y0:
                    continue
                cx = ((x0 + x1) / 2) / pw
                cy = ((y0 + y1) / 2) / ph
                w = (x1 - x0) / pw
                h = (y1 - y0) / ph
                lines.append(f"{CLASS_IDS[cls_name]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
                stats[f'box_{cls_name}'] += 1
            with open(os.path.join(lbl_dir, pid + '.txt'), 'w') as f:
                f.write('\n'.join(lines))
            stats[f'plans_{split}'] += 1
    print('converter stats:', dict(stats))


if __name__ == '__main__':
    main()
