"""Lining walls add their resistance to the host's U in series (#597)."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.pset as _Ps  # noqa: E402
import ifcopenshell.api.spatial as _Sp  # noqa: E402
import ifcopenshell.guid as guid  # noqa: E402

from ifc_import import RSE_WALL_M2K_W, RSI_WALL_M2K_W, import_ifc  # noqa: E402
from ifc_wall_linings import cladding_hosts, host_cover  # noqa: E402
from tests.test_ifc_closet_merge import _place  # noqa: E402
from tests.test_ifc_import import _add_solid, _rect_profile, make_ifc_fixture  # noqa: E402
from tests.test_ifc_import_layered_wall_u import _layer_set  # noqa: E402

RS = RSI_WALL_M2K_W + RSE_WALL_M2K_W
BLOCK = [("Block", 0.2, 0.5, False)]
WOOL = [("Wool", 0.1, 0.035, False)]


def _south(path):
    m = import_ifc(path)
    (w,) = [w for w in m.envelope if w.facade == "south"]
    return w.provenance.note.split(" ", 1)[0][len("GlobalId=") :]


def _build(tmp_path, host_layers=BLOCK, host_u=None, lining=None):
    """Base fixture; the south wall (IFC y 0..0.2) gets ``host_layers``.

    ``lining``: (start (x, y), length, thickness, layers or None).
    """
    path = make_ifc_fixture(tmp_path / "base.ifc")
    gid = _south(path)
    f = ifcopenshell.open(str(path))
    host = f.by_guid(gid)
    for rel in list(host.HasAssociations or []):
        if rel.is_a("IfcRelAssociatesMaterial"):
            f.remove(rel)
    if host_layers:
        f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=guid.new(),
            RelatedObjects=[host],
            RelatingMaterial=_layer_set(f, host_layers),
        )
    if host_u is not None:
        pset = _Ps.add_pset(f, product=host, name="Pset_WallCommon")
        _Ps.edit_pset(f, pset=pset, properties={"ThermalTransmittance": host_u})
    lid = ""
    if lining is not None:
        (x, y), length, t, layers = lining
        st = f.by_type("IfcBuildingStorey")[0]
        body = [
            c
            for c in f.by_type("IfcGeometricRepresentationSubContext")
            if c.ContextIdentifier == "Body"
        ][0]
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name="Lining")
        w.ObjectPlacement = _place(f, (x, y, 0), st.ObjectPlacement, refdir=(1, 0, 0))
        _add_solid(f, w, _rect_profile(f, length, t, ox=length / 2), 3.0, body)
        _Sp.assign_container(f, products=[w], relating_structure=st)
        if layers:
            f.create_entity(
                "IfcRelAssociatesMaterial",
                GlobalId=guid.new(),
                RelatedObjects=[w],
                RelatingMaterial=_layer_set(f, layers),
            )
        lid = w.GlobalId
    out = tmp_path / "lined.ifc"
    f.write(str(out))
    return import_ifc(out), gid, lid


def _host(m, gid):
    (w,) = [w for w in m.envelope if w.facade == "south"]
    assert gid in w.provenance.note
    return w, m.constructions.get(w.construction_id)


def _note(m, gid):
    return next(e.provenance.note for e in m.bim_elements if e.global_id == gid)


# 100 mm wool on the inner face (IFC y 0.2..0.3), 19 of the host's 20 m
INNER = ((0.5, 0.25), 19.0, 0.1, WOOL)


def test_face_to_face_wool_adds_in_series(tmp_path):
    m, gid, lid = _build(tmp_path, lining=INNER)
    w, c = _host(m, gid)
    assert w.construction_id.startswith("IFC-ULL")
    assert c.u_value_w_m2k == pytest.approx(1 / (RS + 0.2 / 0.5 + 0.1 / 0.035), abs=1e-6)
    assert c.provenance.method == "ifc_import:tier0:wall_u_lining"
    assert c.provenance.confidence == pytest.approx(0.8)
    assert gid in c.provenance.note and lid in c.provenance.note
    assert "lining U: 1 host walls with linings in series" in m.revision_log[-1].note


def test_other_walls_keep_their_construction(tmp_path):
    base, _, _ = _build(tmp_path / "a")
    lined, _, _ = _build(tmp_path / "b", lining=INNER)

    def others(m):
        return sorted((w.facade, w.construction_id) for w in m.envelope if w.facade != "south")

    assert others(lined) == others(base)
    assert _host(base, _south_gid(base))[0].construction_id.startswith("IFC-UL")


def _south_gid(m):
    (w,) = [w for w in m.envelope if w.facade == "south"]
    return w.provenance.note.split(" ", 1)[0][len("GlobalId=") :]


def test_stated_u_is_never_altered(tmp_path):
    m, gid, lid = _build(tmp_path, host_u=0.3, lining=INNER)
    w, c = _host(m, gid)
    assert w.construction_id == "IFC-U0.3000"
    assert f"lining(s) {lid} present; stated ThermalTransmittance kept" in _note(m, gid)


def test_embedded_wall_adds_nothing(tmp_path):
    m, gid, lid = _build(tmp_path, lining=((5, 0.1), 4.0, 0.1, WOOL))
    w, c = _host(m, gid)
    assert c.u_value_w_m2k == pytest.approx(1 / (RS + 0.2 / 0.5), abs=1e-6)
    assert f"embedded lining {lid} adds no resistance" in _note(m, gid)


def test_lining_without_material_leaves_host_and_flags(tmp_path):
    m, gid, lid = _build(tmp_path, lining=((0.5, 0.25), 19.0, 0.1, None))
    w, c = _host(m, gid)
    assert c.u_value_w_m2k == pytest.approx(1 / (RS + 0.2 / 0.5), abs=1e-6)
    items = [it for it in m.review_queue if it.kind == "lining_u"]
    assert len(items) == 1 and lid in items[0].description


def test_partial_lining_leaves_host_and_flags(tmp_path):
    m, gid, lid = _build(tmp_path, lining=((0.5, 0.25), 10.0, 0.1, WOOL))
    w, c = _host(m, gid)
    assert c.u_value_w_m2k == pytest.approx(1 / (RS + 0.2 / 0.5), abs=1e-6)
    (it,) = [it for it in m.review_queue if it.kind == "lining_u"]
    assert "covers 50%" in it.description


def test_looked_up_conductivity_is_its_own_construction(tmp_path):
    m, gid, _ = _build(
        tmp_path, lining=((0.5, 0.25), 19.0, 0.1, [("Mineral wool", 0.1, None, False)])
    )
    w, c = _host(m, gid)
    assert w.construction_id.startswith("IFC-UML")
    assert c.u_value_w_m2k == pytest.approx(1 / (RS + 0.2 / 0.5 + 0.1 / 0.05), abs=1e-6)
    assert c.provenance.confidence == pytest.approx(0.6)


def test_cladding_reaches_every_host_it_covers():
    hosts = [
        ("A", "L1", [0, 0], [10, 0], 0.2),
        ("B", "L1", [10, 0], [20, 0], 0.2),
        ("C", "L1", [0, 5], [20, 5], 0.2),  # far away
        ("D", "L2", [0, 0], [20, 0], 0.2),  # other level
    ]
    got = dict(cladding_hosts(([2, -0.106], [18, -0.106]), hosts, 0.012, "L1"))
    assert got == pytest.approx({"A": 0.8, "B": 0.8})


def test_host_cover_is_share_of_the_host_run():
    assert host_cover(([5, 0.2], [15, 0.2]), ([0, 0], [20, 0])) == pytest.approx(0.5)
    assert host_cover(([-5, 0], [25, 0]), ([0, 0], [20, 0])) == pytest.approx(1.0)
