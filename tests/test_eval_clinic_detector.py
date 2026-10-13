"""Scoring glue of the Clinic detector eval (#743): raster + storey plumbing.

No IFC and no torch here: ``score_storeys`` runs on drawn-from-scratch
geometry with a fake provider, so CI collects it everywhere.
"""

from __future__ import annotations

import numpy as np
import pytest
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
