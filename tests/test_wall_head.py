"""Wall-class auxiliary head (#1113): generator labels -> head training.

Covers:
  - gt.json walls section (kind + sheet-px geometry, #1113)
  - convert_synth .wallcls.txt emission (whole sheet + per-tile slicing)
  - train_scratch wall rasterization, head shapes, loss term, dataset mode
    (torch; skip in CI like the other scratch tests -- the GPU path is
    exercised on the runner by detector-train.yml)
  - wall_head.py aggregation helpers (segment cells -> majority class)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
DETECTOR = REPO / "detector"
sys.path.insert(0, str(DETECTOR))

from convert_synth import convert  # noqa: E402
from wall_head import majority_wall_class, segment_cells  # noqa: E402

from synth.sheets import save_sheet  # noqa: E402


def test_gt_walls_section(tmp_path):
    """Every wall line lands in gt with a kind and sheet-px geometry."""
    png_path, json_path = save_sheet(7, tmp_path)
    assert png_path.exists() and json_path.exists()
    gt = json.loads(json_path.read_text())
    walls = gt["walls"]
    assert len(walls) >= 4
    sheet_w, sheet_h = gt["sheet_size_px"]
    for w in walls:
        assert w["kind"] in {"exterior", "interior"}
        assert w["t_px"] > 0
        for a, b in ((w["a_px"], w["b_px"]),):
            assert len(a) == len(b) == 2
            assert 0 <= a[0] <= sheet_w and 0 <= a[1] <= sheet_h
            assert 0 <= b[0] <= sheet_w and 0 <= b[1] <= sheet_h
    # shared edges collapse: no two walls carry identical geometry
    segs = {(round(w["a_px"][0], 4), round(w["a_px"][1], 4), round(w["b_px"][0], 4), round(w["b_px"][1], 4)) for w in walls}
    assert len(segs) == len(walls)


def test_convert_wallcls_whole_sheet(tmp_path):
    """Whole-sheet mode: one wallcls row per gt wall, normalized, own file."""
    in_dir = tmp_path / "sheets"
    in_dir.mkdir()
    save_sheet(7, in_dir)
    gt = json.loads((in_dir / "sheet_007.gt.json").read_text())
    out = tmp_path / "ds"
    summary = convert(in_dir, out, val_fraction=0.0, include_empty=True)
    assert summary["wall_rows_written"] == len(gt["walls"])
    lbl = next((out / "labels" / "train").glob("*.wallcls.txt"))
    rows = [r.split() for r in lbl.read_text().splitlines() if r.strip()]
    assert len(rows) == len(gt["walls"])
    for r in rows:
        assert r[0] in {"0", "1"}
        assert all(0.0 <= float(v) <= 1.0 for v in r[1:5])


def test_convert_wallcls_tiles(tmp_path):
    """Tile mode: every tile carries its wallcls slice; walls repeat per tile."""
    in_dir = tmp_path / "sheets"
    in_dir.mkdir()
    save_sheet(7, in_dir)
    gt = json.loads((in_dir / "sheet_007.gt.json").read_text())
    out = tmp_path / "ds"
    convert(in_dir, out, val_fraction=0.0, include_empty=True, tile_size=640, stride=512)
    n_wall_rows = 0
    for wtxt in sorted((out / "labels" / "train").glob("*_t*x*.wallcls.txt")):
        stem = wtxt.name[: -len(".wallcls.txt")]
        txt = wtxt.parent / f"{stem}.txt"
        assert txt.exists(), f"missing {txt.name}"
        for r in wtxt.read_text().splitlines():
            if r.strip():
                parts = r.split()
                assert parts[0] in {"0", "1"}
                assert all(0.0 <= float(v) <= 1.0 for v in parts[1:5])
                n_wall_rows += 1
    # each wall is labelled in every tile it crosses, so the per-tile sum is
    # at least the wall count
    assert n_wall_rows >= len(gt["walls"])


def test_segment_cells_and_majority():
    grid = 8
    cells = segment_cells(0.0, 0.0, 1.0, 0.0, grid)
    assert cells[0] == (0, 0)
    assert cells[-1] == (0, grid - 1)
    assert len(cells) == grid
    diag = segment_cells(0.0, 0.0, 1.0, 1.0, grid)
    assert diag[0] == (0, 0) and diag[-1] == (grid - 1, grid - 1)
    m = np.zeros((grid, grid), dtype=int)
    assert majority_wall_class(cells, m) is None
    m[0, :] = 1  # map value 1 = partition
    assert majority_wall_class(cells, m) == 0
    m2 = np.zeros((grid, grid), dtype=int)
    m2[0, : grid // 2] = 1
    m2[0, grid // 2 :] = 2
    # split vote -> structural wins the tie
    assert majority_wall_class(cells, m2) == 1


def test_rasterize_wall_rows():
    pytest.importorskip("torch")
    from train_scratch import rasterize_wall_rows

    wall = rasterize_wall_rows([(1, 0.5, 0.5, 0.5, 0.025)], 640)
    assert wall.shape == (80, 80)
    assert wall[40, 40].item() == 2  # structural (cls 1) -> map value 2
    assert wall[37, 40].item() == 0  # outside the band
    assert wall[40, 0].item() == 0  # beyond the long axis
    wall_p = rasterize_wall_rows([(0, 0.5, 0.5, 0.5, 0.025)], 640)
    assert wall_p[40, 40].item() == 1
    # partition under structural: structural wins the shared band
    wall_both = rasterize_wall_rows([(0, 0.5, 0.5, 0.5, 0.025), (1, 0.5, 0.5, 0.5, 0.025)], 640)
    assert wall_both[40, 40].item() == 2


def test_scratchnet_wall_head_shapes():
    pytest.importorskip("torch")
    import torch
    from train_scratch import ScratchNet

    m = ScratchNet(num_classes=2, width=8, imgsz=128, wall_classes=2)
    x = torch.zeros(1, 1, 128, 128)
    det, wall = m(x)
    assert det.shape == (1, 7, 16, 16)  # C+5 detection head unchanged
    assert wall.shape == (1, 2, 16, 16)
    m0 = ScratchNet(num_classes=2, width=8, imgsz=128)
    assert m0.wall_classes == 0
    assert isinstance(m0(x), torch.Tensor)  # wall-off forward stays a tensor


def test_scratchloss_wall_term():
    pytest.importorskip("torch")
    import torch
    from train_scratch import ScratchLoss

    B, C, G, W = 1, 2, 8, 2
    torch.manual_seed(0)
    preds = torch.randn(B, C + 5, G, G)
    crit = ScratchLoss(num_classes=C)
    loss0, parts0 = crit(preds, [torch.zeros((0, 5))])
    assert "wall" not in parts0

    wall_preds = torch.full((B, W, G, G), -5.0)
    wall_preds[:, 0] = 5.0  # correct for partition (map value 1 -> head 0)
    wall_t = torch.ones(B, G, G, dtype=torch.long)
    loss1, parts1 = crit(preds, [torch.zeros((0, 5))], wall_preds, wall_t)
    assert parts1["wall"] == pytest.approx(0.0, abs=1e-3)
    assert loss1 > 0

    wall_preds[:, 0] = -5.0
    wall_preds[:, 1] = 5.0  # confidently wrong
    loss2, parts2 = crit(preds, [torch.zeros((0, 5))], wall_preds, wall_t)
    assert parts2["wall"] > 5.0
    assert loss2 > loss1

    parts3 = crit(preds, [torch.zeros((0, 5))], wall_preds, torch.zeros(B, G, G, dtype=torch.long))[
        1
    ]
    assert parts3["wall"] == 0.0  # no wall-labelled cells -> no wall term


def test_dataset_wall_mode(tmp_path):
    pytest.importorskip("torch")
    import torch
    from PIL import Image
    from train_scratch import YoloDataset, collate

    root = tmp_path / "ds"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    Image.new("L", (640, 640), 255).save(root / "images" / "train" / "sheet_001.png")
    (root / "labels" / "train" / "sheet_001.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    (root / "labels" / "train" / "sheet_001.wallcls.txt").write_text("1 0.5 0.5 0.9 0.05\n")
    Image.new("L", (640, 640), 255).save(root / "images" / "train" / "sheet_002.png")
    (root / "labels" / "train" / "sheet_002.txt").write_text("0 0.5 0.5 0.2 0.2\n")

    ds = YoloDataset(root, "train", imgsz=640, flip=False, load_wall=True)
    x, t, wall, meta = ds[0]
    assert x.shape == (1, 640, 640)
    assert t.shape[0] == 1
    assert wall.shape == (80, 80)
    assert wall[40, 40].item() == 2
    assert wall[37, 40].item() == 0
    # missing wallcls file degrades to an all-zero map (no supervised cells)
    _, _, wall2, _ = ds[1]
    assert int(wall2.sum()) == 0
    assert meta["stem"] == "sheet_001"

    ld = torch.utils.data.DataLoader(ds, batch_size=2, collate_fn=collate)
    xs, ts, ws, metas = next(iter(ld))
    assert ws.shape == (2, 80, 80)
    assert len(ts) == len(metas) == 2
