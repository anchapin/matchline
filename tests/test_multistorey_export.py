"""Multi-storey gbXML export (#639)."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon

from bem_export import validate_gbxml, write_gbxml
from bem_levels import hole_free
from building_model import BuildingModel, EnvelopeWall, Level, Space, SpaceOpening
from ifc_export import _bem_from_model

NS = {"g": "http://www.gbxml.org/schema"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _walls(prefix, sid, x0, y0, x1, y1):
    # canonical frame is y-down: y0 is the north edge
    return [
        EnvelopeWall(
            id=f"{prefix}-N", facade="north", from_m=[x0, y0], to_m=[x1, y0], space_id=sid
        ),
        EnvelopeWall(id=f"{prefix}-E", facade="east", from_m=[x1, y0], to_m=[x1, y1], space_id=sid),
        EnvelopeWall(
            id=f"{prefix}-S", facade="south", from_m=[x1, y1], to_m=[x0, y1], space_id=sid
        ),
        EnvelopeWall(id=f"{prefix}-W", facade="west", from_m=[x0, y1], to_m=[x0, y0], space_id=sid),
    ]


def _model(levels, spaces, walls=True):
    m = BuildingModel(name="t", auto_triage=False)
    m.levels = [
        Level(id=lid, name=f"Level {lid}", elevation_z_m=z, wall_height_m=3.0, above_ground=ag)
        for lid, z, ag in levels
    ]
    for sid, lid, box in spaces:
        m.spaces[sid] = Space(
            id=sid, level_id=lid, polygon_m=_rect(*box), area_m2=Polygon(_rect(*box)).area
        )
        if walls:
            m.envelope.extend(_walls(sid, sid, *box))
    return m


def _two_storey():
    return _model(
        [("L1", 0.0, None), ("L2", 3.0, None)],
        [("L1-101", "L1", (0, 0, 20, 10)), ("L2-201", "L2", (0, 0, 20, 10))],
    )


def _write(m, tmp_path, name="m.xml"):
    p = write_gbxml(_bem_from_model(m), tmp_path / name)
    ok, errs = validate_gbxml(p)
    assert ok, errs[:5]
    return p, ET.parse(p).getroot()


def _surfaces(root, stype):
    return [s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == stype]


def _zs(surface):
    return [
        float(c.findall("g:Coordinate", NS)[2].text)
        for c in surface.iter(f"{{{NS['g']}}}CartesianPoint")
    ]


def _adj(surface):
    return [a.get("spaceIdRef") for a in surface.findall("g:AdjacentSpaceId", NS)]


def _area(surface):
    pts = []
    for c in surface.find("g:PlanarGeometry/g:PolyLoop", NS).findall("g:CartesianPoint", NS):
        xs = [float(v.text) for v in c.findall("g:Coordinate", NS)]
        pts.append((xs[0], xs[1]))
    return abs(Polygon(pts).area), Polygon(pts).exterior.is_ccw


def test_two_storeys_each_space_on_its_storey_and_walls_at_its_height(tmp_path):
    _, root = _write(_two_storey(), tmp_path)
    storeys = root.findall(".//g:BuildingStorey", NS)
    assert [(s.get("id"), float(s.find("g:Level", NS).text)) for s in storeys] == [
        ("storey-L1", 0.0),
        ("storey-L2", 3.0),
    ]
    refs = {s.get("id"): s.get("buildingStoreyIdRef") for s in root.iter(f"{{{NS['g']}}}Space")}
    assert refs == {"L1-101": "storey-L1", "L2-201": "storey-L2"}
    walls = _surfaces(root, "ExteriorWall")
    assert len(walls) == 8
    for w in walls:
        lo, hi = (0.0, 3.0) if "-L1-" in w.get("id") else (3.0, 6.0)
        assert min(_zs(w)) == pytest.approx(lo) and max(_zs(w)) == pytest.approx(hi)
    vol = {
        s.get("id"): float(s.find("g:Volume", NS).text) for s in root.iter(f"{{{NS['g']}}}Space")
    }
    assert vol == {"L1-101": pytest.approx(600.0), "L2-201": pytest.approx(600.0)}


def test_interior_floor_lists_space_below_first_and_faces_up(tmp_path):
    _, root = _write(_two_storey(), tmp_path)
    (fl,) = _surfaces(root, "InteriorFloor")
    assert _adj(fl) == ["L1-101", "L2-201"]
    assert set(_zs(fl)) == {3.0}
    area, ccw = _area(fl)
    assert area == pytest.approx(200.0) and ccw
    (slab,) = _surfaces(root, "SlabOnGrade")
    assert _adj(slab) == ["L1-101"] and set(_zs(slab)) == {0.0} and not _area(slab)[1]
    (roof,) = _surfaces(root, "Roof")
    assert _adj(roof) == ["L2-201"] and set(_zs(roof)) == {6.0} and _area(roof)[1]


def test_setback_roof_and_floors_tile_each_plan(tmp_path):
    m = _model(
        [("L1", 0.0, None), ("L2", 3.0, None)],
        [("L1-101", "L1", (0, 0, 20, 10)), ("L2-201", "L2", (0, 0, 12, 10))],
    )
    _, root = _write(m, tmp_path)
    over_l1 = sum(
        _area(s)[0]
        for s in _surfaces(root, "InteriorFloor") + _surfaces(root, "Roof")
        if _adj(s)[0] == "L1-101"
    )
    assert over_l1 == pytest.approx(200.0)
    low_roofs = [s for s in _surfaces(root, "Roof") if set(_zs(s)) == {3.0}]
    assert [_area(s)[0] for s in low_roofs] == [pytest.approx(80.0)]


def test_stated_basement_writes_underground_types_without_inventing_u(tmp_path):
    m = _model(
        [("B1", -3.0, False), ("L1", 0.0, True)],
        [("B1-001", "B1", (0, 0, 20, 10)), ("L1-101", "L1", (0, 0, 20, 10))],
    )
    _, root = _write(m, tmp_path)
    ug = _surfaces(root, "UndergroundWall")
    assert len(ug) == 4 and {w.get("constructionIdRef") for w in ug} == {"const-ugwall"}
    assert all(min(_zs(w)) == pytest.approx(-3.0) for w in ug)
    (slab,) = _surfaces(root, "UndergroundSlab")
    assert set(_zs(slab)) == {-3.0} and _adj(slab) == ["B1-001"]
    assert len(_surfaces(root, "ExteriorWall")) == 4
    cons = {c.get("id"): c for c in root.findall("g:Construction", NS)}
    assert cons["const-ugwall"].find("g:U-value", NS) is None
    assert cons["const-ugslab"].find("g:U-value", NS) is None
    assert "const-raisedfloor" not in cons


def test_openings_stay_on_their_own_level(tmp_path):
    m = _two_storey()
    m.spaces["L2-201"].openings.append(
        SpaceOpening(
            id="w1", tag="A", category="window", width_m=1.2, height_m=1.5, host_facade="south"
        )
    )
    m.spaces["L2-201"].openings.append(
        SpaceOpening(id="s1", tag="SK", category="skylight", width_m=1.0, height_m=1.0)
    )
    _, root = _write(m, tmp_path)
    hosts = {}
    for s in root.iter(f"{{{NS['g']}}}Surface"):
        for op in s.findall("g:Opening", NS):
            hosts[op.get("openingType")] = (s, op)
    wall, win = hosts["FixedWindow"] if "FixedWindow" in hosts else hosts["OperableWindow"]
    assert "-L2-" in wall.get("id")
    zs = [
        float(c.findall("g:Coordinate", NS)[2].text)
        for c in win.find("g:PlanarGeometry", NS).iter(f"{{{NS['g']}}}CartesianPoint")
    ]
    assert min(zs) >= 3.0 and max(zs) <= 6.0
    roof, sky = hosts["FixedSkylight"]
    assert roof.get("surfaceType") == "Roof" and _adj(roof) == ["L2-201"]


def test_level_without_envelope_walls_uses_room_outline_over_wall_gaps(tmp_path):
    m = _model(
        [("L1", 0.0, None), ("L2", 3.0, None)],
        [("L1-101", "L1", (0, 0, 20, 10))],
    )
    m.spaces["L2-201"] = Space(
        id="L2-201", level_id="L2", polygon_m=_rect(0, 0, 9.9, 10), area_m2=99.0
    )
    m.spaces["L2-202"] = Space(
        id="L2-202", level_id="L2", polygon_m=_rect(10.1, 0, 20, 10), area_m2=99.0
    )
    bem = _bem_from_model(m)
    l2 = next(lv for lv in bem.levels if lv.id == "L2")
    assert len(l2.rings) == 1 and Polygon(l2.rings[0]).area == pytest.approx(200.0)
    assert any("room outlines" in n for n in bem.notes)
    _, root = _write(m, tmp_path)
    assert len([w for w in _surfaces(root, "ExteriorWall") if "-L2-" in w.get("id")]) == 4


def test_hole_free_split_tiles_a_courtyard_plan():
    ring = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(8, 8), (12, 8), (12, 12), (8, 12)]])
    parts = hole_free(ring)
    assert len(parts) >= 2 and all(not p.interiors for p in parts)
    assert sum(p.area for p in parts) == pytest.approx(ring.area)


def test_courtyard_level_gets_inward_facing_courtyard_walls(tmp_path):
    m = _model([("L1", 0.0, None), ("L2", 3.0, None)], [], walls=False)
    for lid in ("L1", "L2"):
        for k, box in enumerate([(0, 0, 20, 8), (0, 12, 20, 20), (0, 8, 8, 12), (12, 8, 20, 12)]):
            sid = f"{lid}-{k}"
            m.spaces[sid] = Space(
                id=sid, level_id=lid, polygon_m=_rect(*box), area_m2=Polygon(_rect(*box)).area
            )
    bem = _bem_from_model(m)
    l1 = bem.levels[0]
    assert len(l1.rings) == 2
    assert Polygon(l1.rings[0]).exterior.is_ccw and not Polygon(l1.rings[1]).exterior.is_ccw
    _, root = _write(m, tmp_path)
    # the four courtyard walls face into the courtyard
    az = sorted(
        float(w.find("g:RectangularGeometry/g:Azimuth", NS).text)
        for w in _surfaces(root, "ExteriorWall")
        if "-L1-" in w.get("id") and int(w.get("id")[-3:]) > 4
    )
    assert az == pytest.approx([0.0, 90.0, 180.0, 270.0])
    floors = _surfaces(root, "InteriorFloor")
    assert sum(_area(s)[0] for s in floors) == pytest.approx(400.0 - 16.0)


def test_single_storey_export_unchanged(tmp_path):
    m = _model([("L1", 0.0, None)], [("L1-101", "L1", (0, 0, 20, 10))])
    bem = _bem_from_model(m)
    assert bem.levels == [] and bem.horizontals == []
    _, root = _write(m, tmp_path)
    assert [s.get("id") for s in root.findall(".//g:BuildingStorey", NS)] == ["storey-1"]


def test_export_bytes_identical_across_hash_seeds(tmp_path):
    code = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from tests.test_multistorey_export import _model\n"
        "from ifc_export import _bem_from_model\nfrom bem_export import write_gbxml\n"
        "m = _model([('B1', -3.0, False), ('L1', 0.0, True), ('L2', 3.0, True)],"
        " [('B1-001', 'B1', (0, 0, 20, 10)), ('L1-101', 'L1', (0, 0, 20, 10)),"
        " ('L1-102', 'L1', (20, 0, 30, 10)), ('L2-201', 'L2', (0, 0, 12, 10))])\n"
        "write_gbxml(_bem_from_model(m), sys.argv[1])\n"
    ) % (ROOT, os.path.join(ROOT, "tests"))
    outs = []
    for seed in ("0", "1", "4242"):
        out = tmp_path / f"s{seed}.xml"
        subprocess.run(
            [sys.executable, "-c", code, str(out)],
            check=True,
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outs.append(out.read_bytes())
    assert outs[0] == outs[1] == outs[2]


def test_openstudio_reads_every_storey(tmp_path):
    openstudio = pytest.importorskip("openstudio")
    p, _ = _write(
        _model(
            [("B1", -3.0, False), ("L1", 0.0, True), ("L2", 3.0, True)],
            [
                ("B1-001", "B1", (0, 0, 20, 10)),
                ("L1-101", "L1", (0, 0, 20, 10)),
                ("L2-201", "L2", (0, 0, 20, 10)),
            ],
        ),
        tmp_path,
    )
    model = openstudio.gbxml.GbXMLReverseTranslator().loadModel(openstudio.path(str(p)))
    assert model.is_initialized()
    m = model.get()
    assert len(m.getBuildingStorys()) == 3 and len(m.getSpaces()) == 3
