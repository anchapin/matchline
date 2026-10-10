"""Semi-exterior walls take the Table 5.5 Semiheated construction (#747 slice 6).

ASHRAE 90.1-2019 5.5.2: "If a building contains any semiheated space or
unconditioned space then the semiexterior building envelope shall comply with
the requirements for semiheated space in Tables 5.5-0 through 5.5-8 for the
appropriate climate" (Alex, 747#issuecomment-6101561224).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from bem_export import BEMModel, BEMSpace, validate_gbxml, write_gbxml
from bem_layers import FILM_R_SI
from building_model import BuildingModel, Construction, EnvelopeWall, Space
from construction_library import (
    DEFAULT_SEMIEXT_WALL_ID,
    LibrarySummary,
    _default_semi_exterior,
    apply_construction_library,
)
from construction_library_data import CONSTRUCTION_LIBRARY
from constructions import semi_exterior_walls

NS = {"g": "http://www.gbxml.org/schema"}
SEMI_4 = CONSTRUCTION_LIBRARY[("Semiheated", "4", "ExteriorWall", "SteelFramed")]


def _rec(a, b, kind="semi_exterior", **kw):
    return {
        "spaces": [a, b],
        "categories": ["conditioned", "unconditioned"],
        "kind": kind,
        "reason": "conditioned against unconditioned",
        "length_m": 10.0,
        "segments": [[[10.0, 0.0], [10.0, 10.0]]],
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


def test_settled_semi_exterior_boundary_gets_the_semiheated_wall_row():
    m = _model(_rec("S1", "S2"))
    s = apply_construction_library(m, "4A", "Nonresidential")
    c = m.constructions[DEFAULT_SEMIEXT_WALL_ID]
    assert c.u_value_w_m2k == SEMI_4["u_si"]
    assert c.provenance.method == "construction_default"
    note = c.provenance.note
    assert "Semiheated" in note and "5.5.2" in note and "Table 5.5-4" in note
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_WALL_ID
    d = s.defaulted[DEFAULT_SEMIEXT_WALL_ID]
    assert d["category"] == "Semiheated" and d["construction_type"] == "SteelFramed"
    assert d["space_pairs"] == [["S1", "S2"]]


@pytest.mark.parametrize("category", ["Nonresidential", "Residential", "Semiheated"])
def test_semiheated_column_whatever_the_building_category(category):
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, "4A", category)
    assert m.constructions[DEFAULT_SEMIEXT_WALL_ID].u_value_w_m2k == SEMI_4["u_si"]


@pytest.mark.parametrize("cz,num", [("2A", "2"), ("5B", "5"), ("7", "7")])
def test_row_follows_the_climate_zone(cz, num):
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, cz)
    row = CONSTRUCTION_LIBRARY[("Semiheated", num, "ExteriorWall", "SteelFramed")]
    assert m.constructions[DEFAULT_SEMIEXT_WALL_ID].u_value_w_m2k == row["u_si"]


def test_review_boundary_keeps_the_partition():
    m = _model(_rec("S1", "S2", kind="review"))
    s = apply_construction_library(m, "4A")
    assert DEFAULT_SEMIEXT_WALL_ID not in m.constructions
    assert DEFAULT_SEMIEXT_WALL_ID not in s.defaulted
    assert "construction_id" not in m.semi_exterior[0]
    assert semi_exterior_walls(m) == {}


def test_no_climate_zone_writes_nothing():
    m = _model(_rec("S1", "S2"))
    apply_construction_library(m, "")
    assert DEFAULT_SEMIEXT_WALL_ID not in m.constructions
    assert semi_exterior_walls(m) == {}


def test_no_semi_exterior_boundary_writes_nothing():
    m = _model()
    s = apply_construction_library(m, "4A")
    assert DEFAULT_SEMIEXT_WALL_ID not in m.constructions
    assert DEFAULT_SEMIEXT_WALL_ID not in s.defaulted


def test_stated_construction_and_named_records_are_kept():
    m = _model(_rec("S1", "S2"), _rec("S2", "S3", construction_id="drawn-wall"))
    m.constructions[DEFAULT_SEMIEXT_WALL_ID] = Construction(
        id=DEFAULT_SEMIEXT_WALL_ID, name="Drawn", u_value_w_m2k=0.25
    )
    s = LibrarySummary()
    _default_semi_exterior(m, s, "4A")
    assert m.constructions[DEFAULT_SEMIEXT_WALL_ID].u_value_w_m2k == 0.25
    assert DEFAULT_SEMIEXT_WALL_ID not in s.defaulted
    assert m.semi_exterior[0]["construction_id"] == DEFAULT_SEMIEXT_WALL_ID
    assert m.semi_exterior[1]["construction_id"] == "drawn-wall"


def test_exporter_view_lists_pairs_per_construction():
    m = _model(_rec("S2", "S1"), _rec("S2", "S3"), _rec("S1", "S3", kind="review"))
    apply_construction_library(m, "4A")
    out = semi_exterior_walls(m)
    assert list(out) == [DEFAULT_SEMIEXT_WALL_ID]
    info = out[DEFAULT_SEMIEXT_WALL_ID]
    assert info["u"] == SEMI_4["u_si"]
    assert info["pairs"] == [["S1", "S2"], ["S2", "S3"]]


# ---- gbXML ----------------------------------------------------------------

RING = [(0.0, 0.0), (30.0, 0.0), (30.0, 12.0), (0.0, 12.0)]


def _rect(x0, x1):
    return [(x0, 0.0), (x1, 0.0), (x1, 12.0), (x0, 12.0)]


def _bem(pairs, spaces=None):
    spaces = spaces or [
        BEMSpace(
            f"sp-{k + 1}", f"ROOM {k + 1}", str(100 + k), _rect(10 * k, 10 * (k + 1)), 120.0, 360.0
        )
        for k in range(3)
    ]
    iwc = (
        {
            DEFAULT_SEMIEXT_WALL_ID: {
                "name": "Semi-exterior wall",
                "u": SEMI_4["u_si"],
                "pairs": pairs,
            }
        }
        if pairs
        else {}
    )
    return BEMModel(
        building_name="Three rooms",
        spaces=spaces,
        openings=[],
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
        interior_wall_constructions=iwc,
    )


def _write(tmp_path, model):
    p = write_gbxml(model, tmp_path / "m.xml")
    ok, errs = validate_gbxml(p)
    assert ok, errs
    return ET.parse(p).getroot()


def _int_refs(root):
    out = {}
    for s in root.findall(".//g:Surface[@surfaceType='InteriorWall']", NS):
        pair = tuple(sorted(a.get("spaceIdRef") for a in s.findall("g:AdjacentSpaceId", NS)))
        out[pair] = s.get("constructionIdRef")
    return out


def test_semi_exterior_interior_wall_takes_the_layered_semiheated_construction(tmp_path):
    root = _write(tmp_path, _bem([["sp-1", "sp-2"]]))
    assert _int_refs(root) == {
        ("sp-1", "sp-2"): DEFAULT_SEMIEXT_WALL_ID,
        ("sp-2", "sp-3"): "const-intwall",
    }
    co = root.find(f"g:Construction[@id='{DEFAULT_SEMIEXT_WALL_ID}']", NS)
    assert float(co.find("g:U-value", NS).text) == pytest.approx(SEMI_4["u_si"], rel=1e-5)
    r = float(root.find(f"g:Material[@id='mat-{DEFAULT_SEMIEXT_WALL_ID}']/g:R-value", NS).text)
    assert r == pytest.approx(1.0 / SEMI_4["u_si"] - FILM_R_SI["InteriorWall"], abs=1e-4)
    assert root.find("g:Construction[@id='const-intwall']", NS) is not None


def test_no_semi_exterior_pairs_export_as_before(tmp_path):
    root = _write(tmp_path, _bem([]))
    assert set(_int_refs(root).values()) == {"const-intwall"}
    assert root.find(f"g:Construction[@id='{DEFAULT_SEMIEXT_WALL_ID}']", NS) is None


def test_pieces_of_a_split_room_match_through_their_parent(tmp_path):
    spaces = [
        BEMSpace("sp-1", "ROOM 1", "100", _rect(0, 10), 120.0, 360.0),
        BEMSpace("sp-2-a", "ROOM 2", "101", _rect(10, 20), 120.0, 360.0, split_from="sp-2"),
        BEMSpace("sp-3", "ROOM 3", "102", _rect(20, 30), 120.0, 360.0),
    ]
    root = _write(tmp_path, _bem([["sp-1", "sp-2"]], spaces))
    assert _int_refs(root) == {
        ("sp-1", "sp-2-a"): DEFAULT_SEMIEXT_WALL_ID,
        ("sp-2-a", "sp-3"): "const-intwall",
    }
