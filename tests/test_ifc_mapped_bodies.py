"""Mapped-item / CSG body unwrap (#1203).

Exporters that put every Body solid behind an IfcMappedItem wrapper, or as
an IfcFacetedBrep / CSG result, used to yield walls with no length and
openings with no volume: _body_extrusions sees none of that and the
geometry kernel fails on the real files. These tests pin the unwrap: the
composed wrapper transforms match ifcopenshell.geom (verified on real
esplanades walls and windows), and the importer falls back to the unwrapped
vertex bbox when the kernel comes back empty.
"""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.api.geometry as _Gm
import pytest

from ifc.ifc_import import _body_vertices, _mapped_body_extents, import_ifc
from tests.ifc_builder import IfcBuilder


def _pt(f, xyz):
    return f.create_entity("IfcCartesianPoint", Coordinates=xyz)


def _dir(f, v):
    return f.create_entity("IfcDirection", DirectionRatios=v)


def _target(f, axis1=(1, 0, 0), axis2=(0, 1, 0), origin=(0, 0, 0), scale=1.0):
    return f.create_entity(
        "IfcCartesianTransformationOperator3D",
        Axis1=_dir(f, axis1),
        Axis2=_dir(f, axis2),
        Axis3=_dir(f, (0, 0, 1)),
        LocalOrigin=_pt(f, origin),
        Scale=scale,
    )


def _map_solid(f, product, origin=(0, 0, 0), target=None):
    """Re-host the product's existing body solid behind an IfcMappedItem."""
    rep = product.Representation.Representations[0]
    solid = rep.Items[0]
    src_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=rep.ContextOfItems,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=(solid,),
    )
    rmap = f.create_entity(
        "IfcRepresentationMap",
        MappingOrigin=f.create_entity("IfcAxis2Placement3D", Location=_pt(f, origin)),
        MappedRepresentation=src_rep,
    )
    mapped = f.create_entity(
        "IfcMappedItem", MappingSource=rmap, MappingTarget=target or _target(f)
    )
    new_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=rep.ContextOfItems,
        RepresentationIdentifier="Body",
        RepresentationType="MappedRepresentation",
        Items=(mapped,),
    )
    product.Representation = None
    _Gm.assign_representation(f, product=product, representation=new_rep)
    return mapped


def _brep_box(f, x0, y0, z0, x1, y1, z1):
    """A closed IfcFacetedBrep box from corner coordinates."""
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    pts = [_pt(f, c) for c in corners]

    def corner(i):
        x, y, z = corners[i]
        return (0 if x == x0 else 4) + (0 if y == y0 else 2) + (0 if z == z0 else 1)

    quads = [
        (0, 2, 3, 1),  # x = x0
        (4, 5, 7, 6),  # x = x1
        (0, 1, 5, 4),  # y = y0
        (2, 6, 7, 3),  # y = y1
        (0, 4, 6, 2),  # z = z0
        (1, 3, 7, 5),  # z = z1
    ]
    faces = []
    for quad in quads:
        loop = f.create_entity("IfcPolyLoop", Polygon=[pts[corner(i)] for i in quad])
        bound = f.create_entity("IfcFaceOuterBound", Bound=loop, Orientation=True)
        faces.append(f.create_entity("IfcFace", Bounds=[bound]))
    shell = f.create_entity("IfcClosedShell", CfsFaces=faces)
    return f.create_entity("IfcFacetedBrep", Outer=shell)


def _set_bodies(f, product, items, rep_type):
    rep = product.Representation.Representations[0]
    new_rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=rep.ContextOfItems,
        RepresentationIdentifier="Body",
        RepresentationType=rep_type,
        Items=tuple(items),
    )
    product.Representation = None
    _Gm.assign_representation(f, product=product, representation=new_rep)


def test_mapped_extrusion_wall_dims(tmp_path):
    """A mapped-item-wrapped extruded wall keeps its length (#1203)."""
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (6, 0), thickness=0.2, height=3.0)
    b.space("S1", [(0, -2), (6, -2), (6, 2), (0, 2)])
    _map_solid(b.f, w)
    path = tmp_path / "m.ifc"
    b.f.write(str(path))
    model = import_ifc(path)
    wall = next(e for e in model.bim_elements if e.global_id == w.GlobalId)
    assert wall.length_m == pytest.approx(6.0, abs=1e-3)
    assert wall.height_m == pytest.approx(3.0, abs=1e-3)
    assert "mapped-item/CSG unwrap" in wall.provenance.note


def test_mapped_origin_translation_compose():
    """The mapping origin composes into the unwrap (kernel interpretation)."""
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (6, 0), thickness=0.2, height=3.0)
    _map_solid(b.f, w, origin=(10, 20, 0))
    verts = _body_vertices(w)
    xs = sorted(verts[0::3])
    ys = sorted(verts[1::3])
    zs = sorted(verts[2::3])
    # the mapped representation is placed by its mapping origin, then the
    # identity target operator maps it into the element's frame
    assert xs[0] == pytest.approx(10.0, abs=1e-6)
    assert xs[-1] == pytest.approx(16.0, abs=1e-6)
    assert ys[0] == pytest.approx(19.9, abs=1e-6)
    assert zs[0] == pytest.approx(0.0, abs=1e-6)
    assert zs[-1] == pytest.approx(3.0, abs=1e-6)


