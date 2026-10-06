"""gbXML export of sloped roofs (#618, roadmap item 2 wave 6)."""

import math
import xml.etree.ElementTree as ET

import pytest

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from bem_geometry import BEMRoof
from bem_roof import open_edges, plane_z, shell_volume, space_shell
from validate.conservation import _check_bem_volume_conservation

NS = {"g": "http://www.gbxml.org/schema"}
H = 3.0
T30 = math.tan(math.radians(30))
ZR = H + 3 * T30  # ridge of a 30 degree roof over a 6 m span


def gable():
    return [
        BEMRoof("S", [(0, 0, H), (10, 0, H), (10, 3, ZR), (0, 3, ZR)], 30.0, 180.0),
        BEMRoof("N", [(0, 3, ZR), (10, 3, ZR), (10, 6, H), (0, 6, H)], 30.0, 0.0),
    ]


def hip():
    return [
        BEMRoof("S", [(0, 0, H), (10, 0, H), (7, 3, ZR), (3, 3, ZR)], 30.0, 180.0),
        BEMRoof("N", [(10, 6, H), (0, 6, H), (3, 3, ZR), (7, 3, ZR)], 30.0, 0.0),
        BEMRoof("E", [(10, 0, H), (10, 6, H), (7, 3, ZR)], 30.0, 90.0),
        BEMRoof("W", [(0, 6, H), (0, 0, H), (3, 3, ZR)], 30.0, 270.0),
    ]


def shed(tilt=10.0):
    zt = H + 6 * math.tan(math.radians(tilt))
    return [BEMRoof("R", [(0, 0, H), (10, 0, H), (10, 6, zt), (0, 6, zt)], tilt, 180.0)]


VOL = {
    "gable": 10 * (6 * H + 0.5 * 6 * 3 * T30),
    "hip": 10 * 6 * H + (6 * 3 * T30 / 6) * (2 * 10 + 4),
    "shed": 10 * 6 * (H + 0.5 * 6 * math.tan(math.radians(10))),
}
ROOFS = {"gable": gable, "hip": hip, "shed": shed}
BOX = [(0, 0), (10, 0), (10, 6), (0, 6)]


def bem(roofs, spaces=None, openings=None):
    spaces = spaces or [BEMSpace("SP1", "HALL 101", "101", BOX, 60.0, 180.0)]
    return BEMModel(
        building_name="Roof test",
        spaces=spaces,
        openings=openings or [],
        ring_m=BOX,
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
        roof_planes=roofs,
    )


@pytest.mark.parametrize("kind", ["gable", "hip", "shed"])
def test_shell_is_closed_outward_and_holds_the_analytic_volume(kind):
    loops, notes = space_shell(BOX, ROOFS[kind](), H)
    assert notes == []
    assert open_edges(loops) == []
    assert shell_volume(loops) == pytest.approx(VOL[kind], rel=1e-9)


def test_two_spaces_under_one_gable_each_close_and_sum_to_the_whole():
    west = [(0, 0), (4, 0), (4, 6), (0, 6)]
    east = [(4, 0), (10, 0), (10, 6), (4, 6)]
    total = 0.0
    for poly in (west, east):
        loops, _ = space_shell(poly, gable(), H)
        assert open_edges(loops) == []
        total += shell_volume(loops)
    assert total == pytest.approx(VOL["gable"], rel=1e-9)


def _write(tmp_path, model, name="out.xml"):
    path = tmp_path / name
    write_gbxml(model, path)
    return path, ET.parse(path).getroot()


def _pts(el):
    return [
        tuple(float(c.text) for c in cp.findall("g:Coordinate", NS))
        for cp in el.findall("g:CartesianPoint", NS)
    ]


@pytest.mark.parametrize("kind,n_roofs", [("gable", 2), ("hip", 4), ("shed", 1)])
def test_one_roof_surface_per_plane_with_tilt_and_azimuth(tmp_path, kind, n_roofs):
    path, root = _write(tmp_path, bem(ROOFS[kind]()))
    roofs = [s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == "Roof"]
    assert len(roofs) == n_roofs
    want = {(round(r.tilt_deg, 4), round(r.azimuth_deg, 4)) for r in ROOFS[kind]()}
    got = {
        (
            float(s.find("g:RectangularGeometry/g:Tilt", NS).text),
            float(s.find("g:RectangularGeometry/g:Azimuth", NS).text),
        )
        for s in roofs
    }
    assert got == want
    vol = float(root.find(".//g:Space/g:Volume", NS).text)
    assert vol == pytest.approx(VOL[kind], abs=1e-3)
    ok, errs = validate_gbxml(path)
    assert ok, errs


