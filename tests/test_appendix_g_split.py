"""Rooms split at Appendix G block lines, pieces joined by air walls (#638)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon

from bem_export import validate_gbxml, write_gbxml
from bem_geometry import BEMModel, BEMOpeningUnit, BEMSpace
from thermal_zoning import PERIMETER_DEPTH_M, appendix_g_split, perimeter_core, split_spaces

D = PERIMETER_DEPTH_M
NS = {"g": "http://www.gbxml.org/schema"}
H = 3.0


def _space(sid, ring, lighting=0.0, u=None, ident=None):
    a = Polygon(ring).area
    return BEMSpace(
        sid=sid,
        name=f"ROOM {sid}",
        number=sid,
        polygon_m=list(ring),
        area_m2=a,
        volume_m3=a * H,
        lighting_w=lighting,
        wall_u_value_w_m2k=u,
        identity=ident,
    )


def _model(spaces, ring=((0, 0), (30, 0), (30, 20), (0, 20)), openings=(), facades=None):
    return BEMModel(
        building_name="T",
        spaces=list(spaces),
        openings=list(openings),
        ring_m=list(ring),
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=0.5,
        ring_facades=list(facades or []),
    )


PLATE = [(0, 0), (30, 0), (30, 20), (0, 20)]


def test_whole_plate_room_splits_into_one_piece_per_block():
    blocks = perimeter_core(PLATE)
    sp = _space("R1", PLATE, lighting=600.0, u=0.35)
    res = split_spaces(blocks, [sp])
    assert sorted(s.sid for s in res.spaces) == sorted(f"R1-{b.id}" for b in blocks)
    assert sum(s.area_m2 for s in res.spaces) == pytest.approx(sp.area_m2, rel=1e-9)
    assert sum(s.volume_m3 for s in res.spaces) == pytest.approx(sp.volume_m3, rel=1e-9)
    assert sum(s.lighting_w for s in res.spaces) == pytest.approx(600.0, rel=1e-9)
    for s in res.spaces:
        assert s.split_from == "R1"
        assert s.wall_u_value_w_m2k == 0.35  # intensive: copied, not apportioned
        assert s.name == "ROOM R1" and s.number == "R1"
        assert Polygon(s.polygon_m).area == pytest.approx(s.area_m2, rel=1e-9)
    # one zone per block, each holding exactly its piece
    assert res.zones == [(b.id, [f"R1-{b.id}"]) for b in blocks]


def test_air_walls_join_only_pieces_of_the_same_room_and_cover_every_block_line():
    blocks = perimeter_core(PLATE)
    res = split_spaces(blocks, [_space("R1", PLATE)])
    pairs = {frozenset(a.space_ids) for a in res.air_walls}
    core = "R1-core"
    for o in ("north", "east", "south", "west"):
        assert frozenset({core, f"R1-perimeter-{o}"}) in pairs
    # corner mitres between neighbouring perimeter pieces
    assert frozenset({"R1-perimeter-south", "R1-perimeter-east"}) in pairs
    # total air-wall length = core perimeter + four 45-degree mitres
    core_len = 2 * ((30 - 2 * D) + (20 - 2 * D))
    mitres = 4 * D * 2**0.5
    assert sum(a.length_m for a in res.air_walls) == pytest.approx(core_len + mitres, rel=1e-6)
    assert len({a.id for a in res.air_walls}) == len(res.air_walls)


def test_air_wall_normal_points_from_first_space_into_second():
    blocks = perimeter_core(PLATE)
    res = split_spaces(blocks, [_space("R1", PLATE)])
    polys = {s.sid: Polygon(s.polygon_m) for s in res.spaces}
    from shapely.geometry import Point

    for a in res.air_walls:
        (x0, y0), (x1, y1) = a.p0, a.p1
        L = a.length_m
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = (y1 - y0) / L, -(x1 - x0) / L  # right of p0 -> p1
        assert polys[a.space_ids[1]].contains(Point(mx + 0.01 * rx, my + 0.01 * ry))
        assert polys[a.space_ids[0]].contains(Point(mx - 0.01 * rx, my - 0.01 * ry))


def test_room_inside_one_block_is_unchanged():
    blocks = perimeter_core(PLATE)
    sp = _space("R2", [(10, 0), (14, 0), (14, 3), (10, 3)])
    res = split_spaces(blocks, [sp])
    assert res.spaces == [sp] and res.spaces[0] is sp
    assert res.air_walls == [] and res.split_from == {}
    assert res.zones == [("perimeter-south", ["R2"])]


def test_room_reaching_outside_the_plate_is_left_whole_with_a_note():
    blocks = perimeter_core(PLATE)
    sp = _space("R3", [(10, -2), (14, -2), (14, 8), (10, 8)])
    res = split_spaces(blocks, [sp])
    assert res.spaces == [sp] and res.air_walls == []
    assert any("R3: not split" in n for n in res.notes)


def test_split_ids_and_geometry_are_stable_across_runs():
    blocks = perimeter_core(PLATE)
    a = split_spaces(blocks, [_space("R1", PLATE)])
    b = split_spaces(perimeter_core(PLATE), [_space("R1", PLATE)])
    assert [(s.sid, s.polygon_m) for s in a.spaces] == [(s.sid, s.polygon_m) for s in b.spaces]
    assert [(w.id, w.space_ids, w.p0, w.p1) for w in a.air_walls] == [
        (w.id, w.space_ids, w.p0, w.p1) for w in b.air_walls
    ]


def test_identity_follows_the_piece_and_names_its_parent():
    blocks = perimeter_core(PLATE)
    ident = {"id": "L1-101", "method": "ifc", "confidence": 1.0}
    res = split_spaces(blocks, [_space("R1", PLATE, ident=ident)])
    core = next(s for s in res.spaces if s.sid == "R1-core")
    assert core.identity["id"] == "L1-101-core"
    assert core.identity["split_from"] == "L1-101"
    assert ident == {"id": "L1-101", "method": "ifc", "confidence": 1.0}  # parent untouched


def test_wall_opening_moves_to_the_piece_on_its_facade():
    win = BEMOpeningUnit("window", "A", 1.2, 1.5, space_sid="R1", host_facade="south")
    sky = BEMOpeningUnit("skylight", "S", 1.0, 1.0, space_sid="R1")
    m = _model(
        [_space("R1", PLATE)], openings=[win, sky], facades=["south", "east", "north", "west"]
    )
    new, res = appendix_g_split(m)
    assert new.openings[0].space_sid == "R1-perimeter-south"
    assert new.openings[1].space_sid == "R1-core"  # largest piece keeps the skylight
    assert m.openings[0].space_sid == "R1"  # input model untouched
    assert m.thermal_zones == [] and m.air_walls == []


def _write(model, tmp_path, name="m.xml"):
    p = write_gbxml(model, tmp_path / name)
    return p, ET.parse(p).getroot()


def test_gbxml_carries_zones_and_air_walls_and_validates(tmp_path):
    new, res = appendix_g_split(_model([_space("R1", PLATE)]))
    p, root = _write(new, tmp_path)
    ok, errs = validate_gbxml(p)
    assert ok, errs
    zones = {z.get("id") for z in root.findall("g:Zone", NS)}
    assert zones == {f"zone-{zid}" for zid, _ in res.zones}
    for se in root.iter(f"{{{NS['g']}}}Space"):
        assert se.get("zoneIdRef") == f"zone-{se.get('id')[len('R1-') :]}"
    air = [s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == "Air"]
    assert len(air) == len(res.air_walls)
    for s in air:
        assert s.get("constructionIdRef") == "const-air"
        assert len(s.findall("g:AdjacentSpaceId", NS)) == 2
        assert len(s.findall("g:PlanarGeometry/g:PolyLoop/g:CartesianPoint", NS)) == 4
    assert root.find("g:Construction[@id='const-air']", NS) is not None


def test_gbxml_without_split_is_unchanged(tmp_path):
    m = _model([_space("R2", [(10, 0), (14, 0), (14, 3), (10, 3)])], ring=PLATE)
    p1, _ = _write(m, tmp_path, "a.xml")
    p2, root = _write(
        _model([_space("R2", [(10, 0), (14, 0), (14, 3), (10, 3)])]), tmp_path, "b.xml"
    )
    assert p1.read_bytes() == p2.read_bytes()
    assert root.find("g:Construction[@id='const-air']", NS) is None
    assert [z.get("id") for z in root.findall("g:Zone", NS)] == ["zone-1"]


def test_ifc_writes_air_walls_as_virtual_space_boundaries(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from bem_ifc4 import validate_ifc4, write_ifc4

    ident = {"id": "L1-101", "method": "ifc", "confidence": 1.0}
    new, res = appendix_g_split(_model([_space("R1", PLATE, ident=ident)]))
    p = write_ifc4(new, tmp_path / "m.ifc")
    ok, errs = validate_ifc4(p)
    assert ok, errs
    f = ifcopenshell.open(str(p))
    assert len(f.by_type("IfcVirtualElement")) == len(res.air_walls)
    rels = [r for r in f.by_type("IfcRelSpaceBoundary") if r.PhysicalOrVirtualBoundary == "VIRTUAL"]
    assert len(rels) == 2 * len(res.air_walls)
    blocks = {z.Name for z in f.by_type("IfcZone") if z.ObjectType}
    assert blocks == {zid for zid, _ in res.zones}
    split = set()
    for sp in f.by_type("IfcSpace"):
        for rel in sp.IsDefinedBy or []:
            pd = getattr(rel, "RelatingPropertyDefinition", None)
            if pd is not None and pd.Name == "Matchline_Identity":
                for prop in pd.HasProperties:
                    if prop.Name == "SplitFrom":
                        split.add(prop.NominalValue.wrappedValue)
    assert split == {"L1-101"}