def test_mapped_brep_wall_end_to_end(tmp_path, monkeypatch):
    """A mapped faceted-brep wall (esplanades' shape) gets length and a body
    centreline even when the geometry kernel fails on the file."""
    import ifc.ifc_import as mod

    monkeypatch.setattr(mod, "_geom_verts", lambda el: None)
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (5, 0), thickness=0.25, height=3.0)
    b.space("S1", [(0, -2), (5, -2), (5, 2), (0, 2)])
    brep = _brep_box(b.f, 0, -0.125, 0, 5, 0.125, 3.0)
    _set_bodies(b.f, w, [brep], "Brep")
    _map_solid(b.f, w, origin=(7, 0, 0))
    path = tmp_path / "m.ifc"
    b.f.write(str(path))
    model = import_ifc(path)
    wall = next(e for e in model.bim_elements if e.global_id == w.GlobalId)
    assert wall.length_m == pytest.approx(5.0, abs=1e-3)
    assert "mapped-item/CSG unwrap" in wall.provenance.note
    # the body sits at x 7..12 in the wall's frame, so the envelope segment
    # moves onto it (the placement axis alone would put it at 0..5)
    env = next(
        e
        for e in model.envelope
        if e.provenance and "GlobalId=" + w.GlobalId in (e.provenance.note or "")
    )
    assert env.length_m == pytest.approx(5.0, abs=1e-3)
    assert env.from_m[0] == pytest.approx(7.0, abs=1e-3)
    assert env.to_m[0] == pytest.approx(12.0, abs=1e-3)
    assert "moved onto body centreline" in env.provenance.note


def test_opening_mapped_brep(tmp_path, monkeypatch):
    """A mapped brep opening body still yields width and height when the
    kernel fails (#1203): esplanades windows read area ~0 without this."""
    import ifc.ifc_import as mod

    monkeypatch.setattr(mod, "_geom_verts", lambda el: None)
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (6, 0), thickness=0.2, height=3.0)
    b.space("S1", [(0, -2), (6, -2), (6, 2), (0, 2)])
    fill = b.opening(w, 3.0, 1.2, 2.1, sill=0.9, kind="IfcWindow")
    op = next(o for o in b.f.by_type("IfcOpeningElement"))
    brep = _brep_box(b.f, -0.6, -0.1, 0.0, 0.6, 0.1, 2.1)
    _set_bodies(b.f, op, [brep], "Brep")
    _map_solid(b.f, op)
    path = tmp_path / "m.ifc"
    b.f.write(str(path))
    model = import_ifc(path)
    wall = next(e for e in model.bim_elements if e.global_id == w.GlobalId)
    opening = wall.openings[0]
    assert opening.category == "window"
    assert opening.width_m == pytest.approx(1.2, abs=1e-3)
    assert opening.height_m == pytest.approx(2.1, abs=1e-3)
    assert "mapped body unwrap" in opening.provenance.note
    assert fill.GlobalId  # the fill relationship survived the re-hosting


def test_csg_difference_reads_first_operand():
    """A DIFFERENCE result bounds by its first operand: the subtracted piece
    can only shrink the body, so a second operand poking out is ignored."""
    b = IfcBuilder()
    w = b.wall("W1", (0, 0), (4, 0), thickness=0.2, height=3.0)
    f = b.f

    def extrusion(xdim, ox):
        prof = f.create_entity(
            "IfcRectangleProfileDef",
            ProfileType="AREA",
            XDim=xdim,
            YDim=0.2,
            Position=f.create_entity("IfcAxis2Placement2D", Location=_pt(f, (ox, 0.0))),
        )
        return f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=prof,
            Position=f.create_entity("IfcAxis2Placement3D", Location=_pt(f, (0, 0, 0))),
            ExtrudedDirection=_dir(f, (0, 0, 1)),
            Depth=3.0,
        )

    clip = f.create_entity(
        "IfcBooleanClippingResult",
        Operator="DIFFERENCE",
        FirstOperand=extrusion(4.0, 2.0),
        # the notch cuts past the wall's end: first-operand extents must win
        SecondOperand=extrusion(1.0, 4.4),
    )
    csg = f.create_entity("IfcCsgSolid", TreeRootExpression=clip)
    _set_bodies(f, w, [csg], "CSG")
    assert _mapped_body_extents(w, 1.0) == pytest.approx((4.0, 0.2, 3.0), abs=1e-6)
    verts = _body_vertices(w)
    assert max(verts[0::3]) == pytest.approx(4.0, abs=1e-6)


def test_kernel_and_unwrap_agree_on_real_mapped_file():
    """The unwrap matches ifcopenshell.geom on real esplanades entities
    (mapped IfcFacetedBrep walls and windows) when the kernel succeeds."""
    from pathlib import Path

    import ifcopenshell.geom as geom

    path = Path.home() / "workspace" / "datasets" / "matchline-bench" / "esplanades" / "arch.ifc"
    if not path.exists():
        pytest.skip("esplanades benchmark file not present")
    f = ifcopenshell.open(str(path))
    # the kernel reports geometry in project SI units; the unwrap in file units
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    checked = 0
    for cls in ("IfcWall", "IfcWindow"):
        for el in f.by_type(cls)[:5]:
            try:
                v = geom.create_shape(geom.settings(), el).geometry.verts
            except RuntimeError:
                continue
            if not v:
                continue
            kernel = (
                min(v[0::3]),
                max(v[0::3]),
                min(v[1::3]),
                max(v[1::3]),
                min(v[2::3]),
                max(v[2::3]),
            )
            verts = _body_vertices(el)
            unwrap = (
                min(verts[0::3]),
                max(verts[0::3]),
                min(verts[1::3]),
                max(verts[1::3]),
                min(verts[2::3]),
                max(verts[2::3]),
            )
            assert all(abs(a - b * scale) < 0.002 for a, b in zip(kernel, unwrap)), cls
            checked += 1
    assert checked >= 2
