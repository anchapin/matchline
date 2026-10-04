"""Wall openings stay on their own space's share of a split facade."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from bem_export import write_gbxml
from bem_helpers import _distribute_openings, _edge_spaces
from ifc_export import _bem_from_model
from tests.model_factory import add_wall_constructions, make_clean_model

G = "{http://www.gbxml.org/schema}"

# south facade split at x=5: edge 0 owned by A, edge 1 by B; edge 2 is north
EDGES = [((0.0, 0.0), (5.0, 0.0)), ((5.0, 0.0), (10.0, 0.0)), ((10.0, 6.0), (0.0, 6.0))]
FACADES = ["south", "south", "north"]
SPACES = ["A", "B", "A"]


def _u(tag, facade="south", sid=""):
    return SimpleNamespace(category="window", tag=tag, host_facade=facade, space_sid=sid)


def _where(assign):
    return {u.tag: i for i, us in assign.items() for u in us}


def _split():
    m = make_clean_model()
    add_wall_constructions(m)
    return m


def test_space_and_facade_confine_the_opening():
    units = [_u("w1", sid="A"), _u("w2", sid="A")]
    assert set(_where(_distribute_openings(units, EDGES, FACADES, SPACES)).values()) == {0}


def test_unmatched_space_falls_back_to_facade_split():
    units = [_u("w1", sid="Z"), _u("w2", sid="Z")]
    got = _where(_distribute_openings(units, EDGES, FACADES, SPACES))
    assert sorted(got.values()) == [0, 1]


def test_without_edge_spaces_behaviour_is_unchanged():
    units = [_u("w1", sid="A"), _u("w2", sid="A")]
    assert _distribute_openings(units, EDGES, FACADES) == _distribute_openings(
        units, EDGES, FACADES, None
    )
    assert sorted(_where(_distribute_openings(units, EDGES, FACADES)).values()) == [0, 1]


def test_mismatched_edge_spaces_are_ignored():
    units = [_u("w1", sid="A"), _u("w2", sid="A")]
    got = _where(_distribute_openings(units, EDGES, FACADES, ["A"]))
    assert sorted(got.values()) == [0, 1]


def test_edge_spaces_empty_without_spaces():
    assert _edge_spaces(EDGES, []) == []


def test_split_model_gbxml_keeps_windows_on_own_space(tmp_path):
    m = _split()
    p = tmp_path / "b.xml"
    write_gbxml(_bem_from_model(m), p)
    hosts = []
    for s in ET.parse(p).getroot().iter(G + "Surface"):
        if s.get("surfaceType") == "ExteriorWall":
            sid = s.find(G + "AdjacentSpaceId").get("spaceIdRef")
            hosts += [sid] * len(s.findall(G + "Opening"))
    want = sorted(sid for sid, sp in m.spaces.items() for o in sp.openings)
    assert sorted(hosts) == want


def test_split_model_ifc_roundtrip_keeps_space_ownership(tmp_path):
    pytest.importorskip("ifcopenshell")
    from ifc_export import _export_ifc
    from ifc_import import import_ifc

    m = _split()
    p = tmp_path / "b.ifc"
    _export_ifc(m, p)
    back = import_ifc(p)

    def own(model):
        return {
            k: sorted((o.tag, o.host_facade) for o in sp.openings) for k, sp in model.spaces.items()
        }

    assert own(back) == own(m)


def test_ifc_adapter_tags_wall_units_with_their_space():
    m = _split()
    bem = _bem_from_model(m)
    want = sorted(
        sid for sid, sp in m.spaces.items() for o in sp.openings if o.category != "skylight"
    )
    assert sorted(u.space_sid for u in bem.openings if u.category != "skylight") == want
