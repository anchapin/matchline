"""Tests for detector/train_scratch.py: from-scratch detector trainer (#743).

These need torch, so they live outside the main tests/ suite (same convention
as detector/tests/test_eca.py). Run with:

    python3 -m pytest detector/tests/test_train_scratch.py -q

Covers:
  - letterbox label transform + reverse roundtrip
  - NMS suppression
  - loss + decode wiring on a tiny overfit set (recall reaches 1.0)
  - train() artifact contract (ckpt.pt + metrics.json, nothing in repo)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

DETECTOR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DETECTOR))

from train_scratch import (  # noqa: E402
    ScratchLoss,
    ScratchNet,
    YoloDataset,
    collate,
    decode,
    evaluate,
    letterbox_params,
    nms,
    train,
    transform_rows,
    untransform_boxes,
)


def test_letterbox_transform_roundtrip():
    size, ow, oh = 640, 1000, 800
    scale, dx, dy = letterbox_params(ow, oh, size)
    rows = [(0, 0.15, 0.175, 0.10, 0.10), (1, 0.6, 0.6, 0.2, 0.2)]
    lb = transform_rows(rows, size, scale, dx, dy, ow, oh)
    # rebuild in decode-emission shape (cls, score, cx, cy, w, h)
    dets = [(c, 0.9, cx, cy, w, h) for c, cx, cy, w, h in lb]
    rev = untransform_boxes(dets, size, scale, dx, dy, ow, oh)
    for (c, cx, cy, w, h), (c2, s2, x0, y0, w2, h2) in zip(rows, rev):
        assert c == c2
        assert s2 == pytest.approx(0.9)
        assert cx - w / 2 == pytest.approx(x0, abs=1e-4)
        assert cy - h / 2 == pytest.approx(y0, abs=1e-4)
        assert w == pytest.approx(w2, abs=1e-4)
        assert h == pytest.approx(h2, abs=1e-4)


def test_untransform_keeps_score_with_its_box():
    # scores must travel with their own boxes, so callers never re-align two
    # parallel lists (#940 duck finding 2): two similar boxes, distinct scores
    size, ow, oh = 640, 640, 640
    dets = [  # (cls, score, cx, cy, w, h): similar boxes, distinct scores
        (1, 0.10, 0.10, 0.10, 0.20, 0.20),
        (1, 0.90, 0.10, 0.10, 0.20, 0.20),
    ]
    out = untransform_boxes(dets, size, 1.0, 0.0, 0.0, ow, oh)
    assert out[0] == pytest.approx((1, 0.10, 0.0, 0.0, 0.20, 0.20))
    assert out[1] == pytest.approx((1, 0.90, 0.0, 0.0, 0.20, 0.20))


def test_untransform_without_score_stays_5_tuple():
    # pre-#940 callers pass (cls, cx, cy, w, h); output shape matches input
    size, ow, oh = 640, 640, 640
    out = untransform_boxes([(0, 0.5, 0.5, 0.1, 0.1)], size, 1.0, 0.0, 0.0, ow, oh)
    assert out == [(0, 0.45, 0.45, 0.10, 0.10)]


def test_nms_suppresses_overlap_keeps_distinct():
    rows = [
        (0, 0.9, 0.5, 0.5, 0.1, 0.1),
        (0, 0.7, 0.51, 0.50, 0.1, 0.1),  # overlaps first heavily
        (1, 0.6, 0.2, 0.2, 0.1, 0.1),  # distinct, kept
    ]
    kept = nms(rows)
    assert len(kept) == 2
    assert kept[0][1] == pytest.approx(0.9)  # highest score first


def test_loss_and_decode_wire_up():
    torch.manual_seed(0)
    m = ScratchNet(num_classes=2, width=8, imgsz=128)
    crit = ScratchLoss(num_classes=2)
    x = torch.rand(2, 1, 128, 128)
    t = [torch.tensor([[0.0, 0.5, 0.5, 0.1, 0.1]]), torch.tensor([[1.0, 0.3, 0.6, 0.2, 0.1]])]
    loss, parts = crit(m(x), t)
    assert torch.isfinite(loss)
    assert set(parts) == {"obj", "cls", "box"}
    d = decode(m(x), num_classes=2, conf=0.0)
    assert len(d) == 2  # one row-set per image at zero confidence


def _make_tiny_dataset(tmp_path, n_train=4, n_val=2, img_px=64):
    """YOLO tree: black squares on white; labels as door (0) boxes."""
    root = tmp_path / "ds"
    for split, n in (("train", n_train), ("val", n_val)):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        for i in range(n):
            img = np.full((img_px, img_px), 255, dtype=np.uint8)
            x0, y0, bw, bh = 8 + 10 * i, 12, 20, 14
            img[y0 : y0 + bh, x0 : x0 + bw] = 0
            Image.fromarray(img, mode="L").save(root / "images" / split / f"sq_{split}_{i}.png")
            cx, cy = (x0 + bw / 2) / img_px, (y0 + bh / 2) / img_px
            (root / "labels" / split / f"sq_{split}_{i}.txt").write_text(
                f"0 {cx:.6f} {cy:.6f} {bw / img_px:.6f} {bh / img_px:.6f}\n"
            )
    (root / "data.yaml").write_text(
        "path: x\ntrain: images/train\nval: images/val\nnames:\n  0: door\n  1: window\n"
    )
    return root


def test_overfit_tiny_set_recall_one(tmp_path):
    """The wiring proof: a tiny net trained on 4 images detects every box."""
    root = _make_tiny_dataset(tmp_path)
    torch.manual_seed(0)
    np.random.seed(0)
    model = ScratchNet(num_classes=2, width=8, imgsz=128)
    crit = ScratchLoss(num_classes=2, neg_sample=10)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    ds = YoloDataset(root, "train", imgsz=128, flip=False)
    ld = torch.utils.data.DataLoader(ds, batch_size=4, collate_fn=collate)
    for _ in range(150):
        for imgs, targets, _meta in ld:
            opt.zero_grad()
            loss, _parts = crit(model(imgs), targets)
            loss.backward()
            opt.step()
    val = YoloDataset(root, "val", imgsz=128, flip=False)
    vld = torch.utils.data.DataLoader(val, batch_size=4, collate_fn=collate)
    metrics = evaluate(model, vld, "cpu", ["door", "window"])
    assert metrics["door"]["recall"] == 1.0
    # a tiny net fires near-duplicate boxes the greedy NMS keeps as FPs, so
    # precision is capacity-dependent; the wiring proof is recall + box quality
    from train_scratch import box_iou

    imgs, targets, _meta = next(iter(vld))
    dets = decode(model(imgs), num_classes=2, conf=0.5)[0]
    best = max(box_iou((d[0], d[2], d[3], d[4], d[5]), tuple(targets[0].tolist()[0])) for d in dets)
    assert best > 0.7  # width-8 net on 4 images: near-fit, not exact


def test_train_artifacts_contract(tmp_path):
    """train() writes ckpt.pt + metrics.json under a caller-set out dir."""
    root = _make_tiny_dataset(tmp_path)

    class A:
        data = str(root)
        epochs = 2
        imgsz = 128
        batch = 2
        width = 8
        lr = 2e-3
        workers = 0
        device = "cpu"
        seed = 0
        name = "t"
        out = str(tmp_path / "run")

    out_dir = Path(train(A))
    assert (out_dir / "ckpt.pt").exists()
    m = json.loads((out_dir / "metrics.json").read_text())
    assert {"best_mean_f1", "epochs", "history", "final_per_class"} <= set(m)
    ck = torch.load(out_dir / "ckpt.pt", weights_only=False)
    assert {"model", "config"} <= set(ck)
    assert ck["config"]["names"] == ["door", "window"]


def test_eval_only_roundtrip(tmp_path, monkeypatch):
    root = _make_tiny_dataset(tmp_path)
    out_dir = Path(train(_Args(str(root), str(tmp_path / "run2"))))
    # eval-only via main()
    import train_scratch

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_scratch.py",
            "--data",
            str(root),
            "--eval-only",
            "--out",
            str(out_dir),
            "--ckpt",
            str(out_dir / "ckpt.pt"),
        ],
    )
    train_scratch.main()
    assert (out_dir / "eval_metrics.json").exists()


class _Args:
    def __init__(self, data, out):
        self.data = data
        self.epochs = 1
        self.imgsz = 128
        self.batch = 2
        self.width = 8
        self.lr = 2e-3
        self.workers = 0
        self.device = "cpu"
        self.seed = 0
        self.name = "t"
        self.out = out
        self.eval_only = False
        self.ckpt = None
