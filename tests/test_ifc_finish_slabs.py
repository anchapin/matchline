"""Finish floors on structural slabs (#584).

Idea from Pascal's room-first.ts (MIT, Copyright (c) 2026 Pascal Group Inc.,
commit 67f8041): a slab up to 0.06 m thick lying on a structural floor is a
finish, never a second floor.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.pset as ps  # noqa: E402
import ifcopenshell.api.spatial as sp  # noqa: E402
import ifcopenshell.guid as guid  # noqa: E402

from ifc_import import import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _place  # noqa: E402
from tests.test_ifc_import import _add_solid, _bem_fixture, make_ifc_fixture  # noqa: E402

FULL = [(0.0, 0.0), (20.0, 0.0), (20.0, 12.0), (0.0, 12.0)]
OFFICE = [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)]


def _base(tmp_path):
    bem = _bem_fixture()
    bem.slab_u_value_w_m2k = 0.35  # exporter then writes a 0.15 m BASESLAB, top at z=0
    return make_ifc_fixture(tmp_path / "base.ifc", bem=bem)


def _with_slabs(tmp_path, slabs):
    """``slabs``: (name, outline, z bottom, thickness, PredefinedType, U or None)."""
    path = _base(tmp_path)
    f = ifcopenshell.open(str(path))
    st = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    gids = {}
    for name, outline, z, t, ptype, u in slabs:
        s = f.create_entity("IfcSlab", GlobalId=guid.new(), Name=name, PredefinedType=ptype)
        s.ObjectPlacement = _place(f, (0, 0, z), st.ObjectPlacement)
        pts = tuple(
            f.create_entity("IfcCartesianPoint", Coordinates=tuple(map(float, p)))
            for p in outline + [outline[0]]
        )
        prof = f.create_entity(
            "IfcArbitraryClosedProfileDef",
            ProfileType="AREA",
            OuterCurve=f.create_entity("IfcPolyline", Points=pts),
        )
        _add_solid(f, s, prof, t, body)
        sp.assign_container(f, products=[s], relating_structure=st)
        if u is not None:
            pset = ps.add_pset(f, product=s, name="Pset_SlabCommon")
            ps.edit_pset(f, pset=pset, properties={"ThermalTransmittance": u})
        gids[name] = s.GlobalId
    out = tmp_path / "slabs.ifc"
    f.write(str(out))
    return import_ifc(out), gids


def _floor_area(m):
    return sum(s.area_m2 or 0 for s in m.spaces.values())


def _el(m, gid):
    return next(e for e in m.bim_elements if e.global_id == gid)


def _floor_plates(m):
    return [e for e in m.bim_elements if e.ifc_class == "IfcSlab" and e.role != "finish"]


def test_20mm_finish_on_slab_is_one_floor_and_same_area(tmp_path):
    plain = import_ifc(_base(tmp_path))
    m, g = _with_slabs(tmp_path, [("Finish Floor", FULL, 0.0, 0.02, "FLOOR", None)])
    e = _el(m, g["Finish Floor"])
    assert e.role == "finish" and "finish floor (0.02 m)" in e.provenance.note
    assert len(_floor_plates(m)) == len(_floor_plates(plain)) == 1
    assert _floor_area(m) == pytest.approx(_floor_area(plain))
    assert m.slab_construction_id == plain.slab_construction_id == "IFC-SU0.3500"
    assert "finish slabs: 1 on structural floors" in m.revision_log[-1].note
    for sid in ("L1-101", "L1-102", "L1-103"):
        assert m.spaces[sid].floor_finishes == [{"global_id": e.global_id, "thickness_m": 0.02}]


def test_finish_over_one_room_is_recorded_on_that_room_only(tmp_path):
    m, g = _with_slabs(tmp_path, [("Carpet", OFFICE, 0.0, 0.015, "NOTDEFINED", None)])
    assert _el(m, g["Carpet"]).role == "finish"
    assert [f["global_id"] for f in m.spaces["L1-101"].floor_finishes] == [g["Carpet"]]
    assert m.spaces["L1-102"].floor_finishes == []


def test_finish_typed_baseslab_stays_out_of_ground_slab_u(tmp_path):
    m, g = _with_slabs(tmp_path, [("Screed", FULL, 0.0, 0.05, "BASESLAB", 1.8)])
    assert _el(m, g["Screed"]).role == "finish"
    assert m.slab_construction_id == "IFC-SU0.3500"


def test_thin_slab_with_nothing_under_it_is_not_a_finish(tmp_path):
    m, g = _with_slabs(tmp_path, [("Mezzanine deck", OFFICE, 2.0, 0.05, "FLOOR", None)])
    assert _el(m, g["Mezzanine deck"]).role == ""
    assert "finish slabs" not in m.revision_log[-1].note


def test_thick_second_slab_is_not_a_finish(tmp_path):
    m, g = _with_slabs(tmp_path, [("Topping", FULL, 0.0, 0.08, "FLOOR", None)])
    assert _el(m, g["Topping"]).role == ""


def test_finish_mostly_off_the_slab_is_not_a_finish(tmp_path):
    out = [(15.0, 0.0), (30.0, 0.0), (30.0, 12.0), (15.0, 12.0)]
    m, g = _with_slabs(tmp_path, [("Terrace tile", out, 0.0, 0.02, "FLOOR", None)])
    assert _el(m, g["Terrace tile"]).role == ""


def test_finish_with_a_gap_under_it_is_not_a_finish(tmp_path):
    m, g = _with_slabs(tmp_path, [("Raised", FULL, 0.05, 0.02, "FLOOR", None)])
    assert _el(m, g["Raised"]).role == ""


def test_plain_fixture_has_no_finishes(tmp_path):
    m = import_ifc(_base(tmp_path))
    assert all(e.role != "finish" for e in m.bim_elements)
    assert all(s.floor_finishes == [] for s in m.spaces.values())
