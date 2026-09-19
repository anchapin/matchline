#!/usr/bin/env python3
"""SAHI-style tiled inference for large sheets (PID pattern, stage 1).

Sheets are 4800x7200+ px; the detector trains at ~640-1408px. This slices the
sheet into overlapping tiles (default 1024px, 20% overlap), runs the model per
tile, shifts boxes back to sheet coordinates, and merges with class-wise NMS.

Usage:
    python3 sahi_infer.py --weights <best.pt> --image sheet_01.png \
        --out /tmp/preds.json [--tile 1024 --overlap 0.2 --conf 0.25]

Output JSON: [{"cls": int, "conf": float, "x0":..,"y0":..,"x1":..,"y1":..}] in
full-sheet pixel coordinates.
"""
import argparse
import json
import os


def tile_boxes(W, H, tile=1024, overlap=0.2):
    stride = int(tile * (1 - overlap))
    boxes = []
    y = 0
    while True:
        x = 0
        y1 = min(y + tile, H)
        y0 = y1 - tile if y1 == H and H > tile else y
        while True:
            x1 = min(x + tile, W)
            x0 = x1 - tile if x1 == W and W > tile else x
            boxes.append((x0, y0, x1, y1))
            if x1 >= W:
                break
            x += stride
        if y1 >= H:
            break
        y += stride
    return boxes


def nms(boxes, iou_thr=0.5):
    """Class-wise greedy NMS. boxes: list of dicts with cls/conf/x0..y1."""
    import numpy as np
    out = []
    boxes = sorted(boxes, key=lambda b: -b['conf'])
    for cls in set(b['cls'] for b in boxes):
        cand = [b for b in boxes if b['cls'] == cls]
        keep = []
        while cand:
            best = cand.pop(0)
            keep.append(best)
            rest = []
            for b in cand:
                xx0, yy0 = max(best['x0'], b['x0']), max(best['y0'], b['y0'])
                xx1, yy1 = min(best['x1'], b['x1']), min(best['y1'], b['y1'])
                inter = max(0, xx1 - xx0) * max(0, yy1 - yy0)
                a1 = (best['x1'] - best['x0']) * (best['y1'] - best['y0'])
                a2 = (b['x1'] - b['x0']) * (b['y1'] - b['y0'])
                iou = inter / max(1e-9, a1 + a2 - inter)
                if iou <= iou_thr:
                    rest.append(b)
            cand = rest
        out.extend(keep)
    return sorted(out, key=lambda b: -b['conf'])


def infer_sheet(weights, image_path, tile=1024, overlap=0.2, conf=0.25,
                iou_thr=0.5, imgsz=1024, device='cpu'):
    from ultralytics import YOLO
    from PIL import Image
    model = YOLO(weights)
    img = Image.open(image_path).convert('RGB')
    W, H = img.size
    all_preds = []
    for (x0, y0, x1, y1) in tile_boxes(W, H, tile, overlap):
        crop = img.crop((x0, y0, x1, y1))
        res = model.predict(crop, conf=conf, imgsz=imgsz, device=device,
                            verbose=False)[0]
        if res.boxes is None:
            continue
        for b in res.boxes:
            bx0, by0, bx1, by1 = (float(v) for v in b.xyxy[0].tolist())
            all_preds.append({
                'cls': int(b.cls[0]),
                'conf': float(b.conf[0]),
                'x0': bx0 + x0, 'y0': by0 + y0,
                'x1': bx1 + x0, 'y1': by1 + y0,
            })
    return nms(all_preds, iou_thr), (W, H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--image', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--tile', type=int, default=1024)
    ap.add_argument('--overlap', type=float, default=0.2)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--iou', type=float, default=0.5)
    ap.add_argument('--imgsz', type=int, default=1024)
    ap.add_argument('--device', default='cpu')
    args = ap.parse_args()

    preds, (W, H) = infer_sheet(args.weights, args.image, args.tile,
                                args.overlap, args.conf, args.iou,
                                args.imgsz, args.device)
    with open(args.out, 'w') as f:
        json.dump({'image': args.image, 'width': W, 'height': H,
                   'preds': preds}, f)
    from collections import Counter
    print(f'{args.image}: {len(preds)} preds after NMS',
          dict(Counter(p['cls'] for p in preds)))


if __name__ == '__main__':
    main()
