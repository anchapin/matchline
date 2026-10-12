"""Tests for detector/degrade.py and detector/eval_robustness.py (issue #499).

Happy path: degradations are deterministic, geometry-preserving, and actually
change pixels; the greedy matcher scores perfect predictions at P=R=1.
Invariants: output size/mode always equal input; per-image seeds are
order-independent.
Defect injection: unknown variant raises; duplicate overlapping predictions
count once; empty predictions give zero recall without crashing.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
from PIL import Image

from detector.degrade import VARIANTS, degrade, variant_seed
from detector.eval_robustness import (
    evaluate,
    load_yolo_labels,
    main,
    match_predictions,
)


@pytest.fixture
def plan_img() -> Image.Image:
    """Small synthetic 'plan': dark linework on a light background."""
    rng = np.random.default_rng(0)
    arr = np.full((64, 96, 3), 255, dtype=np.uint8)
    arr[10:14, 5:90] = 0  # horizontal wall
    arr[30:60, 40:44] = 0  # vertical wall
    arr[rng.random((64, 96)) < 0.02] = 0  # speckle
    return Image.fromarray(arr, "RGB")


def _box(cls=0, x0=10, y0=10, x1=20, y1=20, conf=0.9):
    return {"cls": cls, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class TestDegrade:
    def test_variants_registry(self):
        assert set(VARIANTS) == {"clean", "scan_noise", "thickened_strokes"}

    def test_deterministic_same_seed(self, plan_img):
        a = np.asarray(degrade(plan_img, "scan_noise", seed=7))
        b = np.asarray(degrade(plan_img, "scan_noise", seed=7))
        assert np.array_equal(a, b)

    def test_seed_sensitive(self, plan_img):
        a = np.asarray(degrade(plan_img, "scan_noise", seed=7))
        b = np.asarray(degrade(plan_img, "scan_noise", seed=8))
        assert not np.array_equal(a, b)

    def test_variant_seed_order_independent(self):
        assert variant_seed(7, 3) == variant_seed(7, 3)
        assert variant_seed(7, 3) != variant_seed(7, 4)
        assert variant_seed(7, 3) != variant_seed(8, 3)

    @pytest.mark.parametrize("variant", ["scan_noise", "thickened_strokes"])
    def test_geometry_preserved(self, plan_img, variant):
        out = degrade(plan_img, variant, seed=7)
        assert out.size == plan_img.size
        assert out.mode == plan_img.mode

    @pytest.mark.parametrize("variant", ["scan_noise", "thickened_strokes"])
    def test_changes_pixels(self, plan_img, variant):
        out = np.asarray(degrade(plan_img, variant, seed=7)).astype(float)
        base = np.asarray(plan_img).astype(float)
        assert np.abs(out - base).mean() > 0

    def test_thickened_strokes_expands_dark(self, plan_img):
        out = np.asarray(degrade(plan_img, "thickened_strokes", seed=7))
        dark_before = (np.asarray(plan_img) < 128).sum()
        dark_after = (out < 128).sum()
        assert dark_after > dark_before

    def test_clean_returns_unmodified_copy(self, plan_img):
        out = degrade(plan_img, "clean", seed=7)
        assert np.array_equal(np.asarray(out), np.asarray(plan_img))
        assert out is not plan_img

    def test_unknown_variant_raises(self, plan_img):
        with pytest.raises(ValueError, match="unknown degradation variant"):
            degrade(plan_img, "melted", seed=7)


class TestMatch:
    def test_perfect_predictions(self):
        gt = [_box()]
        preds = [_box(conf=0.9)]
        s = match_predictions(gt, preds)[0]
        assert (s["tp"], s["fp"], s["fn"]) == (1, 0, 0)

    def test_empty_predictions(self):
        s = match_predictions([_box()], [])[0]
        assert (s["tp"], s["fp"], s["fn"], s["n_gt"]) == (0, 0, 1, 1)

    def test_duplicate_predictions_count_once(self):
        # Defect injection: two overlapping preds for one GT -> 1 tp + 1 fp.
        gt = [_box()]
        preds = [_box(conf=0.9), _box(conf=0.8)]
        s = match_predictions(gt, preds)[0]
        assert (s["tp"], s["fp"], s["fn"]) == (1, 1, 0)

    def test_below_iou_threshold_is_fp(self):
        gt = [_box(x0=0, y0=0, x1=10, y1=10)]
        preds = [_box(x0=50, y0=50, x1=60, y1=60)]
        s = match_predictions(gt, preds)[0]
        assert (s["tp"], s["fp"], s["fn"]) == (0, 1, 1)

    def test_per_class_isolation(self):
        gt = [_box(cls=0), _box(cls=1)]
        preds = [_box(cls=0)]
        s = match_predictions(gt, preds)
        assert (s[0]["tp"], s[0]["fn"]) == (1, 0)
        assert (s[1]["tp"], s[1]["fn"]) == (0, 1)


class TestEvaluateEndToEnd:
    def _make_dataset(self, root: str):
        os.makedirs(f"{root}/images/val")
        os.makedirs(f"{root}/labels/val")
        for stem in ("a", "b"):
            Image.new("RGB", (100, 80), (255, 255, 255)).save(f"{root}/images/val/{stem}.png")
            # one door box: cx=0.5 cy=0.5 w=0.2 h=0.25 -> px (40,30)-(60,50)
            with open(f"{root}/labels/val/{stem}.txt", "w") as f:
                f.write("0 0.5 0.5 0.2 0.25\n")

    def _write_preds(self, preds_dir: str):
        os.makedirs(preds_dir, exist_ok=True)
        for stem in ("a", "b"):
            for variant, pred in [
                ("clean", [_box(x0=40, y0=30, x1=60, y1=50)]),
                ("scan_noise", [_box(x0=40, y0=30, x1=60, y1=50)]),
                ("thickened_strokes", []),  # detector missed everything
            ]:
                with open(f"{preds_dir}/{stem}.{variant}.json", "w") as f:
                    json.dump(
                        {"image": stem, "width": 100, "height": 80, "preds": pred},
                        f,
                    )

    def test_load_yolo_labels(self, tmp_path):
        lp = tmp_path / "x.txt"
        lp.write_text("0 0.5 0.5 0.2 0.25\n")
        gt = load_yolo_labels(str(lp), 100, 80)
        assert gt[0]["cls"] == 0
        assert (gt[0]["x0"], gt[0]["y0"], gt[0]["x1"], gt[0]["y1"]) == (40, 30, 60, 50)

    def test_full_run_with_preds_dir(self, tmp_path, capsys):
        root = str(tmp_path / "ds")
        preds_dir = str(tmp_path / "preds")
        out = str(tmp_path / "report.json")
        self._make_dataset(root)
        self._write_preds(preds_dir)
        rc = main(
            [
                "--root",
                root,
                "--preds-dir",
                preds_dir,
                "--n",
                "2",
                "--seed",
                "7",
                "--out",
                out,
            ]
        )
        assert rc == 0
        with open(out) as f:
            report = json.load(f)
        assert report["config"]["n_images"] == 2
        assert set(report["variants"]) == set(VARIANTS)
        clean = report["variants"]["clean"]["0"]
        assert clean["p"] == pytest.approx(1.0)
        assert clean["r"] == pytest.approx(1.0)
        degraded = report["variants"]["thickened_strokes"]["0"]
        assert degraded["r"] == pytest.approx(0.0)
        delta = report["deltas_vs_clean"]["thickened_strokes"]["0"]
        assert delta["dr"] == pytest.approx(-1.0)
        assert "overall" in report["variants"]["clean"]

    def test_evaluate_function_returns_report(self, tmp_path):
        root = str(tmp_path / "ds")
        preds_dir = str(tmp_path / "preds")
        self._make_dataset(root)
        self._write_preds(preds_dir)

        class Args:
            pass

        args = Args()
        args.root, args.split, args.n = root, "val", 2
        args.seed, args.iou = 7, 0.5
        args.weights, args.preds_dir, args.dump_preds = None, preds_dir, None
        args.tile, args.overlap, args.conf, args.device = 1024, 0.2, 0.25, "cpu"
        report = evaluate(args)
        assert report["variants"]["clean"]["overall"]["n_gt"] == 2

    def test_requires_exactly_one_preds_source(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            main(["--root", str(tmp_path)])
