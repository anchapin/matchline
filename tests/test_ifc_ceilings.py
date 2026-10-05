"""Ceiling height and plenum depth from IfcCovering CEILING (#583).

Idea from Pascal's ceiling handling (MIT, Copyright (c) 2026 Pascal Group
Inc., commit 67f8041).
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.geometry as _Gm  # noqa: E402
import ifcopenshell.api.spatial as sp  # noqa: E402
import ifcopenshell.guid as guid  # noqa: E402

from ifc_import import import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _place  # noqa: E402
from tests.test_ifc_import import _add_solid, make_ifc_fixture  # noqa: E402

FULL = [(0.0, 0.0), (20.0, 0.0), (20.0, 12.0), (0.0, 12.0)]
OFFICE = [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)]


def _profile(f, outline):
    pts = tuple(
        f.create_entity("IfcCartesianPoint", Coordinates=tuple(map(float, p)))
        for p in outline + [outline[0]]
    )
    return f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        OuterCurve=f.create_entity("IfcPolyline", Points=pts),
    )


def _sloped_solid(f, product, outline, depth, body, deg=10.0):
    a = math.radians(deg)
    pos = f.create_entity(
        "IfcAxis2Placement3D",
        Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
        Axis=f.create_entity("IfcDirection", DirectionRatios=(-math.sin(a), 0.0, math.cos(a))),
        RefDirection=f.create_entity(
            "IfcDirection", DirectionRatios=(math.cos(a), 0.0, math.sin(a))
        ),
    )
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=_profile(f, outline),
        Position=pos,
        ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=float(depth),
    )
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=(solid,),
    )
    _Gm.assign_representation(f, product=product, representation=rep)


def _build(tmp_path, coverings=(), slabs=(), covers=None):
    """``coverings``: (name, outline, z bottom, thickness, sloped).
    ``slabs``: (name, outline, z bottom, thickness). ``covers``: covering
    name -> text in the IfcSpace Name to link with IfcRelCoversSpaces."""
    path = make_ifc_fixture(tmp_path / "base.ifc")
    f = ifcopenshell.open(str(path))
    st = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    made = {}
    for name, outline, z, t in slabs:
        s = f.create_entity("IfcSlab", GlobalId=guid.new(), Name=name, PredefinedType="FLOOR")
        s.ObjectPlacement = _place(f, (0, 0, z), st.ObjectPlacement)
        _add_solid(f, s, _profile(f, outline), t, body)
        sp.assign_container(f, products=[s], relating_structure=st)
    for name, outline, z, t, sloped in coverings:
        c = f.create_entity("IfcCovering", GlobalId=guid.new(), Name=name, PredefinedType="CEILING")
        c.ObjectPlacement = _place(f, (0, 0, z), st.ObjectPlacement)
        if sloped:
            _sloped_solid(f, c, outline, t, body)
        else:
            _add_solid(f, c, _profile(f, outline), t, body)
        sp.assign_container(f, products=[c], relating_structure=st)
        made[name] = c
    for cname, label in (covers or {}).items():
        space = next(s for s in f.by_type("IfcSpace") if label in (s.Name or ""))
        f.create_entity(
            "IfcRelCoversSpaces",
            GlobalId=guid.new(),
            RelatingSpace=space,
            RelatedCoverings=(made[cname],),
        )
    out = tmp_path / "ceil.ifc"
    f.write(str(out))
    return import_ifc(out), {k: v.GlobalId for k, v in made.items()}


def _summary(m):
    return m.revision_log[-1].note


DECK = [("Deck above", FULL, 3.4, 0.2)]  # underside 3.4 m (3.6 m floor-to-floor)


def test_ceiling_2_7_under_3_6_floor_to_floor(tmp_path):
    m, g = _build(tmp_path, [("Ceiling", OFFICE, 2.7, 0.02, False)], DECK)
    s = m.spaces["L1-101"]
    assert s.ceiling_height_m == pytest.approx(2.7)
    assert s.plenum_depth_m == pytest.approx(3.4 - 2.72)  # 0.9 m less slab and tile
    assert s.history[-1].method == "ifc_import:tier0:ceiling"
    assert g["Ceiling"] in s.history[-1].note
    assert "ceilings: 1 spaces from 1 IfcCovering CEILING" in _summary(m)


def test_space_without_a_covering_reports_none(tmp_path):
    m, _ = _build(tmp_path, [("Ceiling", OFFICE, 2.7, 0.02, False)], DECK)
    for sid in ("L1-102", "L1-103"):
        assert m.spaces[sid].ceiling_height_m is None
        assert m.spaces[sid].plenum_depth_m is None


def test_no_coverings_in_file_changes_nothing(tmp_path):
    m, _ = _build(tmp_path, slabs=DECK)
    assert all(s.ceiling_height_m is None for s in m.spaces.values())
    assert "ceilings:" not in _summary(m)


def test_no_slab_above_leaves_plenum_unknown(tmp_path):
    m, _ = _build(tmp_path, [("Ceiling", OFFICE, 2.7, 0.02, False)])
    assert m.spaces["L1-101"].ceiling_height_m == pytest.approx(2.7)
    assert m.spaces["L1-101"].plenum_depth_m is None


def test_one_covering_over_the_floor_serves_every_room(tmp_path):
    m, _ = _build(tmp_path, [("Grid", FULL, 2.6, 0.02, False)], DECK)
    for sid in ("L1-101", "L1-102", "L1-103"):
        assert m.spaces[sid].ceiling_height_m == pytest.approx(2.6)


def test_coverings_disagreeing_over_a_room_stay_unresolved(tmp_path):
    m, _ = _build(
        tmp_path,
        [("Low", OFFICE, 2.4, 0.02, False), ("High", OFFICE, 2.7, 0.02, False)],
        DECK,
    )
    assert m.spaces["L1-101"].ceiling_height_m is None
    items = [r for r in m.review_queue if r.kind == "ceiling"]
    assert items and "different heights" in items[0].description


def test_sloped_covering_is_never_averaged(tmp_path):
    m, _ = _build(tmp_path, [("Vault", OFFICE, 2.7, 0.02, True)], DECK, covers={"Vault": "101"})
    assert m.spaces["L1-101"].ceiling_height_m is None
    assert any("sloped" in r.description for r in m.review_queue if r.kind == "ceiling")


def test_covers_spaces_relationship_wins_over_plan(tmp_path):
    m, _ = _build(tmp_path, [("Grid", FULL, 2.6, 0.02, False)], DECK, covers={"Grid": "101"})
    assert m.spaces["L1-101"].ceiling_height_m == pytest.approx(2.6)
    assert m.spaces["L1-102"].ceiling_height_m is None
