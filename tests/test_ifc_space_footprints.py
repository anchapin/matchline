"""Space footprints beyond arbitrary closed profiles (#1204).

IfcRectangleProfileDef swept areas and IfcFaceBasedSurfaceModel body items
carry no walkable outer curve, so 13 of 21 duplex spaces used to come out
with no footprint and their pairings scrambled (area err 109.7%). The rect
profile's dims + Position build the rectangle; the face set's vertices build
a plan hull.
"""

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.guid as _guid  # noqa: E402

from ifc.ifc_import import (  # noqa: E402
    _face_set_footprint,
    _rect_profile_footprint,
    import_ifc,
)
from tests.test_ifc_import import _add_solid, _placement, _rect_profile  # noqa: E402


def _file():
    f = ifcopenshell.file(schema_identifier="IFC4")
    org = f.create_entity("IfcOrganization", Name="MATCHLINE-TEST")
    person = f.create_entity("IfcPerson", Identification="t", FamilyName="Tester")
    pao = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = f.create_entity(
        "IfcApplication",
        ApplicationDeveloper=org,
        Version="1",
        ApplicationFullName="test",
        ApplicationIdentifier="test",
    )
    owner = f.create_entity(
        "IfcOwnerHistory",
        OwningUser=pao,
        OwningApplication=app,
        ChangeAction="NOCHANGE",
        CreationDate=0,
    )
    ctx = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-5,
        WorldCoordinateSystem=f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
        ),
    )
    body = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        ContextIdentifier="Body",
        ContextType="Model",
        ParentContext=ctx,
        TargetView="MODEL_VIEW",
    )
    length_unit = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    project = f.create_entity(
        "IfcProject",
        GlobalId=_guid.new(),
        OwnerHistory=owner,
        Name="PROJ",
        UnitsInContext=f.create_entity("IfcUnitAssignment", Units=(length_unit,)),
    )
    site = f.create_entity("IfcSite", GlobalId=_guid.new(), OwnerHistory=owner, Name="SITE")
    building = f.create_entity("IfcBuilding", GlobalId=_guid.new(), OwnerHistory=owner, Name="BLDG")
    storey = f.create_entity(
        "IfcBuildingStorey", GlobalId=_guid.new(), OwnerHistory=owner, Name="Level 1"
    )
    import ifcopenshell.api.aggregate as _Sp

    _Sp.assign_object(f, products=[site], relating_object=project)
    _Sp.assign_object(f, products=[building], relating_object=site)
    _Sp.assign_object(f, products=[storey], relating_object=building)
    return f, body, storey, owner


def _space(f, body, storey, owner, name):
    sp = f.create_entity("IfcSpace", GlobalId=_guid.new(), OwnerHistory=owner, Name=name)
    sp.ObjectPlacement = _placement(f, (0.0, 0.0, 0.0), storey.ObjectPlacement)
    import ifcopenshell.api.aggregate as _Sp

    _Sp.assign_object(f, products=[sp], relating_object=storey)
    return sp


def _set_solid_position(f, xyz=(0.0, 0.0, 0.0), refdir=None, axis=None):
    kw = {"Location": f.create_entity("IfcCartesianPoint", Coordinates=xyz)}
    if refdir is not None:
        kw["RefDirection"] = f.create_entity("IfcDirection", DirectionRatios=refdir)
    if axis is not None:
        kw["Axis"] = f.create_entity("IfcDirection", DirectionRatios=axis)
    solid = f.by_type("IfcExtrudedAreaSolid")[0]
    solid.Position = f.create_entity("IfcAxis2Placement3D", **kw)
    return solid


