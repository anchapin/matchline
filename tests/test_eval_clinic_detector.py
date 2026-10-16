"""Scoring glue of the Clinic detector eval (#743): raster + storey plumbing.

No IFC and no torch here: ``score_storeys`` runs on drawn-from-scratch
geometry with a fake provider, so CI collects it everywhere.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Polygon

from datasets_adapter import Detection
from scripts.eval_clinic_detector import (
    M_PER_PT,
    PX_PER_M,
    raster_sheet,
    score_storeys,
    summarize,
)


def _poly_prim(pts):
    segs = [["M", [pts[0]]]] + [["L", [p]] for p in pts[1:]] + [["Z", []]]
    return {
        "kind": "polygon",
        "stroked": True,
        "filled": False,
        "dashed": False,
        "segments": segs,
    }


def test_raster_sheet_draws_walls():
    sheet = {
        "width_pt": 100,
        "height_pt": 100,
        "primitives": [_poly_prim([(10, 10), (90, 10), (90, 90), (10, 90)])],
    }
    img = np.asarray(raster_sheet(sheet, 1.0))
    assert img.shape == (100, 100)
    assert img[10, 50] == 0, "the stroked wall outline draws"
    assert img[50, 50] == 255, "the interior stays paper"


class FakeProvider:
    """One window box and one door box covering the whole sheet."""

    def __init__(self):
        self.seen = []

    def detect(self, image_path, sheet_id):
        self.seen.append(sheet_id)
        box = (0, 0, 10_000, 10_000)
        return [
            Detection(label="window", tag="", score=1.0, bbox=box, source="test"),
            Detection(label="door", tag="", score=1.0, bbox=box, source="test"),
        ]


@pytest.fixture
def loaded():
    ring = [(0, 0), (4, 0), (4, 3), (0, 3)]
    walls = {
        "First Floor": [Polygon(ring).buffer(0.1)],
        "Second Floor": [Polygon(ring).buffer(0.1)],
    }
    rooms = {st: [("101", "Office", ring)] for st in walls}
    windows = {
        "First Floor": [Polygon([(1, 2), (2, 2), (2, 2.2), (1, 2.2)])],
        "Second Floor": [],
    }
    doors = {
        "First Floor": [Polygon([(3, 1), (3.8, 1), (3.8, 1.2), (3, 1.2)])],
        "Second Floor": [Polygon([(1, 1), (1.8, 1), (1.8, 1.2), (1, 1.2)])],
    }
    return (walls, {}, rooms, {}, windows, doors)


def test_score_storeys_plumbing(loaded):
    prov = FakeProvider()
    out = score_storeys(loaded, prov)
    ff = out["First Floor"]
    assert ff["windows"]["recall"] == 1.0
    assert ff["windows"]["precision"] == 1.0
    assert ff["doors"]["recall"] == 1.0
    assert ff["doors"]["precision"] == 1.0
    # the second floor has no IFC windows, so its window recall is None there
    assert out["Second Floor"]["windows"]["recall"] is None
    assert out["Second Floor"]["doors"]["recall"] == 1.0
    # the provider saw each storey twice: once for windows, once for doors
    assert sorted(prov.seen) == [
        "First Floor",
        "First Floor",
        "Second Floor",
        "Second Floor",
    ]


def test_summarize_averages_the_storeys_that_have_truth(loaded):
    out = score_storeys(loaded, FakeProvider())
    s = summarize(out)
    assert s["windows"]["recall"] == pytest.approx(1.0)
    assert s["doors"]["recall"] == pytest.approx(1.0)


def test_px_per_pt_follows_px_per_m():
    assert PX_PER_M * M_PER_PT == pytest.approx(8.0 * 100 * 0.0254 / 72)


def test_tile_starts_cover_the_tail():
    from scripts.eval_clinic_detector import tile_starts

    assert tile_starts(1500, 640, 512) == [0, 512, 860], "the last tile covers the edge"
    assert tile_starts(600, 640, 512) == [0], "an axis smaller than the tile is one pass"
    assert tile_starts(1500, 0, 512) == [0], "tile_px 0 means the old whole-sheet pass"


def test_nms_dedupe_folds_overlap_keeps_best_score():
    from scripts.eval_clinic_detector import nms_dedupe

    dets = [
        {"label": "window", "score": 0.6, "bbox": (0, 0, 100, 100)},
        {"label": "window", "score": 0.9, "bbox": (5, 5, 105, 105)},
        {"label": "window", "score": 0.7, "bbox": (500, 500, 600, 600)},
        {"label": "door", "score": 0.5, "bbox": (0, 0, 100, 100)},
    ]
    keep = nms_dedupe(dets, iou=0.5)
    assert [d["score"] for d in keep if d["label"] == "window"] == [0.9, 0.7], (
        "the duplicate folds into the best-scoring box, the far one stays"
    )
    assert any(d["label"] == "door" for d in keep), "labels never suppress each other"


def test_detect_tiled_nms_iou_parameter():
    """The tiled pass folds overlapping detections at --nms-iou (sweep: 0.3)."""
    from datasets_adapter import Detection
    from scripts.eval_clinic_detector import detect_tiled, tile_starts

    class PairProvider:
        def detect(self, image_path, sheet_id):
            return [
                Detection(label="window", tag="", score=0.9, bbox=(0, 0, 100, 100), source="t"),
                Detection(label="window", tag="", score=0.6, bbox=(25, 25, 125, 125), source="t"),
            ]

    img = Image.new("L", (1300, 700), 255)
    xs, ys = tile_starts(1300, 640, 512), tile_starts(700, 640, 512)
    dets = detect_tiled(PairProvider(), img, "First Floor", tile_px=640, stride=512)
    assert len(dets) == len(xs) * len(ys), "IoU 0.39 folds at the 0.3 default"
    wide = detect_tiled(PairProvider(), img, "First Floor", tile_px=640, stride=512, nms_iou=0.5)
    assert len(wide) == 2 * len(xs) * len(ys), "IoU 0.5 keeps both"
    best = {(d["bbox"], d["score"]) for d in wide if d["bbox"] == (0, 0, 100, 100)}
    assert best == {((0, 0, 100, 100), 0.9)}, "folding keeps the best-scoring box"


def test_score_storeys_threads_nms_iou(loaded, monkeypatch):
    from scripts import eval_clinic_detector as ecd

    seen = []

    def spy(provider, img, sheet_id, tile_px, stride, nms_iou):
        seen.append((sheet_id, nms_iou))
        return []

    monkeypatch.setattr(ecd, "detect_tiled", spy)
    ecd.score_storeys(loaded, FakeProvider())
    ecd.score_storeys(loaded, FakeProvider(), nms_iou=0.5)
    assert all(n == 0.3 for _, n in seen[:4]), "default 0.3 reaches both passes"
    assert all(n == 0.5 for _, n in seen[4:]), "the override reaches both passes"


def test_detect_tiled_remaps_to_sheet_pixels():
    from datasets_adapter import Detection
    from scripts.eval_clinic_detector import detect_tiled, tile_starts

    class FixedProvider:
        def detect(self, image_path, sheet_id):
            return [
                Detection(label="window", tag="", score=0.8, bbox=(10, 20, 60, 70), source="test")
            ]

    img = Image.new("L", (1300, 700), 255)
    dets = detect_tiled(FixedProvider(), img, "First Floor", tile_px=640, stride=512)
    xs = tile_starts(1300, 640, 512)
    ys = tile_starts(700, 640, 512)
    assert len(dets) == len(xs) * len(ys), "one detection per tile"
    got = {d["bbox"] for d in dets}
    for y0 in ys:
        for x0 in xs:
            assert (10 + x0, 20 + y0, 60 + x0, 70 + y0) in got, "box at its own offset"


class SizingProvider:
    """Records each image's size, then claims the whole sheet."""

    def __init__(self):
        self.sizes = []

    def detect(self, image_path, sheet_id):
        self.sizes.append(Image.open(image_path).size)
        box = (0, 0, 10_000, 10_000)
        return [
            Detection(label="window", tag="", score=1.0, bbox=box, source="test"),
            Detection(label="door", tag="", score=1.0, bbox=box, source="test"),
        ]


