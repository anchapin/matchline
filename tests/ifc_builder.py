"""Tiny IFC4 files per test (#587).

``IfcBuilder`` writes the minimum the importer needs, in metres: one
storey, spaces as extruded footprints, walls as extruded rectangles along a
start->end axis (optionally with a layer set and its usage), doors and
windows in walls (opening + fill, voids/fills relationships, OperationType),
slabs and ceiling coverings. Coordinates are IFC plan coordinates (x east,
y north). ``mirror_x=True`` negates every x it is given, which builds the
mirror image of the same plan.

Idea from Pascal's tests/ifc-builder.ts (MIT, Copyright (c) 2026 Pascal
Group Inc., commit 67f8041); written for IfcOpenShell, no code ported.
"""

from __future__ import annotations

import math

import ifcopenshell
import ifcopenshell.api.aggregate as _Ag
import ifcopenshell.api.context as _Ctx
import ifcopenshell.api.geometry as _Gm
import ifcopenshell.api.root as _Root
import ifcopenshell.api.spatial as _Sp
import ifcopenshell.api.unit as _Un
import ifcopenshell.guid as _guid


class IfcBuilder:
    def __init__(self, mirror_x=False, storey_elevation=0.0):
        self.mx = -1.0 if mirror_x else 1.0
        self._axes = {}  # wall entity id -> (start, end, thickness), builder frame
        f = self.f = ifcopenshell.file(schema="IFC4")
        project = _Root.create_entity(f, ifc_class="IfcProject", name="Builder")
        _Un.assign_unit(
            f,
            units=[
                _Un.add_si_unit(f, unit_type="LENGTHUNIT"),
                _Un.add_si_unit(f, unit_type="AREAUNIT"),
                _Un.add_si_unit(f, unit_type="VOLUMEUNIT"),
            ],
        )
        model = _Ctx.add_context(f, context_type="Model")
        self.body = _Ctx.add_context(
            f,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
            parent=model,
        )
        site = _Root.create_entity(f, ifc_class="IfcSite", name="Site")
        bldg = _Root.create_entity(f, ifc_class="IfcBuilding", name="Building")
        self.storey = _Root.create_entity(f, ifc_class="IfcBuildingStorey", name="Level 1")
        self.storey.Elevation = float(storey_elevation)
        _Ag.assign_object(f, products=[site], relating_object=project)
        _Ag.assign_object(f, products=[bldg], relating_object=site)
        _Ag.assign_object(f, products=[self.storey], relating_object=bldg)
        root = self._placement((0, 0, 0), None)
        site.ObjectPlacement = root
        bldg.ObjectPlacement = self._placement((0, 0, 0), root)
        self.storey.ObjectPlacement = self._placement(
            (0, 0, storey_elevation), bldg.ObjectPlacement, mirror=False
        )

    # -- primitives -----------------------------------------------------
    def _pt(self, p):
        return self.f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in p))

    def _dir(self, d):
        return self.f.create_entity("IfcDirection", DirectionRatios=tuple(float(v) for v in d))

    def _placement(self, xyz, parent, refdir=None, mirror=True):
        x, y, z = xyz
        if mirror:
            x = self.mx * x
        kw = {"Location": self._pt((x, y, z))}
        if refdir is not None:
            kw["Axis"] = self._dir((0, 0, 1))
            kw["RefDirection"] = self._dir(refdir)
        ax = self.f.create_entity("IfcAxis2Placement3D", **kw)
        return self.f.create_entity(
            "IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=ax
        )

    def _solid(self, product, profile, depth):
        solid = self.f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=self.f.create_entity("IfcAxis2Placement3D", Location=self._pt((0, 0, 0))),
            ExtrudedDirection=self._dir((0, 0, 1)),
            Depth=float(depth),
        )
        rep = self.f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=self.body,
            RepresentationIdentifier="Body",
            RepresentationType="SweptSolid",
            Items=(solid,),
        )
        _Gm.assign_representation(self.f, product=product, representation=rep)

    def _outline(self, outline):
        pts = [(self.mx * x, y) for x, y in outline]
        if self.mx < 0:
            pts = pts[::-1]  # keep the profile counterclockwise
        poly = self.f.create_entity("IfcPolyline", Points=[self._pt(p) for p in pts + [pts[0]]])
        return self.f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly
        )

    def _rect(self, xdim, ydim, ox, oy):
        return self.f.create_entity(
            "IfcRectangleProfileDef",
            ProfileType="AREA",
            XDim=float(xdim),
            YDim=float(ydim),
            Position=self.f.create_entity("IfcAxis2Placement2D", Location=self._pt((ox, oy))),
        )

    def _contain(self, el):
        _Sp.assign_container(self.f, products=[el], relating_structure=self.storey)

    # -- elements -------------------------------------------------------
    def space(self, name, outline, height=3.0, long_name=None):
        sp = _Root.create_entity(self.f, ifc_class="IfcSpace", name=name)
        if long_name is not None:
            sp.LongName = long_name
        sp.ObjectPlacement = self._placement((0, 0, 0), self.storey.ObjectPlacement)
        self._solid(sp, self._outline(outline), height)
        _Ag.assign_object(self.f, products=[sp], relating_object=self.storey)
        return sp

    def wall(self, name, start, end, thickness=0.2, height=3.0, layers=None):
        """Wall body centred on the start->end axis. ``layers``: [(material, t)]."""
        (x0, y0), (x1, y1) = start, end
        L = math.hypot(x1 - x0, y1 - y0)
        d = (self.mx * (x1 - x0) / L, (y1 - y0) / L, 0.0)
        w = _Root.create_entity(self.f, ifc_class="IfcWall", name=name)
        w.ObjectPlacement = self._placement((x0, y0, 0), self.storey.ObjectPlacement, refdir=d)
        self._solid(w, self._rect(L, thickness, L / 2, 0.0), height)
        self._contain(w)
        if layers:
            mls = self.f.create_entity(
                "IfcMaterialLayerSet",
                MaterialLayers=[
                    self.f.create_entity(
                        "IfcMaterialLayer",
                        Material=self.f.create_entity("IfcMaterial", Name=m),
                        LayerThickness=float(t),
                    )
                    for m, t in layers
                ],
            )
            self.f.create_entity(
                "IfcRelAssociatesMaterial",
                GlobalId=_guid.new(),
                RelatedObjects=[w],
                RelatingMaterial=mls,
            )
        self._axes[w.id()] = (start, end, thickness)
        return w

    def opening(self, wall, along, width, height, sill=0.0, kind="IfcDoor", operation_type=None):
        """A door or window centred ``along`` metres from the wall's start."""
        start, end, t = self._axes[wall.id()]
        (x0, y0), (x1, y1) = start, end
        L = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        cx, cy = x0 + ux * along, y0 + uy * along
        d = (self.mx * ux, uy, 0.0)
        op = _Root.create_entity(self.f, ifc_class="IfcOpeningElement", name=f"{wall.Name} opening")
        op.ObjectPlacement = self._placement((cx, cy, sill), self.storey.ObjectPlacement, refdir=d)
        self._solid(op, self._rect(width, t, 0.0, 0.0), height)
        fill = _Root.create_entity(self.f, ifc_class=kind, name=f"{kind[3:]} ({width}x{height} m)")
        fill.ObjectPlacement = self._placement(
            (cx, cy, sill), self.storey.ObjectPlacement, refdir=d
        )
        self._solid(fill, self._rect(width, 0.04, 0.0, 0.0), height)
        fill.OverallWidth, fill.OverallHeight = float(width), float(height)
        if operation_type is not None and kind == "IfcDoor":
            fill.OperationType = operation_type
        self._contain(fill)
        self.f.create_entity(
            "IfcRelVoidsElement",
            GlobalId=_guid.new(),
            RelatingBuildingElement=wall,
            RelatedOpeningElement=op,
        )
        self.f.create_entity(
            "IfcRelFillsElement",
            GlobalId=_guid.new(),
            RelatingOpeningElement=op,
            RelatedBuildingElement=fill,
        )
        return fill

    def slab(self, name, outline, z=-0.2, thickness=0.2, predefined="FLOOR"):
        s = _Root.create_entity(self.f, ifc_class="IfcSlab", name=name)
        s.PredefinedType = predefined
        s.ObjectPlacement = self._placement((0, 0, z), self.storey.ObjectPlacement)
        self._solid(s, self._outline(outline), thickness)
        self._contain(s)
        return s

    def covering(self, name, outline, z, thickness=0.02, predefined="CEILING"):
        c = _Root.create_entity(self.f, ifc_class="IfcCovering", name=name)
        c.PredefinedType = predefined
        c.ObjectPlacement = self._placement((0, 0, z), self.storey.ObjectPlacement)
        self._solid(c, self._outline(outline), thickness)
        self._contain(c)
        return c

    def roof_slab(self, name, corners, thickness=0.2, predefined="ROOF", container=True):
        """A planar roof slab whose top face has ``corners`` (x, y, z above the
        storey, builder frame), extruded ``thickness`` down along its normal,
        the way authoring tools model a pitched roof. Not mirrored."""
        import math

        c = [tuple(float(v) for v in p) for p in corners]
        n = [0.0, 0.0, 0.0]
        for i, a in enumerate(c):
            b = c[(i + 1) % len(c)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        if n[2] < 0:
            c, n = c[::-1], [-x for x in n]
        ln = math.sqrt(sum(x * x for x in n))
        n = [x / ln for x in n]
        e = [c[1][k] - c[0][k] for k in range(3)]
        el = math.sqrt(sum(x * x for x in e))
        xd = [x / el for x in e]
        yd = [n[1] * xd[2] - n[2] * xd[1], n[2] * xd[0] - n[0] * xd[2], n[0] * xd[1] - n[1] * xd[0]]
        loc = [[sum((p[k] - c[0][k]) * d[k] for k in range(3)) for d in (xd, yd)] for p in c]
        s = _Root.create_entity(self.f, ifc_class="IfcSlab", name=name)
        s.PredefinedType = predefined
        s.ObjectPlacement = self._placement((0, 0, 0), self.storey.ObjectPlacement, mirror=False)
        poly = self.f.create_entity("IfcPolyline", Points=[self._pt(p) for p in loc + [loc[0]]])
        prof = self.f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly
        )
        pos = self.f.create_entity(
            "IfcAxis2Placement3D",
            Location=self._pt(c[0]),
            Axis=self._dir(n),
            RefDirection=self._dir(xd),
        )
        solid = self.f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=prof,
            Position=pos,
            ExtrudedDirection=self._dir((0, 0, -1)),
            Depth=float(thickness),
        )
        rep = self.f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=self.body,
            RepresentationIdentifier="Body",
            RepresentationType="SweptSolid",
            Items=(solid,),
        )
        _Gm.assign_representation(self.f, product=s, representation=rep)
        if container:
            self._contain(s)
        return s

    def roof(self, name, slabs):
        """An IfcRoof aggregating ``slabs`` (contained in the storey itself)."""
        r = _Root.create_entity(self.f, ifc_class="IfcRoof", name=name)
        r.ObjectPlacement = self._placement((0, 0, 0), self.storey.ObjectPlacement, mirror=False)
        _Ag.assign_object(self.f, products=list(slabs), relating_object=r)
        self._contain(r)
        return r

    def write(self, path):
        self.f.write(str(path))
        return path


def box_plan(b, w=10.0, d=6.0, t=0.2, east_window=True):
    """One room ``w`` x ``d`` with four walls on its edges (axes on the room
    boundary), a door 1 m along the south wall, and a window on the east
    wall when ``east_window``. Returns the walls by facade."""
    b.space("ROOM 101", [(0, 0), (w, 0), (w, d), (0, d)])
    walls = {
        "south": b.wall("S", (0, 0), (w, 0), t),
        "east": b.wall("E", (w, 0), (w, d), t),
        "north": b.wall("N", (w, d), (0, d), t),
        "west": b.wall("W", (0, d), (0, 0), t),
    }
    b.opening(walls["south"], 1.0, 0.9, 2.1, operation_type="SINGLE_SWING_LEFT")
    if east_window:
        b.opening(walls["east"], 3.0, 2.0, 1.5, sill=0.9, kind="IfcWindow")
    return walls
