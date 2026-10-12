"""Walk-out basements: walls and slabs typed by the site terrain (#641)."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from below_grade import boundary_types, wall_by_grade
from building_model import SpaceOpening
from grade import Terrain
from tests.test_multistorey_export import NS, _model, _surfaces, _write, _zs

# plane z = -0.3 * y (canonical y-down): grade 0 at the north edge (y=0),
# -3 at the south edge (y=10) of a 20 x 10 m footprint
TERRAIN = [
    [[-10.0, -10.0, 3.0], [30.0, -10.0, 3.0], [30.0, 20.0, -6.0]],
    [[-10.0, -10.0, 3.0], [30.0, 20.0, -6.0], [-10.0, 20.0, -6.0]],
]


def _walkout(terrain=TERRAIN):
    m = _model(
        [("B1", -3.0, False), ("L1", 0.0, True)],
        [("B1-001", "B1", (0, 0, 20, 10)), ("L1-101", "L1", (0, 0, 20, 10))],
    )
    m.terrain = terrain
    return m


def _pts(surface):
    out = []
    for c in surface.find("g:PlanarGeometry/g:PolyLoop", NS).findall("g:CartesianPoint", NS):
        out.append(tuple(float(v.text) for v in c.findall("g:Coordinate", NS)))
    return out


def _wall_area(surface):
    pts = _pts(surface)
    xs = {round(p[0], 6) for p in pts}
    if len(xs) == 1:  # wall in a constant-x plane: area in (y, z)
        return Polygon([(p[1], p[2]) for p in pts]).area
    return Polygon([(p[0], p[2]) for p in pts]).area


def test_terrain_height_and_profile():
    t = Terrain(TERRAIN)
    assert t.at(5.0, 5.0) == pytest.approx(-1.5)
    assert t.at(100.0, 0.0) is None
    prof = t.profile((0.0, 0.0), (0.0, 10.0))
    assert prof[0] == pytest.approx((0.0, 0.0)) and prof[-1] == pytest.approx((10.0, -3.0))
    assert t.profile((0.0, 0.0), (50.0, 0.0)) is None
    assert not Terrain([])


def test_wall_by_grade():
    assert wall_by_grade([(0, 0.0), (1, 0.0)], -3.0, 0.0) == "UndergroundWall"
    assert wall_by_grade([(0, -3.0), (1, -3.02)], -3.0, 0.0) == "ExteriorWall"
    assert wall_by_grade([(0, 0.0), (1, -3.0)], -3.0, 0.0) == "Split"


def test_boundary_types_follow_the_ground_per_wall():
    m = _walkout()
    bt = boundary_types(m, flag=False)
    assert bt.walls["B1-001-N"] == "UndergroundWall"
    assert bt.walls["B1-001-S"] == "ExteriorWall"
    assert bt.walls["B1-001-E"] == bt.walls["B1-001-W"] == "Split"
    assert sorted(bt.split_walls) == ["B1-001-E", "B1-001-W"]
    assert {bt.walls[f"L1-101-{f}"] for f in "NESW"} == {"ExteriorWall"}
    assert set(bt.horizontals.values()) >= {"SlabOnGrade"}
    assert "UndergroundSlab" not in bt.horizontals.values()
    assert bt.below_grade_levels == ["B1"] and bt.findings == []


def test_fully_buried_slab_stays_underground():
    deep = [[[x, y, z + 2.0] for x, y, z in tri] for tri in TERRAIN]  # grade 2 m higher
    bt = boundary_types(_walkout(deep), flag=False)
    assert "UndergroundSlab" in bt.horizontals.values()
    assert bt.walls["B1-001-S"] == "Split"


def test_gbxml_splits_side_walls_at_grade(tmp_path):
    _, root = _write(_walkout(), tmp_path)
    ug = _surfaces(root, "UndergroundWall")
    ext = _surfaces(root, "ExteriorWall")
    # north wall whole underground, east and west each one buried triangle
    assert sorted(round(_wall_area(s), 3) for s in ug) == [15.0, 15.0, 60.0]
    b1_ext = [s for s in ext if min(_zs(s)) == pytest.approx(-3.0)]
    assert sorted(round(_wall_area(s), 3) for s in b1_ext) == [15.0, 15.0, 60.0]
    assert len(ext) == 4 + 3  # level 1 untouched
    assert {s.get("constructionIdRef") for s in ug} == {"const-ugwall"}
    assert not _surfaces(root, "UndergroundSlab")
    (slab,) = _surfaces(root, "SlabOnGrade")[:1] or [None]
    assert slab is not None and set(_zs(slab)) == {-3.0}
    # split pieces cover the whole wall: buried + exposed = 10 m x 3 m each side
    sides = [s for s in ug + b1_ext if s.get("id").count("-") == 3]
    assert sum(_wall_area(s) for s in sides) == pytest.approx(60.0)


def test_window_below_grade_stays_on_an_exposed_part(tmp_path):
    m = _walkout()
    m.spaces["B1-001"].openings.append(
        SpaceOpening(
            id="w1", tag="BW", category="window", width_m=1.2, height_m=0.6, host_facade="north"
        )
    )
    p, root = _write(m, tmp_path)
    ops = list(root.iter(f"{{{NS['g']}}}Opening"))
    assert len(ops) == 1
    host = next(s for s in root.iter(f"{{{NS['g']}}}Surface") if ops[0] in list(s))
    assert host.get("surfaceType") == "ExteriorWall"
    assert _wall_area(host) == pytest.approx(1.2 * 0.6, abs=1e-3)
    north_ug = [
        s for s in _surfaces(root, "UndergroundWall") if s.get("id").startswith(host.get("id")[:-2])
    ]
    assert sum(_wall_area(s) for s in north_ug) == pytest.approx(60.0 - 0.72, abs=1e-3)


def test_terrain_that_misses_a_wall_keeps_the_storey_type_and_flags_it(tmp_path):
    small = [[[-1.0, -1.0, 0.0], [21.0, -1.0, 0.0], [21.0, 2.0, 0.0]]]
    m = _walkout(small)
    bt = boundary_types(m, flag=True)
    assert bt.walls["B1-001-S"] == "UndergroundWall"
    assert any("not covered by the site terrain" in f for f in bt.findings)
    assert any(r.kind == "below_grade" for r in m.review_queue)
    _write(m, tmp_path)


def test_no_terrain_exports_byte_identical(tmp_path):
    a = _model(
        [("B1", -3.0, False), ("L1", 0.0, True)],
        [("B1-001", "B1", (0, 0, 20, 10)), ("L1-101", "L1", (0, 0, 20, 10))],
    )
    b = _walkout([])
    pa, _ = _write(a, tmp_path, "a.xml")
    pb, _ = _write(b, tmp_path, "b.xml")
    assert pa.read_bytes() == pb.read_bytes()


def test_ifc_import_reads_terrain_element(tmp_path):
    import ifcopenshell.api.geometry as Gm
    import ifcopenshell.api.root as Root

    from ifc_import import import_ifc
    from tests.ifc_builder import IfcBuilder

    b = IfcBuilder()
    b.space("101", [(0, 0), (8, 0), (8, 10), (0, 10)], height=3.0, long_name="Room")
    ge = Root.create_entity(b.f, ifc_class="IfcGeographicElement", name="Terrain")
    ge.PredefinedType = "TERRAIN"
    ge.ObjectPlacement = b._placement((0, 0, 0), None)
    pts = b.f.create_entity(
        "IfcCartesianPointList3D",
        CoordList=(
            (-10.0, -10.0, 3.0),
            (30.0, -10.0, 3.0),
            (30.0, 20.0, -6.0),
            (-10.0, 20.0, -6.0),
        ),
    )
    fs = b.f.create_entity(
        "IfcTriangulatedFaceSet", Coordinates=pts, CoordIndex=((1, 2, 3), (1, 3, 4))
    )
    rep = b.f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=b.body,
        RepresentationIdentifier="Body",
        RepresentationType="Tessellation",
        Items=(fs,),
    )
    Gm.assign_representation(b.f, product=ge, representation=rep)
    model = import_ifc(b.write(tmp_path / "t.ifc"))
    t = Terrain(model.terrain)
    assert len(model.terrain) == 2
    # IFC (x, y-north) -> canonical (x, -y); the plane is z = 3 - 0.3 * (y + 10)
    assert t.at(5.0, -5.0) == pytest.approx(3.0 - 0.3 * 15.0, abs=1e-3)