def test_score_storeys_window_zoom_rasters_windows_larger(loaded):
    """--window-px-per-m rescales only the window pass; doors keep DOOR_PPM."""
    base = SizingProvider()
    score_storeys(loaded, base, px_per_m=50.0, tile_px=0)
    zoom = SizingProvider()
    score_storeys(loaded, zoom, px_per_m=50.0, tile_px=0, window_px_per_m=80.0)
    # the provider sees, per storey: the window png first, then the door png
    base_win, base_door = base.sizes[0], base.sizes[1]
    zoom_win, zoom_door = zoom.sizes[0], zoom.sizes[1]
    assert zoom_win[0] == pytest.approx(base_win[0] * 80.0 / 50.0, rel=0.01)
    assert zoom_door == base_door, "the door pass keeps its own scale"


def test_score_storeys_collects_raw_detections(loaded):
    raw = {}
    score_storeys(loaded, FakeProvider(), raw_out=raw)
    ff = raw["First Floor"]
    assert sorted(d["label"] for d in ff["windows"]) == ["door", "window"], (
        "the dump keeps every detection with its label"
    )
    assert all(d["score"] == 1.0 for d in ff["windows"])
    assert all(len(d["bbox"]) == 4 for d in ff["windows"])
    # the door sheet reports too, and keys exist even when nothing is found
    assert set(raw["Second Floor"]) == {"windows", "doors"}


def test_score_storeys_raw_out_leaves_scores_untouched(loaded):
    raw = {}
    out = score_storeys(loaded, FakeProvider(), raw_out=raw)
    assert out["First Floor"]["windows"]["recall"] == 1.0
    assert out["First Floor"]["doors"]["recall"] == 1.0
