"""Solar-weighted roof aperture (#615)."""

import math

import pytest

from building_model import BuildingModel
from ifc_import import import_ifc
from roof_geometry import roof_plane
from solar_aperture import (
    compound_angle_deg,
    facet_aperture,
    model_aperture,
    orientation_bucket,
    solar_aperture,
    sun_vectors,
)
from tests.ifc_builder import IfcBuilder, box_plan

R30 = 3 * math.tan(math.radians(30))
SOUTH = [(0, 0, 3), (10, 0, 3), (10, -3, 3 + R30), (0, -3, 3 + R30)]  # canonical, y down
NORTH = [(0, -3, 3 + R30), (10, -3, 3 + R30), (10, -6, 3), (0, -6, 3)]


def _flat(area=1.0):
    return roof_plane("F", [(0, 0, 3), (area, 0, 3), (area, -1, 3), (0, -1, 3)])


def test_flat_plane_at_the_equator_matches_the_hand_sum():
    # equinox: cos(zenith) = cos(hour angle), hours 07..17 above the horizon
    day = 1 + 2 * sum(math.cos(math.radians(15 * k)) for k in range(1, 6))
    solstice = math.cos(math.radians(23.44)) * day  # equator: cos(dec) cos(w)
    ap = solar_aperture([_flat()], 0.0)
    assert ap.total == pytest.approx(2 * day + 2 * solstice, rel=1e-9)
    assert ap.n_sun_positions == 44
    assert ap.by_orientation["flat"] == pytest.approx(ap.total)


def test_aperture_scales_with_area():
    assert solar_aperture([_flat(4.0)], 40.0).total == pytest.approx(
        4 * solar_aperture([_flat(1.0)], 40.0).total, rel=1e-12
    )


@pytest.mark.parametrize("lat, sunny", [(40.0, "S"), (-40.0, "N")])
def test_equator_facing_roof_wins_in_each_hemisphere(lat, sunny):
    planes = [roof_plane("S", SOUTH), roof_plane("N", NORTH)]
    ap = solar_aperture(planes, lat)
    shady = "N" if sunny == "S" else "S"
    assert ap.by_orientation[sunny] > ap.by_orientation[shady] > 0
    assert ap.by_orientation["E"] == ap.by_orientation["W"] == 0


def test_east_and_west_roofs_match_by_symmetry():
    east = roof_plane("E", [(0, 0, 3 + R30), (3, 0, 3), (3, -10, 3), (0, -10, 3 + R30)])
    west = roof_plane("W", [(0, 0, 3), (3, 0, 3 + R30), (3, -10, 3 + R30), (0, -10, 3)])
    assert east.azimuth_deg == pytest.approx(90) and west.azimuth_deg == pytest.approx(270)
    ap = solar_aperture([east, west], 38.0)
    assert ap.by_orientation["E"] == pytest.approx(ap.by_orientation["W"], rel=1e-9)


def test_invariant_to_triangulation():
    a, b, c, d = SOUTH
    whole = facet_aperture(SOUTH, 40.0)
    tris = facet_aperture([a, b, c], 40.0) + facet_aperture([a, c, d], 40.0)
    assert tris == pytest.approx(whole, rel=1e-12)
    planes = solar_aperture([roof_plane("t1", [a, b, c]), roof_plane("t2", [a, c, d])], 40.0)
    assert planes.total == pytest.approx(
        solar_aperture([roof_plane("S", SOUTH)], 40.0).total, rel=1e-9
    )
    assert facet_aperture(SOUTH[::-1], 40.0) == pytest.approx(whole, rel=1e-12)


def test_plane_and_facet_forms_agree():
    assert solar_aperture([roof_plane("S", SOUTH)], 25.0).total == pytest.approx(
        facet_aperture(SOUTH, 25.0), rel=1e-6
    )


def test_no_latitude_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        solar_aperture([_flat()], None)
    with pytest.raises(ValueError):
        solar_aperture([_flat()], 91.0)
    with pytest.raises(ValueError):
        model_aperture(BuildingModel())


def test_model_latitude_used_and_caller_wins():
    m = BuildingModel(roof_planes=[roof_plane("S", SOUTH)], site_latitude_deg=40.0)
    assert model_aperture(m).latitude_deg == 40.0
    assert model_aperture(m, latitude_deg=-10.0).latitude_deg == -10.0


def test_sun_vectors_are_unit_and_above_the_horizon():
    for s in sun_vectors(51.5):
        assert s[2] > 0
        assert math.sqrt(sum(x * x for x in s)) == pytest.approx(1.0)


def test_noon_sun_is_due_south_in_the_north():
    noon = max(sun_vectors(40.0), key=lambda s: s[2])
    assert noon[0] == pytest.approx(0, abs=1e-12) and noon[1] > 0  # +y is south


def test_orientation_buckets():
    assert orientation_bucket(0.0, None) == "flat"
    assert orientation_bucket(0.2, 180.0) == "flat"
    assert [orientation_bucket(30, a) for a in (0, 44.9, 45, 135, 180, 225, 315, 359)] == [
        "N",
        "N",
        "E",
        "S",
        "S",
        "W",
        "N",
        "N",
    ]


def test_compound_angle():
    assert compound_angle_deg((39, 44, 24, 0)) == pytest.approx(39.74)
    assert compound_angle_deg((-105, -10, -12)) == pytest.approx(-105.17)
    assert compound_angle_deg(None) is None


def test_ifc_import_reads_site_latitude(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    b.f.by_type("IfcSite")[0].RefLatitude = (39, 44, 24, 0)
    m = import_ifc(str(b.write(tmp_path / "site.ifc")))
    assert m.site_latitude_deg == pytest.approx(39.74)


def test_ifc_import_without_latitude_leaves_none(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    m = import_ifc(str(b.write(tmp_path / "site.ifc")))
    assert m.site_latitude_deg is None
