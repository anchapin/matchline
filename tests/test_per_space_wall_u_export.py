"""Per-space area-weighted wall U reaches gbXML and IFC (roadmap item 6)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from bem_export import validate_gbxml, write_gbxml
from constructions import apply_wall_u_rollup
from geometry_simplify import footprint_from_regions
from ifc_export import _bem_from_model
from run_pipeline import model_from_linked_model
from tests.model_factory import add_wall_constructions, make_clean_model

G = "{http://www.gbxml.org/schema}"


def _rolled():
    m = make_clean_model()
    add_wall_constructions(m)
    apply_wall_u_rollup(m)
    return m


def _gbxml(bem, tmp_path):
    p = tmp_path / "b.xml"
    write_gbxml(bem, p)
    return p, ET.parse(p).getroot()


def _constructions(root):
    out = {}
    for c in root.iter(G + "Construction"):
        u = c.find(G + "U-value")
        out[c.get("id")] = float(u.text) if u is not None else None
    return out


def _wall_refs(root):
    return [
        (s.get("constructionIdRef"), s.find(G + "AdjacentSpaceId").get("spaceIdRef"))
        for s in root.iter(G + "Surface")
        if s.get("surfaceType") == "ExteriorWall"
    ]


def test_gbxml_writes_one_construction_per_space_with_its_u(tmp_path):
    m = _rolled()
    _, root = _gbxml(_bem_from_model(m), tmp_path)
    cons = _constructions(root)
    for sid, sp in m.spaces.items():
        assert cons[f"const-wall-{sid}"] == pytest.approx(sp.wall_u_value_w_m2k, abs=1e-4)


def test_gbxml_walls_reference_their_space_construction(tmp_path):
    _, root = _gbxml(_bem_from_model(_rolled()), tmp_path)
    refs = _wall_refs(root)
    assert refs
    assert all(cid == f"const-wall-{sid}" for cid, sid in refs)


def test_gbxml_with_per_space_constructions_is_schema_valid(tmp_path):
    p, _ = _gbxml(_bem_from_model(_rolled()), tmp_path)
    ok, errs = validate_gbxml(p)
    assert ok, errs[:3]


def test_no_rollup_keeps_generic_wall_construction(tmp_path):
    _, root = _gbxml(_bem_from_model(make_clean_model()), tmp_path)
    assert not [c for c in _constructions(root) if c.startswith("const-wall-")]
    assert {cid for cid, _ in _wall_refs(root)} == {"const-wall"}


def test_non_positive_u_falls_back_to_generic(tmp_path):
    m = _rolled()
    m.spaces["L1-102"].wall_u_value_w_m2k = 0.0
    _, root = _gbxml(_bem_from_model(m), tmp_path)
    refs = dict((sid, cid) for cid, sid in _wall_refs(root))
    assert refs["L1-102"] == "const-wall"
    assert refs["L1-101"] == "const-wall-L1-101"


def test_linked_model_adapter_carries_wall_u(tmp_path):
    m = _rolled()
    ring = footprint_from_regions([sp.polygon_m for sp in m.spaces.values()])
    bem = model_from_linked_model(
        model=m, simplified_ring=ring, wall_height_m=3.0, simplify_tolerance=0.0
    )
    assert {s.sid: s.wall_u_value_w_m2k for s in bem.spaces} == {
        sid: sp.wall_u_value_w_m2k for sid, sp in m.spaces.items()
    }
    _, root = _gbxml(bem, tmp_path)
    assert all(cid == f"const-wall-{sid}" for cid, sid in _wall_refs(root))


def test_ifc_walls_carry_thermal_transmittance(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.util.element as E

    from bem_ifc4 import write_ifc4

    m = _rolled()
    p = tmp_path / "b.ifc"
    write_ifc4(_bem_from_model(m), p)
    f = ifcopenshell.open(str(p))  # keep the file alive while reading its walls
    us = sorted(
        round(E.get_psets(w)["Pset_WallCommon"]["ThermalTransmittance"], 6)
        for w in f.by_type("IfcWall")
    )
    want = {round(sp.wall_u_value_w_m2k, 6) for sp in m.spaces.values()}
    assert set(us) == want


def test_ifc_walls_without_u_get_no_pset(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.util.element as E

    from bem_ifc4 import write_ifc4

    p = tmp_path / "b.ifc"
    write_ifc4(_bem_from_model(make_clean_model()), p)
    f = ifcopenshell.open(str(p))
    for w in f.by_type("IfcWall"):
        assert "Pset_WallCommon" not in E.get_psets(w)
