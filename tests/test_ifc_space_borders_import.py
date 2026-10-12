"""Virtual space borders through the IFC importer (#582)."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.spatial as sp  # noqa: E402
import ifcopenshell.guid as guid  # noqa: E402

from ifc_import import import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _place  # noqa: E402
from tests.test_ifc_import import _add_solid, make_ifc_fixture  # noqa: E402


def _with(tmp_path, cls=None, outline=None):
    path = make_ifc_fixture(tmp_path / "base.ifc")
    if cls is None:
        return import_ifc(path), ""
    f = ifcopenshell.open(str(path))
    st = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    el = f.create_entity(cls, GlobalId=guid.new(), Name="Separator")
    el.ObjectPlacement = _place(f, (0, 0, 0), st.ObjectPlacement)
    pts = tuple(
        f.create_entity("IfcCartesianPoint", Coordinates=tuple(map(float, p)))
        for p in outline + [outline[0]]
    )
    prof = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        OuterCurve=f.create_entity("IfcPolyline", Points=pts),
    )
    _add_solid(f, el, prof, 3.0, body)
    sp.assign_container(f, products=[el], relating_structure=st)
    out = tmp_path / "with.ifc"
    f.write(str(out))
    return import_ifc(out), el.GlobalId


def _pair(m, a, b):
    return [x for x in m.space_adjacencies if {x.space_a, x.space_b} == {a, b}]


def test_open_plan_rooms_get_virtual_borders_and_no_wall_area(tmp_path):
    m, _ = _with(tmp_path)
    (ab,) = _pair(m, "L1-101", "L1-102")
    # each border stops at the inner face of the 0.2 m exterior wall it meets
    assert ab.length_m == pytest.approx(8.0 - 0.2, abs=0.01) and ab.boundary == "virtual"
    assert _pair(m, "L1-101", "L1-103")[0].length_m == pytest.approx(10.0 - 0.2, abs=0.01)
    assert _pair(m, "L1-102", "L1-103")[0].length_m == pytest.approx(10.0 - 0.2, abs=0.01)
    assert len(m.envelope) == 4
    assert sum(w.area_m2 for w in m.envelope) == pytest.approx(189.6)
    assert "space borders: 3 virtual (0 from IfcVirtualElement)" in m.revision_log[-1].note


def test_interior_wall_between_rooms_is_not_virtual(tmp_path):
    m, _ = _with(tmp_path, "IfcWall", [(9.9, 0.0), (10.1, 0.0), (10.1, 8.0), (9.9, 8.0)])
    assert _pair(m, "L1-101", "L1-102") == []
    assert len(_pair(m, "L1-101", "L1-103")) == 1


def test_virtual_element_in_the_file_is_named(tmp_path):
    m, gid = _with(
        tmp_path, "IfcVirtualElement", [(9.995, 0.0), (10.005, 0.0), (10.005, 8.0), (9.995, 8.0)]
    )
    (ab,) = _pair(m, "L1-101", "L1-102")
    assert ab.virtual_element_id == gid and ab.provenance.confidence == pytest.approx(0.9)
