#!/usr/bin/env python3
"""From-scratch PyTorch door/window detector for synthetic sheets (#743).

License-clean path for #743: a tiny custom detector (no ultralytics import
anywhere in this module) trained from random init on a YOLO-format dataset
converted from matchline's own synthetic sheets (see
``detector/convert_synth.py``). Outputs a state-dict checkpoint, config and
metrics json under ``--out`` (default
``~/workspace/datasets/detector_runs/scratch_<name>/``), never in the repo.

Model: YOLOv1-lite. Grayscale input letterboxed to ``--imgsz`` (square,
white padding); stride-8 dense grid of S/8 x S/8 cells; each cell predicts
C class logits + 1 objectness + 4 box params (cx, cy, w, h, S-normalized,
sigmoid in [0, 1]). Positives are cells whose center falls inside a GT box
(center-in-box, multi-positive). Loss: BCE obj (positives + hard-negative
subsample), BCE cls (positives), CIoU box (positives).

Usage:
    python3 detector/train_scratch.py --data <converted-dataset-dir> \
        --epochs 25 --device cuda --name synth_v1
    python3 detector/train_scratch.py --data ... --eval-only --out <dir> \
        --ckpt <dir>/ckpt.pt
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
from pathlib import Path

import numpy as np
import torch

try:  # package import: python -m detector.train_scratch, pytest
    from detector.ap50 import average_precision, mean_average_precision
except ImportError:  # direct script execution from inside detector/
    from ap50 import average_precision, mean_average_precision
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image

PAD_VALUE = 255.0  # white: matches drawing background


# ---------------------------------------------------------------------------
# Letterbox + label transforms
# ---------------------------------------------------------------------------


def letterbox_params(orig_w, orig_h, size):
    """(scale, dx, dy) mapping orig px coords into a size x size letterbox."""
    scale = min(size / orig_w, size / orig_h)
    dx = (size - orig_w * scale) / 2.0
    dy = (size - orig_h * scale) / 2.0
    return scale, dx, dy


def letterbox_image(img, size, scale, dx, dy):
    """Resize + pad a 2-D grayscale array into a size x size white-padded array."""
    h, w = img.shape
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    pil = Image.fromarray(img.astype(np.uint8), mode="L").resize((nw, nh), Image.BILINEAR)
    im = np.asarray(pil, dtype=np.float32)
    out = np.full((size, size), PAD_VALUE, dtype=np.float32)
    x0, y0 = int(round(dx)), int(round(dy))
    x1, y1 = min(size, x0 + nw), min(size, y0 + nh)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = im[: y1 - y0, : x1 - x0]
    return out


def transform_rows(rows, size, scale, dx, dy, orig_w, orig_h):
    """YOLO rows (cls, cx, cy, w, h normalized to orig sheet) -> size-normalized."""
    out = []
    for r in rows:
        cls = int(r[0])
        cx, cy, w, h = (float(v) for v in r[1:5])
        lb_cx = cx * orig_w * scale + dx
        lb_cy = cy * orig_h * scale + dy
        lb_w, lb_h = w * orig_w * scale, h * orig_h * scale
        out.append((cls, lb_cx / size, lb_cy / size, lb_w / size, lb_h / size))
    return out


def untransform_boxes(boxes, size, scale, dx, dy, orig_w, orig_h):
    """Reverse of transform_rows for inference output.

    boxes: iterable of (cls, cx, cy, w, h) in size-normalized units, or
    decode-emission rows (cls, score, cx, cy, w, h). A score travels through
    unchanged so callers never re-align boxes against a parallel row list
    (the #940 duck finding 2): the return matches the input shape, either
    (cls, x0, y0, w, h) or (cls, score, x0, y0, w, h), normalized to the
    orig sheet.
    """
    out = []
    for b in boxes:
        if len(b) == 6:  # decode emission: (cls, score, cx, cy, w, h)
            cls, score, cx, cy, w, h = b
        else:
            cls, cx, cy, w, h = b
            score = None
        px_cx, px_cy = (cx * size - dx) / scale, (cy * size - dy) / scale
        px_w, px_h = w * size / scale, h * size / scale
        row = (
            (px_cx - px_w / 2) / orig_w,
            (px_cy - px_h / 2) / orig_h,
            px_w / orig_w,
            px_h / orig_h,
        )
        out.append((int(cls), score, *row) if score is not None else (int(cls), *row))
    return out


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _conv(cin, cout, stride):
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, stride=stride, padding=1, bias=False),
        nn.BatchNorm2d(cout),
        nn.SiLU(inplace=True),
    )


class ScratchNet(nn.Module):
    """Tiny YOLOv1-lite: 4 stride-2 stages, stride-8 detection head.

    imgsz must be divisible by 8; grid = imgsz // 8 cells per side.
    Head outputs C + 1 + 4 channels per cell.
    """

    def __init__(self, num_classes=2, width=32, imgsz=640, wall_classes=0):
        super().__init__()
        assert imgsz % 8 == 0, "imgsz must be divisible by 8"
        self.num_classes = num_classes
        self.imgsz = imgsz
        self.grid = imgsz // 8
        self.wall_classes = int(wall_classes)
        w2, w4 = width * 2, width * 4
        self.backbone = nn.Sequential(
            _conv(1, width, 2),  # /2
            _conv(width, w2, 2),  # /4
            _conv(w2, w4, 2),  # /8
            _conv(w4, w4, 1),
            _conv(w4, w4, 1),
        )
        self.head = nn.Sequential(
            _conv(w4, w4, 1),
            nn.Conv2d(w4, num_classes + 1 + 4, 1),
        )
        if self.wall_classes > 0:
            # Auxiliary wall-class head (#1113): coarse wall TYPE per grid
            # cell (partition vs structural) on wall-covered cells. Shares
            # the backbone; supervision comes from the generator's wall
            # kinds via .wallcls.txt labels. Coarse class only: physical
            # width regression off raster pixels is out of scope (#1113).
            self.wall_head = nn.Sequential(
                _conv(w4, w4, 1),
                nn.Conv2d(w4, self.wall_classes, 1),
            )

    def forward(self, x):
        feat = self.backbone(x)
        det = self.head(feat)  # (B, C+5, G, G)
        if self.wall_classes > 0:
            return det, self.wall_head(feat)  # ((B, C+5, G, G), (B, W, G, G))
        return det


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------


def ciou_loss(pred, target):
    """Mean CIoU loss between (N, 4) xywh S-normalized box sets."""
    px, py, pw, ph = pred.unbind(-1)
    tx, ty, tw, th = target.unbind(-1)
    ix0 = torch.max(px - pw / 2, tx - tw / 2)
    iy0 = torch.max(py - ph / 2, ty - th / 2)
    ix1 = torch.min(px + pw / 2, tx + tw / 2)
    iy1 = torch.min(py + ph / 2, ty + th / 2)
    inter = (ix1 - ix0).clamp(min=0) * (iy1 - iy0).clamp(min=0)
    union = pw.clamp(min=0) * ph.clamp(min=0) + tw.clamp(min=0) * th.clamp(min=0) - inter
    iou = inter / union.clamp(min=1e-9)
    ex1 = torch.max(px + pw / 2, tx + tw / 2)
    ey1 = torch.max(py + ph / 2, ty + th / 2)
    ex0 = torch.min(px - pw / 2, tx - tw / 2)
    ey0 = torch.min(py - ph / 2, ty - th / 2)
    cw = (ex1 - ex0).clamp(min=1e-9)
    ch = (ey1 - ey0).clamp(min=1e-9)
    rho = ((px - tx) ** 2 + (py - ty) ** 2) / (cw * cw + ch * ch)
    v = (
        (4 / math.pi**2)
        * (tw / th.clamp(min=1e-9)).atan().sub((pw / ph.clamp(min=1e-9)).atan()).pow(2)
    ).clamp(max=4 / math.pi**2)
    with torch.no_grad():
        alpha = v / (1 - iou + v + 1e-9)
    return (1.0 - iou + rho + alpha * v).mean()


class ScratchLoss(nn.Module):
    def __init__(
        self,
        num_classes=2,
        obj_weight=1.0,
        cls_weight=1.0,
        box_weight=5.0,
        neg_sample=3.0,
        wall_cls_weight=0.5,
    ):
        super().__init__()
        self.num_classes = num_classes
        self.obj_weight = obj_weight
        self.cls_weight = cls_weight
        self.box_weight = box_weight
        self.neg_sample = neg_sample
        self.wall_cls_weight = wall_cls_weight

    def forward(self, preds, targets, wall_preds=None, wall_t=None):
        """preds: (B, C+5, G, G) raw logits. targets: list of (n, 5) tensors.

        wall_preds/wall_t activate the auxiliary wall-class term (#1113):
        wall_preds (B, W, G, G) raw logits, wall_t (B, G, G) long with 0 =
        no wall (ignored), 1 = partition, 2 = structural.
        """
        B, CH, G, _ = preds.shape
        C = self.num_classes
        device = preds.device
        cls_raw = preds[:, :C].permute(0, 2, 3, 1)  # (B, G, G, C)
        obj_raw = preds[:, C]  # (B, G, G)
        box_raw = preds[:, C + 1 :].permute(0, 2, 3, 1)  # (B, G, G, 4)

        obj_t = torch.zeros(B, G, G, device=device)
        cls_t = torch.zeros(B, G, G, C, device=device)
        box_t = torch.zeros(B, G, G, 4, device=device)
        pos_mask = torch.zeros(B, G, G, dtype=torch.bool, device=device)

        for b, t in enumerate(targets):
            if t.numel() == 0:
                continue
            # targets arrive on CPU from the dataloader; move once so every
            # derived index shares preds' device (mixed-device indexing raised
            # "indices should be either on cpu or on the same device" in run
            # 38311804719 on CUDA)
            t = t.to(device)
            cls, cx, cy, w, h = t.unbind(-1)
            gi = (cy * G).long().clamp(0, G - 1)
            gj = (cx * G).long().clamp(0, G - 1)
            # keep only the first GT per cell (rare: two centers in one cell)
            first = torch.ones(len(gi), dtype=torch.bool, device=device)
            seen = {}
            for k in range(len(gi)):
                key = (gi[k].item(), gj[k].item())
                if key in seen:
                    first[k] = False
                else:
                    seen[key] = True
            # slice the box values too: leaving cx/cy/w/h at full length made
            # box_t[b, gi, gj] = ... a [n,4]-into-[k,4] scatter that crashed
            # whenever two GT centers shared a cell
            gi, gj, cls = gi[first], gj[first], cls[first]
            cx, cy, w, h = cx[first], cy[first], w[first], h[first]
            obj_t[b, gi, gj] = 1.0
            cls_t[b, gi, gj, cls.long()] = 1.0
            # box targets stay image-normalized ((cx, cy, w, h) straight from
            # the labels) so every CIoU term shares one coordinate frame; the
            # prediction side re-derives cell-relative centers below. The old
            # mixed frame (cell-relative centers vs image-normalized sizes)
            # made IoU ~0 for almost any prediction, pinning box loss at ~1.0
            # and mean F1 at 0.0 through training run 38317139729.
            box_t[b, gi, gj] = torch.stack([cx, cy, w, h], -1)
            pos_mask[b, gi, gj] = True

        pos_idx = pos_mask.nonzero(as_tuple=False)
        neg_mask = ~pos_mask
        n_neg = min(int(self.neg_sample * max(len(pos_idx), 1)), int(neg_mask.sum()))
        if n_neg > 0:
            neg_idx = neg_mask.nonzero(as_tuple=False)
            sel = neg_idx[torch.randperm(len(neg_idx), device=device)[:n_neg]]
            obj_sel = torch.cat([pos_idx, sel], 0)
        else:
            obj_sel = pos_idx

        if len(obj_sel):
            bi, gi, gj = obj_sel.unbind(-1)
            obj_loss = F.binary_cross_entropy_with_logits(obj_raw[bi, gi, gj], obj_t[bi, gi, gj])
        else:
            obj_loss = torch.zeros((), device=device)

        if len(pos_idx):
            bi, gi, gj = pos_idx.unbind(-1)
            cls_loss = F.binary_cross_entropy_with_logits(cls_raw[bi, gi, gj], cls_t[bi, gi, gj])
            # (tx, ty) sigmoid to cell-relative offsets; (w, h) stay linear
            # image-normalized fractions. Boxes span 1.2-5.3 cells in the
            # synthetic sheets, so a sigmoid on w/h can never represent them.
            bp = box_raw[bi, gi, gj]
            pred_xywh = torch.stack(
                [
                    (gj.float() + bp[:, 0].sigmoid()) / G,
                    (gi.float() + bp[:, 1].sigmoid()) / G,
                    bp[:, 2].clamp(0.0, 1.0),
                    bp[:, 3].clamp(0.0, 1.0),
                ],
                -1,
            )
            box_loss = ciou_loss(pred_xywh, box_t[bi, gi, gj])
        else:
            cls_loss = torch.zeros((), device=device)
            box_loss = torch.zeros((), device=device)

        total = self.obj_weight * obj_loss + self.cls_weight * cls_loss + self.box_weight * box_loss
        parts = {"obj": obj_loss.item(), "cls": cls_loss.item(), "box": box_loss.item()}
        if wall_preds is not None and wall_t is not None:
            wt = wall_t.to(device)
            wsel = wt > 0
            if wsel.any():
                # map values are 1/2 (1 = partition, 2 = structural); head
                # classes are 0/1, so shift down before the CE
                wall_loss = F.cross_entropy(
                    wall_preds.permute(0, 2, 3, 1)[wsel], (wt[wsel] - 1).long()
                )
            else:
                wall_loss = torch.zeros((), device=device)
            total = total + self.wall_cls_weight * wall_loss
            parts["wall"] = wall_loss.item()
        return total, parts


# ---------------------------------------------------------------------------
# Inference helpers (pure torch; the future backend imports from here)
# ---------------------------------------------------------------------------


def box_iou(a, b):
    """IoU of two (cls, cx, cy, w, h) rows in normalized units."""
    ax0, ay0, ax1, ay1 = a[1] - a[3] / 2, a[2] - a[4] / 2, a[1] + a[3] / 2, a[2] + a[4] / 2
    bx0, by0, bx1, by1 = b[1] - b[3] / 2, b[2] - b[4] / 2, b[1] + b[3] / 2, b[2] + b[4] / 2
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a[3] * a[4] + b[3] * b[4] - inter
    return inter / union if union > 0 else 0.0


def nms(rows, iou_thresh=0.5):
    """Greedy class-agnostic NMS over (cls, score, cx, cy, w, h) rows."""
    rows = sorted(rows, key=lambda r: -r[1])
    kept = []
    for r in rows:
        if all(
            box_iou((r[0], r[2], r[3], r[4], r[5]), (k[0], k[2], k[3], k[4], k[5])) < iou_thresh
            for k in kept
        ):
            kept.append(r)
    return kept


def decode(preds, num_classes=2, conf=0.25):
    """(B, C+5, G, G) raw -> per-image list of (cls, score, cx, cy, w, h) normalized."""
    B, CH, G, _ = preds.shape
    C = num_classes
    cls_prob = preds[:, :C].sigmoid().permute(0, 2, 3, 1)  # (B, G, G, C)
    obj_prob = preds[:, C].sigmoid()  # (B, G, G)
    box = preds[:, C + 1 :].permute(0, 2, 3, 1)  # (B, G, G, 4) raw
    score = cls_prob * obj_prob.unsqueeze(-1)
    out = []
    for b in range(B):
        keep = score[b] > conf
        rows = []
        for c in range(C):
            yi, xj = keep[..., c].nonzero(as_tuple=True)
            for i, j in zip(yi.tolist(), xj.tolist()):
                # cells: i indexes y, j indexes x; (tx, ty) are cell-relative
                # sigmoid offsets, (w, h) linear image-normalized fractions
                txy = box[b, i, j, :2].sigmoid().tolist()
                wh = box[b, i, j, 2:].clamp(0.0, 1.0).tolist()
                cx = (j + txy[0]) / G
                cy = (i + txy[1]) / G
                rows.append((c, float(score[b, i, j, c]), cx, cy, wh[0], wh[1]))
        out.append(nms(rows))
    return out


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def rasterize_wall_rows(rows, imgsz):
    """wallcls rows (cls cx cy w h, size-normalized) -> (G, G) long class map.

    0 = no wall, 1 = partition, 2 = structural (cls + 1). A cell is marked
    when its area overlaps the wall band (pure overlap, not a center test),
    so even a sub-cell-thin wall at whole-sheet scale marks the cell it sits
    in. Structural wins overlaps: walls meet at corners and a partition
    endpoint can sit inside a structural wall's band (#1113).
    """
    G = imgsz // 8
    wall = torch.zeros(G, G, dtype=torch.long)
    for r in rows:
        cls = int(r[0])
        _, cx, cy, w, h = r
        x0n, x1n = cx - w / 2.0, cx + w / 2.0
        y0n, y1n = cy - h / 2.0, cy + h / 2.0
        j0 = max(0, int(math.floor(x0n * G)))
        j1 = min(G - 1, int(math.ceil(x1n * G)) - 1)
        i0 = max(0, int(math.floor(y0n * G)))
        i1 = min(G - 1, int(math.ceil(y1n * G)) - 1)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if wall[i, j].item() == 0 or cls == 1:
                    wall[i, j] = cls + 1
    return wall


class YoloDataset(torch.utils.data.Dataset):
    """YOLO-format dataset tree (images/{train,val}, labels/{train,val}).

    load_wall=True additionally reads labels/<stem>.wallcls.txt (#1113) and
    returns a per-cell wall-class map; missing files degrade to all-zero
    maps (no supervised wall cells), so older datasets keep training.
    """

    def __init__(self, root, split, imgsz=640, flip=True, load_wall=False):
        self.img_dir = Path(root) / "images" / split
        self.lbl_dir = Path(root) / "labels" / split
        self.items = sorted(p.stem for p in self.img_dir.glob("*.png"))
        if not self.items:
            raise FileNotFoundError(f"no images under {self.img_dir}")
        self.imgsz = imgsz
        self.flip = flip
        self.load_wall = load_wall

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        stem = self.items[i]
        img = np.asarray(Image.open(self.img_dir / f"{stem}.png").convert("L"))
        oh, ow = img.shape
        scale, dx, dy = letterbox_params(ow, oh, self.imgsz)
        lb = letterbox_image(img, self.imgsz, scale, dx, dy)
        rows = []
        lbl = self.lbl_dir / f"{stem}.txt"
        if lbl.exists():
            for line in lbl.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    rows.append([float(parts[0])] + [float(v) for v in parts[1:5]])
        wrows = []
        if self.load_wall:
            wlbl = self.lbl_dir / f"{stem}.wallcls.txt"
            if wlbl.exists():
                for line in wlbl.read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        wrows.append([float(parts[0])] + [float(v) for v in parts[1:5]])
        do_flip = self.flip and random.random() < 0.5
        if do_flip:
            lb = lb[:, ::-1].copy()
            rows = [[r[0], 1.0 - r[1], r[2], r[3], r[4]] for r in rows]
            wrows = [[r[0], 1.0 - r[1], r[2], r[3], r[4]] for r in wrows]
        x = torch.from_numpy(lb / 255.0).unsqueeze(0)  # (1, S, S)
        t = torch.tensor(
            transform_rows(rows, self.imgsz, scale, dx, dy, ow, oh), dtype=torch.float32
        ).reshape(-1, 5)
        meta = {"stem": stem, "scale": scale, "dx": dx, "dy": dy, "orig_w": ow, "orig_h": oh}
        if not self.load_wall:
            return x, t, meta
        wall = rasterize_wall_rows(
            transform_rows(wrows, self.imgsz, scale, dx, dy, ow, oh), self.imgsz
        )
        return x, t, wall, meta


def collate(batch):
    xs = torch.stack([b[0] for b in batch])
    ts = [b[1] for b in batch]
    if len(batch[0]) == 4:  # wall-head mode: (x, t, wall, meta)
        ws = torch.stack([b[2] for b in batch])
        return xs, ts, ws, [b[3] for b in batch]
    meta = [b[2] for b in batch]
    return xs, ts, meta


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(model, loader, device, names, iou_thresh=0.5, conf=0.25, wall_out=None):
    """IoU-matched precision/recall/F1 per class. Returns {name: {...}}.

    wall_out: optional list; one wall-class accuracy entry (float, or None
    when no wall-labelled cells were seen) is appended per call (#1113).
    """
    model.eval()
    C = model.num_classes
    tp = [0] * C
    fp = [0] * C
    fn = [0] * C
    # Per-detection (confidence, matched) pairs feed the AP50 computation;
    # they come from the same greedy matching pass as the P/R/F1 counts,
    # so both metrics always describe identical detections (#1119).
    pairs: list[list[tuple[float, bool]]] = [[] for _ in range(C)]
    wall_ok = 0
    wall_n = 0
    with torch.no_grad():
        for batch in loader:
            if len(batch) == 4:  # wall-head mode: (x, t, wall, meta)
                imgs, targets, wall_t, _meta = batch
            else:
                imgs, targets, _meta = batch
                wall_t = None
            out = model(imgs.to(device))
            if isinstance(out, tuple):  # (det, wall) when the wall head is on
                preds, wall_preds = out
            else:
                preds, wall_preds = out, None
            if wall_t is not None and wall_preds is not None:
                wt = wall_t.to(device)
                mask = wt > 0
                if mask.any():
                    # head class ids sit 1 below the map values (see ScratchLoss)
                    wall_ok += int(((wall_preds.argmax(1) + 1)[mask] == wt[mask]).sum())
                    wall_n += int(mask.sum())
            decoded = decode(preds, num_classes=C, conf=conf)
            for b in range(len(decoded)):
                gts = [g for g in targets[b].tolist() if g]
                dets = decoded[b]
                matched = [False] * len(dets)
                for gt in gts:
                    best, best_iou = -1, iou_thresh
                    for idx, d in enumerate(dets):
                        if matched[idx] or int(d[0]) != int(gt[0]):
                            continue
                        i = box_iou((d[0], d[2], d[3], d[4], d[5]), tuple(gt))
                        if i >= best_iou:
                            best, best_iou = idx, i
                    if best >= 0:
                        matched[best] = True
                        tp[int(gt[0])] += 1
                    else:
                        fn[int(gt[0])] += 1
                for idx, d in enumerate(dets):
                    pairs[int(d[0])].append((float(d[1]), matched[idx]))
                    if not matched[idx]:
                        fp[int(d[0])] += 1
    per_class = {}
    for c in range(C):
        name = names[c] if c < len(names) else str(c)
        p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0.0
        r = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0.0
        per_class[name] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
            "tp": tp[c],
            "fp": fp[c],
            "fn": fn[c],
            # AP50 (all-point interpolated, same greedy matches). None when
            # the class has no ground truth: AP is undefined there.
            "ap50": None
            if (ap := average_precision(pairs[c], tp[c] + fn[c])) is None
            else round(ap, 4),
        }
    if wall_out is not None:
        wall_out.append((wall_ok / wall_n) if wall_n else None)
    return per_class


# ---------------------------------------------------------------------------
# Train / main
# ---------------------------------------------------------------------------


def load_names(data_dir):
    """Read class names from the converter's data.yaml (minimal parser)."""
    yaml_path = Path(data_dir) / "data.yaml"
    if not yaml_path.exists():
        return []
    names = []
    in_names = False
    for line in yaml_path.read_text().splitlines():
        if line.strip() == "names:":
            in_names = True
            continue
        if in_names:
            if line.startswith("  "):
                names.append(line.split(":", 1)[1].strip())
            else:
                break
    return names


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def init_box_head_bias(model, labels_dir, num_classes=2):
    """Set box-channel head biases from dataset label stats.

    tx/ty targets are uniform in [0, 1), so bias 0 puts their sigmoid at
    0.5; w/h are linear image-normalized fractions around 0.03-0.13, so
    their biases start at the label means. The box channels' conv weights
    are zeroed too: with random weights the trunk contribution (std ~0.3
    for width-16 heads) swamps the bias and the head starts tens of pixels
    wide again, which is what pinned box loss at ~1.0 and mean F1 at 0.0
    through training run 38317139729. Reads train labels once.

    Returns (mean_w, mean_h, n_boxes), or None when no labels exist.
    """
    root = Path(labels_dir)
    ws, hs = [], []
    for f in sorted(root.glob("*.txt")):
        for line in f.read_text().splitlines():
            parts = line.split()
            if len(parts) == 5:
                ws.append(float(parts[3]))
                hs.append(float(parts[4]))
    if not ws:
        return None
    mean_w = sum(ws) / len(ws)
    mean_h = sum(hs) / len(hs)

    head = model.head[-1]
    with torch.no_grad():
        head.weight[num_classes + 1 : num_classes + 5].zero_()
        bias = head.bias
        bias[num_classes + 1] = 0.0  # tx: targets uniform in [0, 1)
        bias[num_classes + 2] = 0.0  # ty
        bias[num_classes + 3] = mean_w
        bias[num_classes + 4] = mean_h
    return mean_w, mean_h, len(ws)


def save_train_state(path, model, opt, sched, epoch, best_f1, history, cfg):
    """Persist resumable training state (#1116 ROCm crash recovery).

    Saves everything needed to continue an interrupted run at the next
    epoch boundary: model/optimizer/scheduler state, loop position, best
    F1, history, config, and the torch/numpy/python RNG states so a
    resumed run reproduces the uninterrupted trajectory's batch order.
    Written every N epochs when --checkpoint-every is set; separate from
    the best-F1-only ckpt.pt, which stays the eval artifact.
    """
    torch.save(
        {
            "model": model.state_dict(),
            "opt": opt.state_dict(),
            "sched": sched.state_dict(),
            "epoch": epoch,
            "best_f1": best_f1,
            "history": history,
            "config": cfg,
            "rng_torch": torch.get_rng_state(),
            "rng_numpy": np.random.get_state(),
            "rng_python": random.getstate(),
        },
        path,
    )


def load_train_state(path, model, opt, sched):
    """Restore a save_train_state checkpoint; returns the loop position.

    Loads on CPU and lets load_state_dict move tensors to the model's
    device. weights_only=False because numpy/python RNG states carry
    non-whitelisted objects; these files only come from runs we control
    (same tradeoff as the legacy ckpt.pt fallback).
    """
    state = torch.load(path, map_location="cpu", weights_only=False)
    model.load_state_dict(state["model"])
    opt.load_state_dict(state["opt"])
    sched.load_state_dict(state["sched"])
    torch.set_rng_state(state["rng_torch"])
    np.random.set_state(state["rng_numpy"])
    random.setstate(state["rng_python"])
    return {
        "epoch": state["epoch"],
        "best_f1": state["best_f1"],
        "history": state["history"],
        "config": state.get("config", {}),
    }


def train(args):
    device = torch.device(
        args.device
        if args.device != "cpu" or not torch.cuda.is_available()
        else "cuda"
        if args.device == "auto"
        else args.device
    )
    set_seed(args.seed)
    names = load_names(args.data)
    train_ds = YoloDataset(args.data, "train", imgsz=args.imgsz, load_wall=args.wall_head > 0)
    val_ds = YoloDataset(
        args.data, "val", imgsz=args.imgsz, flip=False, load_wall=args.wall_head > 0
    )
    train_ld = torch.utils.data.DataLoader(
        train_ds, batch_size=args.batch, shuffle=True, num_workers=args.workers, collate_fn=collate
    )
    val_ld = torch.utils.data.DataLoader(
        val_ds, batch_size=args.batch, shuffle=False, num_workers=args.workers, collate_fn=collate
    )
    model = ScratchNet(
        num_classes=max(2, len(names)) or 2,
        width=args.width,
        imgsz=args.imgsz,
        wall_classes=args.wall_head,
    ).to(device)
    criterion = ScratchLoss(num_classes=model.num_classes, wall_cls_weight=args.wall_cls_weight)
    init = init_box_head_bias(
        model, Path(args.data) / "labels" / "train", num_classes=model.num_classes
    )
    if init:
        print(
            f"box head bias init: mean w {init[0]:.4f} h {init[1]:.4f} from {init[2]} train boxes",
            flush=True,
        )
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

    out_dir = Path(
        args.out
        if args.out
        else os.path.expanduser(f"~/workspace/datasets/detector_runs/scratch_{args.name}")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "num_classes": model.num_classes,
        "width": args.width,
        "imgsz": args.imgsz,
        "names": names,
        "wall_classes": args.wall_head,
    }
    history = []
    best_f1 = -1.0
    start_epoch = 0
    if args.resume:
        src = Path(os.path.expanduser(args.resume))
        state = load_train_state(src / "ckpt_last.pt", model, opt, sched)
        saved_wc = int((state.get("config") or {}).get("wall_classes", 0))
        if saved_wc != args.wall_head:
            raise ValueError(
                f"resume mismatch: ckpt_last.pt was trained with wall_classes={saved_wc} "
                f"but --wall-head is {args.wall_head}; the head shape cannot change mid-run"
            )
        start_epoch = state["epoch"] + 1
        best_f1 = state["best_f1"]
        history = state["history"]
        best_src = src / "ckpt.pt"
        if best_src.exists() and best_src.resolve() != (out_dir / "ckpt.pt").resolve():
            shutil.copyfile(best_src, out_dir / "ckpt.pt")
        print(
            f"resumed from {src} at epoch {start_epoch}/{args.epochs} (best_f1 {best_f1:.4f})",
            flush=True,
        )
    wall_stats: list = []
    for epoch in range(start_epoch, args.epochs):
        model.train()
        tot = 0.0
        for batch in train_ld:
            if len(batch) == 4:  # wall-head mode: (x, t, wall, meta)
                imgs, targets, wall_t, _meta = batch
            else:
                imgs, targets, _meta = batch
                wall_t = None
            opt.zero_grad()
            out = model(imgs.to(device))
            if isinstance(out, tuple):  # (det, wall) when the wall head is on
                det, wall_preds = out
            else:
                det, wall_preds = out, None
            loss, parts = criterion(det, targets, wall_preds, wall_t)
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        wall_stats.clear()
        metrics = evaluate(model, val_ld, device, names, wall_out=wall_stats)
        mean_f1 = sum(m["f1"] for m in metrics.values()) / len(metrics) if metrics else 0.0
        entry = {
            "epoch": epoch,
            "loss": round(tot / max(len(train_ld), 1), 4),
            "mean_f1": round(mean_f1, 4),
            **parts,
        }
        if wall_stats and wall_stats[-1] is not None:
            entry["wall_cls_acc"] = round(wall_stats[-1], 4)
        history.append(entry)
        if mean_f1 >= best_f1:
            best_f1 = mean_f1
            torch.save({"model": model.state_dict(), "config": cfg}, out_dir / "ckpt.pt")
        if args.checkpoint_every and (epoch + 1) % args.checkpoint_every == 0:
            save_train_state(
                out_dir / "ckpt_last.pt", model, opt, sched, epoch, best_f1, history, cfg
            )
        print(
            f"epoch {epoch}: loss {history[-1]['loss']} mean_f1 {history[-1]['mean_f1']}",
            flush=True,
        )
    final_payload = {
        "best_mean_f1": round(best_f1, 4),
        "epochs": args.epochs,
        "history": history,
        "final_per_class": metrics,
        "map50": mean_average_precision(metrics),
    }
    if wall_stats and wall_stats[-1] is not None:
        final_payload["final_wall_cls_acc"] = round(wall_stats[-1], 4)
    (out_dir / "metrics.json").write_text(json.dumps(final_payload, indent=1))
    print(f"saved -> {out_dir} (best_mean_f1 {best_f1:.4f})")
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="converted dataset dir (data.yaml)")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--width", type=int, default=32)
    ap.add_argument(
        "--wall-head",
        type=int,
        default=0,
        help="auxiliary wall-class head classes (0 = off, 2 = partition vs structural, #1113)",
    )
    ap.add_argument(
        "--wall-cls-weight",
        type=float,
        default=0.5,
        help="weight of the auxiliary wall-class loss term (#1113)",
    )
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--name", default="synth_v1")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--eval-only",
        action="store_true",
        help="load --ckpt and evaluate, write metrics.json to --out",
    )
    ap.add_argument("--ckpt", default=None)
    ap.add_argument(
        "--checkpoint-every",
        type=int,
        default=0,
        help="save resumable train state to <out>/ckpt_last.pt every N epochs (0 = off, #1116)",
    )
    ap.add_argument(
        "--resume",
        default="",
        help="dir holding ckpt_last.pt from an interrupted run; resumes training"
        " and copies its best ckpt.pt into --out (#1116)",
    )
    args = ap.parse_args()
    if args.eval_only:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        names = load_names(args.data)
        try:
            ckpt = torch.load(args.ckpt, map_location=device, weights_only=True)
        except Exception:
            # legacy checkpoints pickle non-whitelisted objects; those only
            # come from workflows we control, so keep the permissive load
            # as a fallback (#743 hardening)
            ckpt = torch.load(args.ckpt, map_location=device, weights_only=False)
        cfg = ckpt["config"]
        state_dict = ckpt["model"]
        wall_classes = int(cfg.get("wall_classes", 0)) if isinstance(cfg, dict) else 0
        if wall_classes == 0:
            # older checkpoints carry no wall head; infer from state-dict keys
            # so eval-only keeps working on pre-#1113 ckpt.pt files
            wc_conv = [
                k for k in state_dict if k.startswith("wall_head.") and state_dict[k].dim() == 4
            ]
            if wc_conv:
                wall_classes = int(state_dict[sorted(wc_conv)[-1]].shape[0])
        model = ScratchNet(
            num_classes=cfg["num_classes"],
            width=cfg["width"],
            imgsz=cfg["imgsz"],
            wall_classes=wall_classes,
        ).to(device)
        model.load_state_dict(ckpt["model"])
        val_ld = torch.utils.data.DataLoader(
            YoloDataset(
                args.data, "val", imgsz=cfg["imgsz"], flip=False, load_wall=wall_classes > 0
            ),
            batch_size=8,
            collate_fn=collate,
        )
        wall_stats = []
        metrics = evaluate(model, val_ld, device, names, wall_out=wall_stats)
        out_dir = Path(args.out or Path(args.ckpt).parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {**metrics, "map50": mean_average_precision(metrics)}
        if wall_stats and wall_stats[-1] is not None:
            payload["wall_cls_acc"] = round(wall_stats[-1], 4)
        (out_dir / "eval_metrics.json").write_text(json.dumps(payload, indent=1))
        print(json.dumps(payload, indent=1))
        return
    train(args)


if __name__ == "__main__":
    main()
