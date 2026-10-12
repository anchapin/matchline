"""Grade split in the single-storey gbXML writer and the IFC4 export (#649)."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from bem_ifc4 import validate_ifc4, write_ifc4
from bem_multistorey import _grade_pieces
from building_model import SpaceOpening
from grade import Terrain
from ifc_export import _bem_from_model
from tests.test_multistorey_export import NS, _model, _surfaces, _write
from tests.test_walkout_basement import TERRAIN, _walkout, _wall_area

# grade 3 m at the north edge, 0 at the south edge of a 20 x 10 m footprint
HIGH = [[[x, y, z + 3.0] for x, y, z in tri] for tri in TERRAIN]


def _single(terrain=HIGH, z=0.0):
    m = _model([("L1", z, None)], [("L1-101", "L1", (0, 0, 20, 10))])
    m.terrain = [[[x, y, zz + z] for x, y, zz in tri] for tri in terrain]
    return m


def test_single_storey_gbxml_splits_walls_at_grade(tmp_path):
    _, root = _write(_single(), tmp_path)
    ug = _surfaces(root, "UndergroundWall")
    ext = _surfaces(root, "ExteriorWall")
    assert sorted(round(_wall_area(s), 3) for s in ug) == [15.0, 15.0, 60.0]
    assert sorted(round(_wall_area(s), 3) for s in ext) == [15.0, 15.0, 60.0]
    assert {s.get("constructionIdRef") for s in ug} == {"const-ugwall"}
    ids = {c.get("id") for c in root.iter(f"{{{NS['g']}}}Construction")}
    assert "const-ugwall" in ids


def test_storey_elevation_shifts_the_terrain(tmp_path):
    # the same site 5 m up: the cut is the same
    _, root = _write(_single(z=5.0), tmp_path)
    assert sorted(round(_wall_area(s), 3) for s in _surfaces(root, "UndergroundWall")) == [
        15.0,
        15.0,
        60.0,
    ]


def test_single_storey_window_below_grade_on_exposed_part(tmp_path):
    m = _single()
    m.spaces["L1-101"].openings.append(
        SpaceOpening(
            id="w1", tag="BW", category="window", width_m=1.2, height_m=0.6, host_facade="north"
        )
    )
    _, root = _write(m, tmp_path)
    ops = list(root.iter(f"{{{NS['g']}}}Opening"))
    assert len(ops) == 1
    host = next(s for s in root.iter(f"{{{NS['g']}}}Surface") if ops[0] in list(s))
    assert host.get("surfaceType") == "ExteriorWall"
    assert _wall_area(host) == pytest.approx(0.72, abs=1e-3)


def test_single_storey_no_terrain_byte_identical(tmp_path):
    a = _model([("L1", 0.0, None)], [("L1-101", "L1", (0, 0, 20, 10))])
    b = _single([])
    pa, _ = _write(a, tmp_path, "a.xml")
    pb, _ = _write(b, tmp_path, "b.xml")
    assert pa.read_bytes() == pb.read_bytes()


def test_grade_pieces_follow_a_sloped_outline():
    # gable-ended wall 10 m long, 3 m eaves, 5 m ridge; grade flat at 4 m
    t = Terrain(
        [
            [[-5, -5, 4.0], [20, -5, 4.0], [20, 20, 4.0]],
            [[-5, -5, 4.0], [20, 20, 4.0], [-5, 20, 4.0]],
        ]
    )
    outline = Polygon([(0, 0), (10, 0), (10, 3), (5, 5), (0, 3)])
    pieces = _grade_pieces(t, (0.0, 0.0), (10.0, 0.0), 10.0, 0.0, 3.0, [], outline=outline)
    by = {k: sum(p.area for s, p in pieces if s == k) for k in ("UndergroundWall", "ExteriorWall")}
    assert by["UndergroundWall"] + by["ExteriorWall"] == pytest.approx(outline.area)
    assert by["ExteriorWall"] == pytest.approx(0.5 * 5.0 * 1.0)  # tip above 4 m: s 2.5..7.5


def _ifc_bounds(m, tmp_path):
    import ifcopenshell

    p = write_ifc4(_bem_from_model(m), tmp_path / "m.ifc")
    ok, errs = validate_ifc4(p)
    assert ok, errs[:5]
    f = ifcopenshell.open(str(p))
    out = []
    for b in f.by_type("IfcRelSpaceBoundary2ndLevel"):
        surf = b.ConnectionGeometry.SurfaceOnRelatingElement
        outer = Polygon([pt.Coordinates for pt in surf.OuterBoundary.Points])
        holes = [Polygon([pt.Coordinates for pt in r.Points]).area for r in surf.InnerBoundaries]
        out.append(
            (b.InternalOrExternalBoundary, b.RelatedBuildingElement.Name, outer.area - sum(holes))
        )
    return f, out


def test_ifc4_single_storey_grade_boundaries(tmp_path):
    f, bounds = _ifc_bounds(_single(), tmp_path)
    assert len(f.by_type("IfcWall")) == 4  # walls stay whole
    earth = sorted(round(a, 3) for k, _, a in bounds if k == "EXTERNAL_EARTH")
    ext = sorted(round(a, 3) for k, _, a in bounds if k == "EXTERNAL")
    assert earth == [15.0, 15.0, 60.0]
    assert ext == [15.0, 15.0, 60.0]
    assert all(
        b.PhysicalOrVirtualBoundary == "PHYSICAL" for b in f.by_type("IfcRelSpaceBoundary2ndLevel")
    )


def test_ifc4_window_is_a_hole_in_the_exposed_boundary(tmp_path):
    m = _single()
    m.spaces["L1-101"].openings.append(
        SpaceOpening(
            id="w1", tag="SW", category="window", width_m=2.0, height_m=1.5, host_facade="south"
        )
    )
    _, bounds = _ifc_bounds(m, tmp_path)
    ext = sorted(round(a, 3) for k, _, a in bounds if k == "EXTERNAL")
    assert ext == [15.0, 15.0, 57.0]


def test_ifc4_multistorey_walkout_boundaries_only_where_terrain_covers(tmp_path):
    _, bounds = _ifc_bounds(_walkout(), tmp_path)
    earth = sorted(round(a, 3) for k, _, a in bounds if k == "EXTERNAL_EARTH")
    assert earth == [15.0, 15.0, 60.0]
    # level 1 sits above grade: whole-wall EXTERNAL boundaries
    l1 = [a for k, n, a in bounds if n.startswith("Wall-L1-") and k == "EXTERNAL"]
    assert sorted(round(a, 3) for a in l1) == [30.0, 30.0, 60.0, 60.0]


def test_ifc4_no_terrain_writes_no_boundaries(tmp_path):
    f, bounds = _ifc_bounds(
        _model([("L1", 0.0, None)], [("L1-101", "L1", (0, 0, 20, 10))]), tmp_path
    )
    assert bounds == []
