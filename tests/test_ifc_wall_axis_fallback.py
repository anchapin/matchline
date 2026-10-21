"""Walls whose body gives no geometry read the file's own declared length (#1219).

A wall whose Body representation carries only a bounding box (or nothing)
gets no length from the geometry chain, so it never reached the envelope
and the exterior wall length collapsed (molio: 48 m of 742 m). The
fallback reads Qto_WallBaseQuantities.Length, then the Axis polyline --
the same ladder bench_truth uses -- and draws the envelope segment along
the axis itself.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.geometry as _Gm  # noqa: E402

from ifc.ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder  # noqa: E402


def _strip_body(b, wall):
    rep = wall.Representation
    rep.Representations = tuple(
        r for r in rep.Representations if r.RepresentationIdentifier != "Body"
    )


def _add_axis(b, wall, length):
    poly = b.f.create_entity("IfcPolyline", Points=(b._pt((0, 0, 0)), b._pt((length, 0, 0))))
    rep = b.f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=b.body,
        RepresentationIdentifier="Axis",
        RepresentationType="Curve2D",
        Items=(poly,),
    )
    _Gm.assign_representation(b.f, product=wall, representation=rep)


def _add_qto(b, wall, length):
    q = b.f.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_WallBaseQuantities",
        Quantities=(
            b.f.create_entity("IfcQuantityLength", Name="Length", LengthValue=float(length) * b.u),
        ),
    )
    b.f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=(wall,),
        RelatingPropertyDefinition=q,
    )


def _bbox_wall(b, length, qto=None):
    w = b.wall("W1", (0, 0), (length, 0))
    _strip_body(b, w)
    _add_axis(b, w, length)
    if qto is not None:
        _add_qto(b, w, qto)
    return w


def test_no_body_wall_reads_axis_length(tmp_path):
    b = IfcBuilder()
    _bbox_wall(b, 20.0)
    p = tmp_path / "axis.ifc"
    b.f.write(str(p))
    m = import_ifc(p)
    assert len(m.envelope) == 1
    w = m.envelope[0]
    assert w.length_m == pytest.approx(20.0, abs=0.01)
    assert w.to_m[0] - w.from_m[0] == pytest.approx(20.0, abs=0.01)
    assert w.from_m[1] == pytest.approx(w.to_m[1], abs=0.01)


def test_qto_length_beats_axis(tmp_path):
    b = IfcBuilder()
    _bbox_wall(b, 20.0, qto=25.0)
    p = tmp_path / "qto.ifc"
    b.f.write(str(p))
    m = import_ifc(p)
    assert len(m.envelope) == 1
    assert m.envelope[0].length_m == pytest.approx(25.0, abs=0.01)


def test_axis_fallback_mm_units(tmp_path):
    b = IfcBuilder(length_units="mm")
    _bbox_wall(b, 20.0)
    p = tmp_path / "axis_mm.ifc"
    b.f.write(str(p))
    m = import_ifc(p)
    assert len(m.envelope) == 1
    assert m.envelope[0].length_m == pytest.approx(20.0, abs=0.01)


def _rotated_profile_body(b, wall, length, thick, height):
    """Replace the Body with a length x thickness rect profile rotated 90 deg.

    The closed-profile bbox of that solid reads (thick, length) in local
    coords, so the bbox path labels the thickness as the wall length --
    exactly what gni-136's exported walls do (gni-136 read 0.36 m and
    6.2 m for walls whose Axis runs 3.64 m and 12.36 m, #1227).
    """
    _strip_body(b, wall)
    pts = [(0, 0), (0, length), (-thick, length), (-thick, 0), (0, 0)]
    curve = b.f.create_entity("IfcPolyline", Points=[b._pt(p) for p in pts])
    prof = b.f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=curve)
    solid = b.f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=prof,
        Position=b.f.create_entity("IfcAxis2Placement3D", Location=b._pt((0, 0, 0))),
        ExtrudedDirection=b.f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=float(height) * b.u,
    )
    rep = b.f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=b.body,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=(solid,),
    )
    _Gm.assign_representation(b.f, product=wall, representation=rep)


def _add_axis_y(b, wall, length):
    poly = b.f.create_entity("IfcPolyline", Points=(b._pt((0, 0, 0)), b._pt((0, length, 0))))
    rep = b.f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=b.body,
        RepresentationIdentifier="Axis",
        RepresentationType="Curve2D",
        Items=(poly,),
    )
    _Gm.assign_representation(b.f, product=wall, representation=rep)


def test_axis_length_outranks_body_bbox(tmp_path):
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (12.36, 0))
    _rotated_profile_body(b, w, 12.36, 0.36, 3.2)
    _add_axis_y(b, w, 12.36)
    p = tmp_path / "rot.ifc"
    b.f.write(str(p))
    m = import_ifc(p)
    assert len(m.envelope) == 1
    assert m.envelope[0].length_m == pytest.approx(12.36, abs=0.01)
    be = next(e for e in m.bim_elements if e.global_id == w.GlobalId)
    assert be.length_m == pytest.approx(12.36, abs=0.01)


def test_qto_length_outranks_body_bbox(tmp_path):
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (20.0, 0))
    _add_axis(b, w, 20.0)
    _add_qto(b, w, 25.0)
    p = tmp_path / "qto_body.ifc"
    b.f.write(str(p))
    m = import_ifc(p)
    assert len(m.envelope) == 1
    assert m.envelope[0].length_m == pytest.approx(25.0, abs=0.01)
