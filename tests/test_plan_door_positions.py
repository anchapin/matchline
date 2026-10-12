"""Door symbols on the AEC-Bench plan path carry their plan position."""

from types import SimpleNamespace

from datasets_adapter import Region
from run_pipeline import _build_minimal_model_from_regions


def _takeoff(regions):
    return SimpleNamespace(regions=regions, spaces=[], unmatched_labels=[])


def test_door_regions_get_plan_center():
    regions = [
        Region("floor_area", [(0, 0), (10000, 0), (10000, 8000), (0, 8000)], "t"),
        Region("door", [(1000, 2000), (1900, 2000), (1900, 2200), (1000, 2200)], "t"),
    ]
    m = _build_minimal_model_from_regions(_takeoff(regions), "b")
    (d,) = [o for o in m.spaces["L1-1"].openings if o.tag == "D00"]
    assert d.plan_center_m == [1.45, 2.1]


def test_degenerate_door_region_has_no_position():
    regions = [
        Region("floor_area", [(0, 0), (10000, 0), (10000, 8000), (0, 8000)], "t"),
        Region("door", [(1000, 2000), (1900, 2000)], "t"),
    ]
    m = _build_minimal_model_from_regions(_takeoff(regions), "b")
    (d,) = [o for o in m.spaces["L1-1"].openings if o.tag == "D00"]
    assert d.plan_center_m is None


def test_wall_regions_are_not_door_openings():
    regions = [
        Region("floor_area", [(0, 0), (10000, 0), (10000, 8000), (0, 8000)], "t"),
        Region("wall", [(0, 0), (10000, 0), (10000, 200), (0, 200)], "t"),
        Region("wall", [(0, 0), (200, 0), (200, 8000), (0, 8000)], "t"),
        Region("door", [(1000, 2000), (1900, 2000), (1900, 2200), (1000, 2200)], "t"),
    ]
    m = _build_minimal_model_from_regions(_takeoff(regions), "b")
    doors = [o for o in m.spaces["L1-1"].openings if o.category == "door"]
    assert [o.tag for o in doors] == ["D00"]