def test_gable_end_walls_get_peaked_tops_and_eave_walls_stay_rectangles(tmp_path):
    _, root = _write(tmp_path, bem(gable()))
    walls = [
        s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == "ExteriorWall"
    ]
    # every wall carries its PlanarGeometry outline (#627)
    loops = [_pts(w.find("g:PlanarGeometry/g:PolyLoop", NS)) for w in walls]
    peaked = [lp for lp in loops if len(lp) == 5]
    assert len(peaked) == 2  # east and west gable ends
    for pts in peaked:
        assert max(z for _, _, z in pts) == pytest.approx(ZR, abs=1e-4)
    eaves = [lp for lp in loops if len(lp) == 4]
    assert len(eaves) == 2  # eave walls: level top at H
    assert all(max(z for _, _, z in lp) == pytest.approx(H, abs=1e-4) for lp in eaves)


def test_skylight_sits_on_the_plane_above_it(tmp_path):
    sky = [BEMOpeningUnit(category="skylight", tag="SK-1", width_m=1.0, height_m=1.0)]
    path, root = _write(tmp_path, bem(gable(), openings=sky))
    ops = [o for o in root.iter(f"{{{NS['g']}}}Opening") if "Skylight" in o.get("openingType")]
    assert len(ops) == 1
    pts = _pts(ops[0].find("g:PlanarGeometry/g:PolyLoop", NS))
    ys = [y for _, y, _ in pts]
    assert max(ys) <= 3 + 1e-9 or min(ys) >= 3 - 1e-9  # wholly on one plane
    host = next(r for r in gable() if (sum(ys) / 4 < 3) == (r.id == "S"))
    for x, y, z in pts:
        assert H < z < ZR
        assert z == pytest.approx(plane_z(host, x, y), abs=1e-4)
    ok, errs = validate_gbxml(path)
    assert ok, errs


def test_no_roof_planes_and_flat_planes_export_byte_for_byte(tmp_path):
    flat = [BEMRoof("F", [(0, 0, H), (10, 0, H), (10, 6, H), (0, 6, H)], 0.0, None)]
    a, _ = _write(tmp_path, bem([]), "a.xml")
    b, _ = _write(tmp_path, bem(flat), "b.xml")
    assert a.read_bytes() == b.read_bytes()
    assert b"PlanarGeometry" in a.read_bytes() and b"roof-001" in a.read_bytes()


def test_volume_check_uses_the_shell_under_a_sloped_roof():
    good = bem(gable(), spaces=[BEMSpace("SP1", "HALL", "101", BOX, 60.0, VOL["gable"])])
    assert _check_bem_volume_conservation(good, 0.01).severity == "pass"
    stale = bem(gable())  # volume left at 60 x 3 = 180
    res = _check_bem_volume_conservation(stale, 0.01)
    assert res.severity == "error"
    assert "roof planes" in res.message


def test_bem_from_model_flips_roofs_north_up_and_sets_shell_volumes():
    from building_model import BuildingModel, Level, Space
    from ifc_export import _bem_from_model
    from roof_geometry import roof_plane

    m = BuildingModel(name="t")
    m.levels.append(Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=H))
    # canonical frame: y down the sheet, so the south eave is at y = 0 and
    # the north eave at y = -6
    m.spaces["SP1"] = Space(
        id="SP1",
        level_id="L1",
        name="HALL",
        number="101",
        polygon_m=[(0, 0), (10, 0), (10, -6), (0, -6)],
        area_m2=60.0,
    )
    m.roof_planes = [
        roof_plane("S", [(0, 0, H), (10, 0, H), (10, -3, ZR), (0, -3, ZR)], level_id="L1"),
        roof_plane("N", [(0, -3, ZR), (10, -3, ZR), (10, -6, H), (0, -6, H)], level_id="L1"),
    ]
    b = _bem_from_model(m)
    assert {r.id: round(r.azimuth_deg) for r in b.roof_planes} == {"S": 180, "N": 0}
    assert all(y >= 0 for r in b.roof_planes for _, y, _ in r.vertices)
    assert b.spaces[0].volume_m3 == pytest.approx(VOL["gable"], rel=1e-9)
