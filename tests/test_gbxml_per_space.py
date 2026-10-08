"""A gbXML export that simulates as exported (#765).

Before #765 every construction carried a U-value and no layers (EnergyPlus
stops at "Missing required property 'outside_layer'"), and the roof and slab
went whole to the largest space, so the other spaces imported with no floor.
These tests pin the fix: layered constructions that keep their U-value, one
roof and one slab per space when the spaces tile the footprint, walls cut at
space boundaries without splitting an opening, and interior walls between
spaces that share a boundary.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from bem_layers import FILM_R_SI, GYPSUM_13MM, layer_r_si
from bem_space_split import covers_footprint, keep_openings_whole, shared_walls, split_edge

NS = {"g": "http://www.gbxml.org/schema"}
RING = [(0.0, 0.0), (30.0, 0.0), (30.0, 12.0), (0.0, 12.0)]


def _rect(x0, x1, y0=0.0, y1=12.0):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _three(openings=()):
    spaces = [
        BEMSpace(
            f"sp-{k + 1}", f"ROOM {k + 1}", str(100 + k), _rect(10 * k, 10 * (k + 1)), 120.0, 360.0
        )
        for k in range(3)
    ]
    return BEMModel(
        building_name="Three rooms",
        spaces=spaces,
        openings=list(openings),
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )


def _loop(su):
    return [
        tuple(float(c.text) for c in cp)
        for cp in su.findall("g:PlanarGeometry/g:PolyLoop/g:CartesianPoint", NS)
    ]


def _area_xy(pts):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2.0)


def _write(tmp_path, model):
    path = write_gbxml(model, tmp_path / "m.xml")
    ok, errors = validate_gbxml(path)
    assert ok, errors
    return ET.parse(path).getroot()


@pytest.mark.parametrize("stype", ["ExteriorWall", "Roof", "SlabOnGrade"])
def test_layer_resistance_reproduces_the_stated_u_value(stype):
    for u in (0.2, 0.35, 0.5, 1.0):
        r = layer_r_si(u, stype)
        assert math.isclose(1.0 / (r + FILM_R_SI[stype]), u, rel_tol=1e-12)


def test_u_value_too_high_for_any_layer_gives_none():
    assert layer_r_si(1.0 / FILM_R_SI["ExteriorWall"], "ExteriorWall") is None


def test_every_opaque_construction_has_a_layer(tmp_path):
    root = _write(tmp_path, _three())
    mats = {m.get("id"): m for m in root.findall("g:Material", NS)}
    layers = {lay.get("id"): lay for lay in root.findall("g:Layer", NS)}
    for co in root.findall("g:Construction", NS):
        lid = co.find("g:LayerId", NS)
        assert lid is not None, co.get("id")
        for mid in layers[lid.get("layerIdRef")].findall("g:MaterialId", NS):
            assert mid.get("materialIdRef") in mats
        u = co.find("g:U-value", NS)
        if u is not None and co.get("id") in ("const-wall", "const-roof", "const-slab"):
            stype = {"const-wall": "ExteriorWall", "const-roof": "Roof"}.get(
                co.get("id"), "SlabOnGrade"
            )
            mat = mats[f"mat-{co.get('id')}"]
            r = float(mat.find("g:R-value", NS).text)
            assert math.isclose(1.0 / (r + FILM_R_SI[stype]), float(u.text), rel_tol=1e-3)


def test_partition_is_the_cited_gypsum_assembly():
    assert math.isclose(GYPSUM_13MM["thickness_m"], 0.0127)
    assert math.isclose(GYPSUM_13MM["conductivity_w_mk"], 0.16, rel_tol=1e-3)
    assert math.isclose(GYPSUM_13MM["density_kg_m3"], 800.0, rel_tol=1e-3)
    assert math.isclose(GYPSUM_13MM["specific_heat_j_kgk"], 1090.0, rel_tol=1e-3)


def test_each_space_gets_its_own_roof_and_slab(tmp_path):
    root = _write(tmp_path, _three())
    for stype in ("Roof", "SlabOnGrade"):
        surfs = root.findall(f".//g:Surface[@surfaceType='{stype}']", NS)
        owners = sorted(s.find("g:AdjacentSpaceId", NS).get("spaceIdRef") for s in surfs)
        assert owners == ["sp-1", "sp-2", "sp-3"]
        for s in surfs:
            assert math.isclose(_area_xy(_loop(s)), 120.0, rel_tol=1e-6)


def test_walls_are_cut_per_space_and_keep_total_area(tmp_path):
    root = _write(tmp_path, _three())
    walls = root.findall(".//g:Surface[@surfaceType='ExteriorWall']", NS)
    total = 0.0
    per_space = {}
    for w in walls:
        pts = _loop(w)
        length = math.dist(pts[0][:2], pts[1][:2])
        total += length * 3.0
        sid = w.find("g:AdjacentSpaceId", NS).get("spaceIdRef")
        per_space[sid] = per_space.get(sid, 0.0) + length
    assert math.isclose(total, 2 * (30.0 + 12.0) * 3.0, rel_tol=1e-9)
    # end rooms: 10 + 12 + 10 m of facade; the middle room: 10 + 10 m
    assert math.isclose(per_space["sp-1"], 32.0, rel_tol=1e-6)
    assert math.isclose(per_space["sp-2"], 20.0, rel_tol=1e-6)
    assert math.isclose(per_space["sp-3"], 32.0, rel_tol=1e-6)


def test_interior_walls_join_neighbours_and_face_the_second(tmp_path):
    root = _write(tmp_path, _three())
    iw = root.findall(".//g:Surface[@surfaceType='InteriorWall']", NS)
    pairs = sorted(
        tuple(a.get("spaceIdRef") for a in s.findall("g:AdjacentSpaceId", NS)) for s in iw
    )
    assert pairs == [("sp-1", "sp-2"), ("sp-2", "sp-3")]
    for s in iw:
        assert s.get("constructionIdRef") == "const-intwall"
        (x0, y0, _), (x1, y1, _) = _loop(s)[:2]
        L = math.hypot(x1 - x0, y1 - y0)
        assert math.isclose(L, 12.0, rel_tol=1e-6)
        # right of q0 -> q1 is the normal; it must point into the second space
        nx = (y1 - y0) / L
        second = s.findall("g:AdjacentSpaceId", NS)[1].get("spaceIdRef")
        assert nx > 0 and x0 == {"sp-2": 10.0, "sp-3": 20.0}[second]
    assert root.find("g:Construction[@id='const-intwall']", NS) is not None


def test_openings_are_never_split_between_spaces(tmp_path):
    wins = [BEMOpeningUnit("window", "W", 3.0, 1.5) for _ in range(6)]
    root = _write(tmp_path, _three(wins))
    ops = root.findall(".//g:Surface[@surfaceType='ExteriorWall']/g:Opening", NS)
    assert len(ops) == 6
    for w in root.findall(".//g:Surface[@surfaceType='ExteriorWall']", NS):
        pts = _loop(w)
        L = math.dist(pts[0][:2], pts[1][:2])
        for op in w.findall("g:Opening", NS):
            xs = [
                float(cp[0].text) for cp in op.findall("g:RectangularGeometry/g:CartesianPoint", NS)
            ]
            assert min(xs) >= -1e-9 and max(xs) <= L + 1e-9


def test_keep_openings_whole_moves_a_cut_to_the_nearer_jamb():
    a, b = object(), object()
    out = keep_openings_whole([(0.0, 10.0, a), (10.0, 20.0, b)], [(9.0, 12.0)])
    assert out == [(0.0, 9.0, a), (9.0, 20.0, b)]


def test_split_edge_keeps_one_piece_when_one_space_touches_it():
    m = _three()
    pieces = split_edge((30.0, 0.0), (30.0, 12.0), m.spaces, m.spaces[0])
    assert pieces == [(0.0, 12.0, m.spaces[0])]


def test_spaces_that_do_not_tile_keep_the_old_whole_roof(tmp_path):
    m = _three()
    m.spaces = m.spaces[:2]  # 2/3 of the footprint
    assert not covers_footprint(m.ring_m, m.spaces)
    root = _write(tmp_path, m)
    roofs = root.findall(".//g:Surface[@surfaceType='Roof']", NS)
    assert [r.get("id") for r in roofs] == ["roof-001"]
    assert any("do not tile the footprint" in n for n in m.notes)


def test_single_space_export_keeps_its_ids(tmp_path):
    m = _three()
    sp = BEMSpace("sp-1", "ALL", "100", list(RING), 360.0, 1080.0)
    m.spaces = [sp]
    root = _write(tmp_path, m)
    ids = [s.get("id") for s in root.findall(".//g:Surface", NS)]
    assert ids == ["wall-001", "wall-002", "wall-003", "wall-004", "roof-001", "floor-001"]
    assert shared_walls(m.spaces, m.ring_m) == []


def test_openstudio_import_gives_every_space_a_floor_and_layers(tmp_path):
    openstudio = pytest.importorskip("openstudio")
    path = write_gbxml(_three(), tmp_path / "os.xml")
    model = openstudio.gbxml.GbXMLReverseTranslator().loadModel(openstudio.path(str(path)))
    assert model.is_initialized()
    model = model.get()
    areas = sorted(round(sp.floorArea(), 3) for sp in model.getSpaces())
    assert areas == [120.0, 120.0, 120.0]
    assert all(c.numLayers() > 0 for c in model.getConstructions())