def test_rect_profile_footprint_honors_solid_position():
    f, body, storey, owner = _file()
    sp = _space(f, body, storey, owner, "ROOM 101")
    _add_solid(f, sp, _rect_profile(f, 6.0, 4.0, -3.0, -2.0), 3.0, body)
    # 90-degree solid rotation: XDim rides onto world Y
    _set_solid_position(f, xyz=(2.0, 1.0, 0.0), refdir=(0.0, 1.0, 0.0))
    fp = _rect_profile_footprint(f.by_type("IfcExtrudedAreaSolid")[0], 1.0)
    assert fp is not None
    pts, depth = fp
    assert abs(depth - 3.0) < 1e-6
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    # 90-degree rotation: profile Y rides onto world X (span 4, center 4),
    # profile X onto world Y (span 6, center -2)
    assert abs((max(xs) - min(xs)) - 4.0) < 1e-6 and abs((max(ys) - min(ys)) - 6.0) < 1e-6
    assert abs((max(xs) + min(xs)) / 2 - 4.0) < 1e-6 and abs((max(ys) + min(ys)) / 2 + 2.0) < 1e-6
    # end to end: the imported space gets the rectangle, not the quantity fallback
    path = "/tmp/_rect_space.ifc"
    f.write(path)
    model = import_ifc(path)
    room = next(iter(model.spaces.values()))
    assert abs(room.area_m2 - 24.0) < 1e-6
    assert len(room.polygon_m) == 4


def test_tilted_solid_degrades_to_quantity_fallback():
    f, body, storey, owner = _file()
    sp = _space(f, body, storey, owner, "ROOM 101")
    _add_solid(f, sp, _rect_profile(f, 6.0, 4.0, -3.0, -2.0), 3.0, body)
    _set_solid_position(f, axis=(0.0, 1.0, 0.0))
    assert _rect_profile_footprint(f.by_type("IfcExtrudedAreaSolid")[0], 1.0) is None
    path = "/tmp/_tilted_space.ifc"
    f.write(path)
    model = import_ifc(path)
    room = next(iter(model.spaces.values()))
    assert room.polygon_m == [] and room.area_m2 is None
    assert any(it.kind == "space_no_geometry" for it in model.review_queue)


def _area(ring):
    return (
        abs(
            sum(
                ring[i][0] * ring[(i + 1) % len(ring)][1]
                - ring[(i + 1) % len(ring)][0] * ring[i][1]
                for i in range(len(ring))
            )
        )
        / 2
    )


def test_face_set_footprint_is_the_floor_ring_not_a_hull():
    f, body, storey, owner = _file()
    sp = _space(f, body, storey, owner, "ROOM 102")
    # L-shaped prism, 4 m tall: L spans (0,0)-(4,4) minus the (2,2)-(4,4) notch
    outer = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4), (0, 0)]
    pts = [
        f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y), z))
        for z in (0.0, 4.0)
        for x, y in outer
    ]
    top = f.create_entity("IfcPolyLoop", Polygon=tuple(pts[:7]))
    bot = f.create_entity("IfcPolyLoop", Polygon=tuple(pts[7:]))
    loops = []
    side_pairs = list(zip(outer, outer[1:] + outer[:1]))
    for (x1, y1), (x2, y2) in side_pairs:
        quad = [
            f.create_entity("IfcCartesianPoint", Coordinates=(float(x1), float(y1), 0.0)),
            f.create_entity("IfcCartesianPoint", Coordinates=(float(x2), float(y2), 0.0)),
            f.create_entity("IfcCartesianPoint", Coordinates=(float(x2), float(y2), 4.0)),
            f.create_entity("IfcCartesianPoint", Coordinates=(float(x1), float(y1), 4.0)),
        ]
        loops.append(f.create_entity("IfcPolyLoop", Polygon=tuple(quad)))
    faces = []
    for loop in [top, bot] + loops:
        bound = f.create_entity("IfcFaceOuterBound", Bound=loop, Orientation=True)
        face = f.create_entity("IfcFace", Bounds=(bound,))
        faces.append(face)
    cfs = f.create_entity("IfcConnectedFaceSet", CfsFaces=tuple(faces))
    fbsm = f.create_entity("IfcFaceBasedSurfaceModel", FbsmFaces=(cfs,))
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(fbsm,),
    )
    from ifcopenshell.api import geometry as _Gm

    _Gm.assign_representation(f, product=sp, representation=rep)
    fp = _face_set_footprint(sp, 1.0)
    assert fp is not None
    # the floor's own ring, notch kept: 12, not the hull's 14 or the bbox's 16
    assert abs(_area(fp) - 12.0) < 1e-6
    path = "/tmp/_face_space.ifc"
    f.write(path)
    model = import_ifc(path)
    room = next(iter(model.spaces.values()))
    assert abs(room.area_m2 - 12.0) < 1e-6


