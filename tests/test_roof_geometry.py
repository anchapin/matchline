"""RoofPlane and roof facet orientation (roadmap item 2, #613)."""

from __future__ import annotations

import math

import pytest

from building_model import BuildingModel, Provenance, RoofPlane
from roof_geometry import (
    RoofGeometryError,
    plan_area,
    plane_orientation,
    roof_plane,
    sloped_area,
)

# canonical frame: x east, y down the sheet (south), z up
RISE = 2.0  # ridge height above the eaves
H = 3.0  # eaves height


def _gable():
    """10 m (E-W) x 8 m (N-S) box, ridge along x at y = 4."""
    north = [[0, 0, H], [10, 0, H], [10, 4, H + RISE], [0, 4, H + RISE]]
    south = [[0, 4, H + RISE], [10, 4, H + RISE], [10, 8, H], [0, 8, H]]
    return north, south


def test_gable_faces_north_and_south():
    north, south = _gable()
    tn, an, _ = plane_orientation(north)
    ts, as_, _ = plane_orientation(south)
    want = math.degrees(math.atan2(RISE, 4.0))
    assert tn == pytest.approx(want) and ts == pytest.approx(want)
    assert an == pytest.approx(0.0, abs=1e-9)
    assert as_ == pytest.approx(180.0)


def test_hip_faces_all_four_ways():
    # 8 x 8 square hip, apex at the centre
    a = [4, 4, H + RISE]
    faces = {
        "N": [[0, 0, H], [8, 0, H], a],
        "E": [[8, 0, H], [8, 8, H], a],
        "S": [[8, 8, H], [0, 8, H], a],
        "W": [[0, 8, H], [0, 0, H], a],
    }
    want = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
    for k, v in faces.items():
        tilt, az, _ = plane_orientation(v)
        assert tilt == pytest.approx(math.degrees(math.atan2(RISE, 4.0)))
        assert az == pytest.approx(want[k], abs=1e-9), k


def test_shed_area_is_plan_over_cos_tilt():
    shed = [[0, 0, H + RISE], [6, 0, H + RISE], [6, 5, H], [0, 5, H]]  # falls to the south
    tilt, az, area = plane_orientation(shed)
    assert az == pytest.approx(180.0)
    assert plan_area(shed) == pytest.approx(30.0)
    assert area == pytest.approx(30.0 / math.cos(math.radians(tilt)))
    assert sloped_area(shed) == pytest.approx(area)


def test_flat_facet_has_no_azimuth():
    tilt, az, area = plane_orientation([[0, 0, H], [5, 0, H], [5, 4, H], [0, 4, H]])
    assert tilt == 0.0 and az is None and area == pytest.approx(20.0)


@pytest.mark.parametrize("reverse", [False, True])
def test_winding_and_closing_vertex_do_not_matter(reverse):
    north, _ = _gable()
    pts = list(reversed(north)) if reverse else list(north)
    ref = plane_orientation(north)
    got = plane_orientation(pts + [pts[0]])
    assert got[0] == pytest.approx(ref[0])
    assert got[1] == pytest.approx(ref[1], abs=1e-9)
    assert got[2] == pytest.approx(ref[2])


def test_mirrored_plan_swaps_east_and_west():
    east = [[8, 0, H], [8, 8, H], [4, 4, H + RISE]]
    mirrored = [[-x, y, z] for x, y, z in east]
    assert plane_orientation(east)[1] == pytest.approx(90.0)
    assert plane_orientation(mirrored)[1] == pytest.approx(270.0)


def test_non_planar_facet_is_rejected_not_fitted():
    warped = [[0, 0, H], [10, 0, H], [10, 4, H + RISE], [0, 4, H + RISE + 0.3]]
    with pytest.raises(RoofGeometryError, match="off one plane"):
        plane_orientation(warped)


@pytest.mark.parametrize(
    "bad",
    [[[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0], [1, 0], [1, 1]]],
)
def test_degenerate_facets_raise(bad):
    with pytest.raises(RoofGeometryError):
        plane_orientation(bad)


def test_roof_planes_round_trip_through_json():
    north, south = _gable()
    prov = Provenance(sheet_id="t", revision=1, method="test", confidence=1.0)
    m = BuildingModel(name="gable")
    m.roof_planes = [
        roof_plane("RF-N", north, level_id="L1", provenance=prov),
        roof_plane("RF-S", south, level_id="L1", host_global_id="abc"),
    ]
    back = BuildingModel.from_json(m.to_json())
    assert [type(p) for p in back.roof_planes] == [RoofPlane, RoofPlane]
    assert back.roof_planes == m.roof_planes
    assert isinstance(back.roof_planes[0].provenance, Provenance)


def test_models_without_roof_planes_load_as_flat():
    d = BuildingModel(name="old").to_dict()
    d["model"].pop("roof_planes")  # a file written before #613
    assert BuildingModel.from_dict(d).roof_planes == []
