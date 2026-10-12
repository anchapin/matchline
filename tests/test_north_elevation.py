"""North elevation fixture and mirrored no-grid registration (#702)."""

import numpy as np
import pytest

from link import build_model
from registration import Facade, register_elevation_geometric
from synth.multidiscipline import generate_building

SEED = 3


@pytest.fixture(scope="module")
def bldg_n():
    return generate_building(SEED, north_elevation=True)


def _north_links(model):
    out = {}
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.category == "window":
                assert op.host_facade == "north"
                out[op.id.removeprefix("north-")] = sp.number
    return out


def test_default_building_unchanged(bldg_n):
    base = generate_building(SEED)
    assert "elev_north" not in base["sheets"] and "north_windows" not in base
    assert base["south_windows"] == bldg_n["south_windows"]
    for key in ("arch", "mech", "lighting", "elev_grid", "elev_nogrid"):
        assert np.array_equal(base["sheets"][key]["image"], bldg_n["sheets"][key]["image"])


def test_north_fixture_has_windows(bldg_n):
    gt = bldg_n["gt_links"]["window_room_north"]
    assert gt and len(gt) == len(bldg_n["north_windows"])
    for key in ("elev_north", "elev_north_nogrid"):
        assert bldg_n["sheets"][key]["meta"]["facade"] == "north"


@pytest.mark.parametrize("key", ["elev_north", "elev_north_nogrid"])
def test_north_windows_link_to_gt_rooms(bldg_n, key):
    model, report = build_model(bldg_n, elevation_key=key)
    assert _north_links(model) == bldg_n["gt_links"]["window_room_north"]
    assert report.windows_unlinked == 0


@pytest.mark.parametrize(
    "name,mirrored,expect_s",
    [
        ("south", None, 2.0),
        ("west", None, 2.0),
        ("north", None, 8.0),
        ("east", None, 8.0),
        ("north", False, 2.0),
        ("south", True, 8.0),
    ],
)
def test_geometric_mirroring(name, mirrored, expect_s):
    f = Facade(name=name, ref_corner_m=(0.0, 0.0), length_m=10.0, fixed_coord_m=0.0)
    reg = register_elevation_geometric("s", f, 100.0, 10.0, 500.0, 1, mirrored=mirrored)
    s, _ = reg.to_facade(120.0, 0)  # 2 m right of the drawn wall's left end
    assert s == pytest.approx(expect_s)
