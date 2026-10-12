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
from pathlib import Path

import numpy as np
import torch
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

    boxes: iterable of (cls, cx, cy, w, h) in size-normalized units.
    Returns (cls, x0, y0, w, h) normalized to the orig sheet.
    """
    out = []
    for b in boxes:
        if len(b) == 6:  # decode emission: (cls, score, cx, cy, w, h)
            b = (b[0], b[2], b[3], b[4], b[5])
        cls, cx, cy, w, h = b
        px_cx, px_cy = (cx * size - dx) / scale, (cy * size - dy) / scale
        px_w, px_h = w * size / scale, h * size / scale
        out.append(
            (
                int(cls),
                (px_cx - px_w / 2) / orig_w,
                (px_cy - px_h / 2) / orig_h,
                px_w / orig_w,
                px_h / orig_h,
            )
        )
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

    def __init__(self, num_classes=2, width=32, imgsz=640):
        super().__init__()
        assert imgsz % 8 == 0, "imgsz must be divisible by 8"
        self.num_classes = num_classes
        self.imgsz = imgsz
        self.grid = imgsz // 8
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

    def forward(self, x):
        return self.head(self.backbone(x))  # (B, C+5, G, G)


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
    v = (4 / math.pi**2) * (tw / th).atan().sub((pw / ph).atan()).pow(2).clamp(max=4 / math.pi**2)
    with torch.no_grad():
        alpha = v / (1 - iou + v + 1e-9)
    return (1.0 - iou + rho + alpha * v).mean()


class ScratchLoss(nn.Module):
    def __init__(
        self, num_classes=2, obj_weight=1.0, cls_weight=1.0, box_weight=5.0, neg_sample=3.0
    ):
        super().__init__()
        self.num_classes = num_classes
        self.obj_weight = obj_weight
        self.cls_weight = cls_weight
        self.box_weight = box_weight
        self.neg_sample = neg_sample

    def forward(self, preds, targets):
        """preds: (B, C+5, G, G) raw logits. targets: list of (n, 5) tensors."""
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
            # box targets are cell-relative: tx/ty in [0, 1) within the cell,
            # plus raw w/h. Easier to learn than absolute image coords.
            tx = cx * G - gj
            ty = cy * G - gi
            box_t[b, gi, gj] = torch.stack([tx, ty, w, h], -1)
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
            box_loss = ciou_loss(box_raw[bi, gi, gj].sigmoid(), box_t[bi, gi, gj])
        else:
            cls_loss = torch.zeros((), device=device)
            box_loss = torch.zeros((), device=device)

        total = self.obj_weight * obj_loss + self.cls_weight * cls_loss + self.box_weight * box_loss
        return total, {"obj": obj_loss.item(), "cls": cls_loss.item(), "box": box_loss.item()}


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
    box = preds[:, C + 1 :].sigmoid().permute(0, 2, 3, 1)  # (B, G, G, 4)
    score = cls_prob * obj_prob.unsqueeze(-1)
    out = []
    for b in range(B):
        keep = score[b] > conf
        rows = []
        for c in range(C):
            yi, xj = keep[..., c].nonzero(as_tuple=True)
            for i, j in zip(yi.tolist(), xj.tolist()):
                # cells: i indexes y, j indexes x; box is (tx, ty, w, h)
                tx, ty, w, h = box[b, i, j].tolist()
                cx = (j + tx) / G
                cy = (i + ty) / G
                rows.append((c, float(score[b, i, j, c]), cx, cy, w, h))
        out.append(nms(rows))
    return out


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class YoloDataset(torch.utils.data.Dataset):
    """YOLO-format dataset tree (images/{train,val}, labels/{train,val})."""

    def __init__(self, root, split, imgsz=640, flip=True):
        self.img_dir = Path(root) / "images" / split
        self.lbl_dir = Path(root) / "labels" / split
        self.items = sorted(p.stem for p in self.img_dir.glob("*.png"))
        if not self.items:
            raise FileNotFoundError(f"no images under {self.img_dir}")
        self.imgsz = imgsz
        self.flip = flip

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
        if self.flip and random.random() < 0.5:
            lb = lb[:, ::-1].copy()
            rows = [[r[0], 1.0 - r[1], r[2], r[3], r[4]] for r in rows]
        x = torch.from_numpy(lb / 255.0).unsqueeze(0)  # (1, S, S)
        t = torch.tensor(
            transform_rows(rows, self.imgsz, scale, dx, dy, ow, oh), dtype=torch.float32
        ).reshape(-1, 5)
        meta = {"stem": stem, "scale": scale, "dx": dx, "dy": dy, "orig_w": ow, "orig_h": oh}
        return x, t, meta


def collate(batch):
    xs = torch.stack([b[0] for b in batch])
    ts = [b[1] for b in batch]
    meta = [b[2] for b in batch]
    return xs, ts, meta


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(model, loader, device, names, iou_thresh=0.5, conf=0.25):
    """IoU-matched precision/recall/F1 per class. Returns {name: {...}}."""
    model.eval()
    C = model.num_classes
    tp = [0] * C
    fp = [0] * C
    fn = [0] * C
    with torch.no_grad():
        for imgs, targets, _meta in loader:
            preds = model(imgs.to(device))
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
        }
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
    train_ds = YoloDataset(args.data, "train", imgsz=args.imgsz)
    val_ds = YoloDataset(args.data, "val", imgsz=args.imgsz, flip=False)
    train_ld = torch.utils.data.DataLoader(
        train_ds, batch_size=args.batch, shuffle=True, num_workers=args.workers, collate_fn=collate
    )
    val_ld = torch.utils.data.DataLoader(
        val_ds, batch_size=args.batch, shuffle=False, num_workers=args.workers, collate_fn=collate
    )
    model = ScratchNet(num_classes=max(2, len(names)) or 2, width=args.width, imgsz=args.imgsz).to(
        device
    )
    criterion = ScratchLoss(num_classes=model.num_classes)
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
    }
    history = []
    best_f1 = -1.0
    for epoch in range(args.epochs):
        model.train()
        tot = 0.0
        for imgs, targets, _meta in train_ld:
            opt.zero_grad()
            loss, parts = criterion(model(imgs.to(device)), targets)
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        metrics = evaluate(model, val_ld, device, names)
        mean_f1 = sum(m["f1"] for m in metrics.values()) / len(metrics) if metrics else 0.0
        history.append(
            {
                "epoch": epoch,
                "loss": round(tot / max(len(train_ld), 1), 4),
                "mean_f1": round(mean_f1, 4),
                **parts,
            }
        )
        if mean_f1 >= best_f1:
            best_f1 = mean_f1
            torch.save({"model": model.state_dict(), "config": cfg}, out_dir / "ckpt.pt")
        print(
            f"epoch {epoch}: loss {history[-1]['loss']} mean_f1 {history[-1]['mean_f1']}",
            flush=True,
        )
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "best_mean_f1": round(best_f1, 4),
                "epochs": args.epochs,
                "history": history,
                "final_per_class": metrics,
            },
            indent=1,
        )
    )
    print(f"saved -> {out_dir} (best_mean_f1 {best_f1:.4f})")
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="converted dataset dir (data.yaml)")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--width", type=int, default=32)
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
    args = ap.parse_args()
    if args.eval_only:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        names = load_names(args.data)
        ckpt = torch.load(args.ckpt, map_location=device, weights_only=False)
        cfg = ckpt["config"]
        model = ScratchNet(
            num_classes=cfg["num_classes"], width=cfg["width"], imgsz=cfg["imgsz"]
        ).to(device)
        model.load_state_dict(ckpt["model"])
        val_ld = torch.utils.data.DataLoader(
            YoloDataset(args.data, "val", imgsz=cfg["imgsz"], flip=False),
            batch_size=8,
            collate_fn=collate,
        )
        metrics = evaluate(model, val_ld, device, names)
        out_dir = Path(args.out or Path(args.ckpt).parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "eval_metrics.json").write_text(json.dumps(metrics, indent=1))
        print(json.dumps(metrics, indent=1))
        return
    train(args)


if __name__ == "__main__":
    main()
