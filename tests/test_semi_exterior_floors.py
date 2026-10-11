"""Semi-exterior floors take the Table 5.5 Semiheated floor construction (#900).

A floor/ceiling between a conditioned space and a semiheated or unconditioned
one is semi-exterior envelope (#747, #900). ASHRAE 90.1-2019 5.5.2: "If a
building contains any semiheated space or unconditioned space then the
semiexterior building envelope shall comply with the requirements for
semiheated space in Tables 5.5-0 through 5.5-8 for the appropriate climate"
(Alex, 747#issuecomment-6101561224), so the settled record takes the
Semiheated column's ExteriorFloor row.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon

from bem_export import validate_gbxml, write_gbxml
from bem_layers import FILM_R_SI
from building_model import BuildingModel, Construction, EnvelopeWall, Level, Space
from construction_library import (
    DEFAULT_SEMIEXT_FLOOR_ID,
    DEFAULT_SEMIEXT_WALL_ID,
    LibrarySummary,
    _default_semi_exterior_floor,
    apply_construction_library,
)
from construction_library_data import CONSTRUCTION_LIBRARY
from constructions import semi_exterior_floors, semi_exterior_walls
from ifc_export import _bem_from_model

NS = {"g": "http://www.gbxml.org/schema"}
SEMI_4 = CONSTRUCTION_LIBRARY[("Semiheated", "4", "ExteriorFloor", "SteelFramed")]


def _rec(a, b, kind="semi_exterior", **kw):
    return {
        "spaces": sorted([a, b]),
        "categories": ["conditioned", "unconditioned"],
        "kind": kind,
        "reason": "conditioned against unconditioned",
        "plane": "floor",
        "area_m2": 200.0,
        **kw,
    }


def _model(*recs) -> BuildingModel:
    m = BuildingModel(name="t")
    for sid in ("S1", "S2", "S3"):
        m.spaces[sid] = Space(id=sid, level_id="L1", name="Room")
    m.envelope.append(
        EnvelopeWall(id="w1", facade="south", length_m=10.0, height_m=3.0, space_id="S1")
    )
    m.semi_exterior = [dict(r) for r in recs]
    return m


def test_settled_semi_exterior_floor_record_gets_the_semiheated_floor_row():
    m = _model(_rec("S1", "S2"))
    s = apply_construction_library(m, "4A", "Nonresidential")
    c = m.constructions[DEFAULT_SEMIEXT_FLOOR_ID]
    assert c.u_value_w_m2k == SEMI_4["u_si"]
    assert c.provenance.method == "construction_default"
    note = c.provenance.note
    assert "Semiheated" in note and "5.5.2" in note and "Table 5.5-4" in note
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_FLOOR_ID
    d = s.defaulted[DEFAULT_SEMIEXT_FLOOR_ID]
    assert d["surface"] == "ExteriorFloor"
    assert d["category"] == "Semiheated" and d["construction_type"] == "SteelFramed"
    assert d["space_pairs"] == [["S1", "S2"]]


@pytest.mark.parametrize("category", ["Nonresidential", "Residential", "Semiheated"])
def test_semiheated_column_whatever_the_building_category(category):
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, "4A", category)
    assert m.constructions[DEFAULT_SEMIEXT_FLOOR_ID].u_value_w_m2k == SEMI_4["u_si"]


@pytest.mark.parametrize("cz,num", [("2A", "2"), ("5B", "5"), ("7", "7")])
def test_row_follows_the_climate_zone(cz, num):
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, cz)
    row = CONSTRUCTION_LIBRARY[("Semiheated", num, "ExteriorFloor", "SteelFramed")]
    assert m.constructions[DEFAULT_SEMIEXT_FLOOR_ID].u_value_w_m2k == row["u_si"]


def test_review_floor_record_keeps_the_partition():
    m = _model(_rec("S1", "S2", kind="review"))
    s = apply_construction_library(m, "4A")
    assert DEFAULT_SEMIEXT_FLOOR_ID not in m.constructions
    assert DEFAULT_SEMIEXT_FLOOR_ID not in s.defaulted
    assert "construction_id" not in m.semi_exterior[0]
    assert semi_exterior_floors(m) == {}
    assert semi_exterior_walls(m) == {}


def test_no_floor_record_writes_nothing():
    m = _model()
    s = apply_construction_library(m, "4A")
    assert DEFAULT_SEMIEXT_FLOOR_ID not in m.constructions
    assert DEFAULT_SEMIEXT_FLOOR_ID not in s.defaulted


def test_no_climate_zone_writes_nothing():
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, "")
    assert DEFAULT_SEMIEXT_FLOOR_ID not in m.constructions
    assert semi_exterior_floors(m) == {}


def test_stated_construction_and_named_records_are_kept():
    m = _model(_rec("S1", "S2"), _rec("S2", "S3", construction_id="drawn-floor"))
    m.constructions[DEFAULT_SEMIEXT_FLOOR_ID] = Construction(
        id=DEFAULT_SEMIEXT_FLOOR_ID, name="Drawn", u_value_w_m2k=0.25
    )
    s = LibrarySummary()
    _default_semi_exterior_floor(m, s, "4A")
    assert m.constructions[DEFAULT_SEMIEXT_FLOOR_ID].u_value_w_m2k == 0.25
    assert DEFAULT_SEMIEXT_FLOOR_ID not in s.defaulted
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_FLOOR_ID
    assert m.semi_exterior[1]["construction_id"] == "drawn-floor"


def test_exporter_view_lists_pairs_per_construction():
    m = _model(_rec("S2", "S1"), _rec("S2", "S3"), _rec("S1", "S3", kind="review"))
    apply_construction_library(m, "4A")
    out = semi_exterior_floors(m)
    assert list(out) == [DEFAULT_SEMIEXT_FLOOR_ID]
    info = out[DEFAULT_SEMIEXT_FLOOR_ID]
    assert info["u"] == SEMI_4["u_si"]
    assert info["pairs"] == [["S1", "S2"], ["S2", "S3"]]


def test_walls_view_ignores_floor_records():
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, "4A")
    assert semi_exterior_walls(m) == {}
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_FLOOR_ID


def test_wall_record_gets_the_wall_row_not_the_floor_row():
    m = _model(
        {
            "spaces": ["S1", "S2"],
            "categories": ["conditioned", "unconditioned"],
            "kind": "semi_exterior",
            "reason": "conditioned against unconditioned",
            "length_m": 10.0,
            "segments": [[[10.0, 0.0], [10.0, 10.0]]],
        }
    )
    apply_construction_library(m, "4A")
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_WALL_ID
    assert DEFAULT_SEMIEXT_FLOOR_ID not in m.constructions


# ---- multi-storey gbXML -----------------------------------------------------

RING = [(0.0, 0.0), (30.0, 0.0), (30.0, 12.0), (0.0, 12.0)]


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


def _two_storey(*recs) -> BuildingModel:
    m = BuildingModel(name="t", auto_triage=False)
    m.levels = [
        Level(id="L1", name="Level L1", elevation_z_m=0.0, wall_height_m=3.0, above_ground=True),
        Level(id="L2", name="Level L2", elevation_z_m=3.0, wall_height_m=3.0, above_ground=True),
    ]
    for sid, lid, box in (("L1-101", "L1", (0, 0, 20, 10)), ("L2-201", "L2", (0, 0, 20, 10))):
        m.spaces[sid] = Space(
            id=sid, level_id=lid, polygon_m=_rect(*box), area_m2=Polygon(_rect(*box)).area
        )
        m.envelope.extend(_walls(sid, sid, *box))
    m.semi_exterior = [dict(r) for r in recs]
    return m


def _write(tmp_path, m):
    apply_construction_library(m, "4A")
    bem = _bem_from_model(m)
    bem.interior_floor_constructions = semi_exterior_floors(m)
    p = write_gbxml(bem, tmp_path / "m.xml")
    ok, errs = validate_gbxml(p)
    assert ok, errs[:5]
    return ET.parse(p).getroot()


def _floors(root):
    return [
        s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == "InteriorFloor"
    ]


def test_multi_storey_interior_floor_takes_the_layered_semiheated_construction(tmp_path):
    root = _write(tmp_path, _two_storey(_rec("L1-101", "L2-201")))
    (fl,) = _floors(root)
    assert fl.get("constructionIdRef") == DEFAULT_SEMIEXT_FLOOR_ID
    adj = [a.get("spaceIdRef") for a in fl.findall("g:AdjacentSpaceId", NS)]
    assert adj == ["L1-101", "L2-201"]
    co = root.find(f"g:Construction[@id='{DEFAULT_SEMIEXT_FLOOR_ID}']", NS)
    assert float(co.find("g:U-value", NS).text) == pytest.approx(SEMI_4["u_si"], rel=1e-5)
    r = float(root.find(f"g:Material[@id='mat-{DEFAULT_SEMIEXT_FLOOR_ID}']/g:R-value", NS).text)
    assert r == pytest.approx(1.0 / SEMI_4["u_si"] - FILM_R_SI["InteriorFloor"], abs=1e-4)


def test_multi_storey_without_a_record_exports_as_before(tmp_path):
    root = _write(tmp_path, _two_storey())
    assert [s.get("constructionIdRef") for s in _floors(root)] == ["const-intfloor"]
    assert root.find(f"g:Construction[@id='{DEFAULT_SEMIEXT_FLOOR_ID}']", NS) is None