def test_face_set_hole_in_the_top_face_does_not_disturb_the_floor():
    """Duplex hallway shape (#1218): stair hole in the ceiling, plain floor."""
    f, body, storey, owner = _file()
    sp = _space(f, body, storey, owner, "ROOM 102")
    outer = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4), (0, 0)]

    def point(x, y, z):
        return f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y), z))

    def loop(coords):
        return f.create_entity("IfcPolyLoop", Polygon=tuple(coords))

    top_outer = loop([point(x, y, 4.0) for x, y in outer])
    top_hole = loop(
        [point(2.5, 2.5, 4.0), point(3.5, 2.5, 4.0), point(3.5, 3.5, 4.0), point(2.5, 3.5, 4.0)]
    )
    bot = loop([point(x, y, 0.0) for x, y in outer])
    faces = []
    for bounds in ((top_outer, top_hole), (bot,)):
        bound_entities = tuple(
            f.create_entity("IfcFaceOuterBound", Bound=b, Orientation=True) for b in bounds
        )
        faces.append(f.create_entity("IfcFace", Bounds=bound_entities))
    for (x1, y1), (x2, y2) in list(zip(outer, outer[1:] + outer[:1])):
        quad = loop(
            [point(x1, y1, 0.0), point(x2, y2, 0.0), point(x2, y2, 4.0), point(x1, y1, 4.0)]
        )
        bound = f.create_entity("IfcFaceOuterBound", Bound=quad, Orientation=True)
        faces.append(f.create_entity("IfcFace", Bounds=(bound,)))
    cfs = f.create_entity("IfcConnectedFaceSet", CfsFaces=tuple(faces))
    fbsm = f.create_entity("IfcFaceBasedSurfaceModel", FbsmFaces=(cfs,))
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(fbsm,),
    )
    from ifcopenshell.api import geometry as _Gm

    _Gm.assign_representation(f, product=sp, representation=rep)
    fp = _face_set_footprint(sp, 1.0)
    assert fp is not None
    assert abs(_area(fp) - 12.0) < 1e-6  # the plain floor ring, ceiling hole ignored


def test_face_set_hole_in_the_floor_falls_back_to_the_hull():
    f, body, storey, owner = _file()
    sp = _space(f, body, storey, owner, "ROOM 102")
    outer = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4), (0, 0)]

    def point(x, y, z):
        return f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y), z))

    def loop(coords):
        return f.create_entity("IfcPolyLoop", Polygon=tuple(coords))

    top = loop([point(x, y, 4.0) for x, y in outer])
    floor_outer = loop([point(x, y, 0.0) for x, y in outer])
    floor_hole = loop(
        [point(0.5, 0.5, 0.0), point(1.5, 0.5, 0.0), point(1.5, 1.5, 0.0), point(0.5, 1.5, 0.0)]
    )
    faces = []
    for bounds in ((top,), (floor_outer, floor_hole)):
        bound_entities = tuple(
            f.create_entity("IfcFaceOuterBound", Bound=b, Orientation=True) for b in bounds
        )
        faces.append(f.create_entity("IfcFace", Bounds=bound_entities))
    for (x1, y1), (x2, y2) in list(zip(outer, outer[1:] + outer[:1])):
        quad = loop(
            [point(x1, y1, 0.0), point(x2, y2, 0.0), point(x2, y2, 4.0), point(x1, y1, 4.0)]
        )
        bound = f.create_entity("IfcFaceOuterBound", Bound=quad, Orientation=True)
        faces.append(f.create_entity("IfcFace", Bounds=(bound,)))
    cfs = f.create_entity("IfcConnectedFaceSet", CfsFaces=tuple(faces))
    fbsm = f.create_entity("IfcFaceBasedSurfaceModel", FbsmFaces=(cfs,))
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(fbsm,),
    )
    from ifcopenshell.api import geometry as _Gm

    _Gm.assign_representation(f, product=sp, representation=rep)
    fp = _face_set_footprint(sp, 1.0)
    assert fp is not None
    assert abs(_area(fp) - 14.0) < 1e-6  # the hull stands in when the floor has a hole
