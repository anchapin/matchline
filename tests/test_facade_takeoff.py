"""Unit tests for facade_takeoff.py. Fast synthetic masks only."""

import numpy as np
import pytest

from facade_takeoff import (
    dataset_priors,
    facade_takeoff,
    xml_agreement,
)


def _mask():
    # 100x100: 60% facade(2), 25% window(3), 5% blind(8), 5% door(4),
    # 3% shop(12), 2% background(1)
    m = np.full((100, 100), 2, np.int32)
    m[0:25, 0:100] = 3
    m[25:30, 0:100] = 8
    m[30:35, 0:100] = 4
    m[35:38, 0:100] = 12
    m[38:40, 0:100] = 1
    return m


def test_fractions_close_over_wall_plane():
    t = facade_takeoff(_mask(), "synth")
    # wall plane = 6000+2500+500+500+300 = 9800 px
    assert t.wwr == pytest.approx(3300 / 9800, abs=1e-9)
    assert t.frac_door == pytest.approx(500 / 9800, abs=1e-9)
    assert t.frac_opaque == pytest.approx(6000 / 9800, abs=1e-9)
    closure = t.frac_opaque + t.frac_glazing + t.frac_door
    assert closure == pytest.approx(1.0, abs=1e-12)


def test_glazing_split():
    t = facade_takeoff(_mask(), "synth")
    assert t.frac_blind_of_glazing == pytest.approx(0.05 / 0.33, abs=1e-9)
    assert t.frac_shop_of_glazing == pytest.approx(0.03 / 0.33, abs=1e-9)


def test_scale_free_has_no_absolute_areas():
    t = facade_takeoff(_mask(), "synth")
    assert t.px_per_m is None
    assert t.area_glazing_m2 is None
    assert t.to_envelope_dict()["scale_supplied"] is False


def test_supplied_width_gives_absolute_areas():
    m = _mask()
    t = facade_takeoff(m, "synth", width_m=50.0)
    # facade region bbox is the full 100px width (bg rows included in bbox)
    assert t.px_per_m == pytest.approx(100 / 50.0)
    # 1 px = 0.5 m -> 0.25 m^2/px; glazing = 3300 px
    assert t.area_glazing_m2 == pytest.approx(3300 * 0.25, abs=1e-9)
    assert t.area_wall_plane_m2 == pytest.approx(9800 * 0.25, abs=1e-9)


def test_scale_disagreement_warns():
    m = _mask()
    # height 100px mapped to 10m disagrees with width 100px -> 50m
    t = facade_takeoff(m, "synth", width_m=50.0, height_m=10.0)
    assert any("disagree" in w for w in t.scale_warnings)


def test_xml_agreement_perfect_on_aligned_boxes():
    m = _mask()
    H, W = m.shape
    boxes = [(3, 0.0, 0.0, 1.0, 0.25)]  # exactly the window band
    ag = xml_agreement(m, boxes)
    w = ag["per_class"][3]
    assert w["ratio_xml_over_mask"] == pytest.approx(1.0, abs=1e-9)
    assert w["iou"] == pytest.approx(1.0, abs=1e-9)
    assert w["coverage_mask_in_xml"] == pytest.approx(1.0, abs=1e-9)


def test_xml_boxes_coarse_but_countable():
    m = _mask()
    H, W = m.shape
    # one coarse box covering window+blind bands (loose annotation)
    boxes = [(3, 0.0, 0.0, 1.0, 0.35)]
    ag = xml_agreement(m, boxes)
    w = ag["per_class"][3]
    assert w["ratio_xml_over_mask"] > 1.0  # coarse -> overestimates
    assert w["coverage_mask_in_xml"] == pytest.approx(1.0, abs=1e-9)


def test_priors_stats():
    ts = [facade_takeoff(_mask(), f"s{i}") for i in range(4)]
    p = dataset_priors(ts)
    assert p["n_facades"] == 4
    assert p["wwr"]["mean"] == pytest.approx(ts[0].wwr, abs=1e-12)
    assert p["wwr"]["p10"] <= p["wwr"]["median"] <= p["wwr"]["p90"]


def test_provenance_carries_license():
    t = facade_takeoff(_mask(), "synth")
    assert "CC BY-SA" in t.provenance.note
    env = t.to_envelope_dict()
    assert "CC BY-SA" in env["provenance"]["note"]
