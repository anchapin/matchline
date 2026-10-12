"""Defect injection for IFC import parity tests (#578).

Takes the exporter-built fixture from ``tests.test_ifc_import`` and rewrites
its 20 m south wall (``Wall-1``) the way other authoring tools sometimes
export it: as several collinear fragments, fragments of different height,
or with a window doubled on top of itself. Modelled on the defect cases in
Pascal's ``cleanup.test.ts`` (MIT, Copyright (c) 2026 Pascal Group Inc.,
commit 67f8041); no code is ported, only the idea.
"""

from __future__ import annotations

import re

import ifcopenshell
import ifcopenshell.api.root as root
import ifcopenshell.api.spatial as sp
import ifcopenshell.guid as guid

from tests.test_ifc_closet_merge import _place
from tests.test_ifc_import import H, _add_solid, _rect_profile, make_ifc_fixture

T = 0.2  # fixture wall thickness


def _body(f):
    return [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]


def _south(f):
    return next(w for w in f.by_type("IfcWall") if w.Name == "Wall-1")


def fragment_south_wall(path, cuts, heights=None):
    """Replace Wall-1 by collinear fragments ``[cuts[k], cuts[k+1]]`` (m,
    along the wall from its origin), each with its own height. Openings move
    to the fragment that holds them, keeping their plan position; the
    fragments share Wall-1's material layer set."""
    f = ifcopenshell.open(str(path))
    wall = _south(f)
    storey = f.by_type("IfcBuildingStorey")[0]
    body = _body(f)
    loc = wall.ObjectPlacement.RelativePlacement.Location.Coordinates
    ref = wall.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios
    mat = next(
        r.RelatingMaterial for r in wall.HasAssociations if r.is_a("IfcRelAssociatesMaterial")
    )
    heights = heights or [H] * (len(cuts) - 1)
    frags = []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        fr = f.create_entity("IfcWall", GlobalId=guid.new(), Name=f"Wall-1.{k + 1}")
        start = (loc[0] + ref[0] * a, loc[1] + ref[1] * a, loc[2])
        fr.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        # exporter bodies sit on the wall's left side: local y in [0, T]
        _add_solid(f, fr, _rect_profile(f, b - a, T, ox=(b - a) / 2, oy=T / 2), heights[k], body)
        frags.append((a, b, fr))
    for rel in list(wall.HasOpenings):
        op = rel.RelatedOpeningElement
        pl = op.ObjectPlacement.RelativePlacement.Location
        x = float(pl.Coordinates[0])
        a, _b, fr = next(t for t in frags if t[0] <= x < t[1])
        pl.Coordinates = (x - a, *[float(v) for v in pl.Coordinates[1:]])
        op.ObjectPlacement.PlacementRelTo = fr.ObjectPlacement
        rel.RelatingBuildingElement = fr
    f.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=guid.new(),
        RelatingMaterial=mat,
        RelatedObjects=tuple(t[2] for t in frags),
    )
    sp.assign_container(f, products=[t[2] for t in frags], relating_structure=storey)
    root.remove_product(f, product=wall)
    f.write(str(path))
    return path


def double_a_window(path, shift=0.02):
    """Add a second copy of Wall-1's window ``shift`` m along the wall from
    the first: its own opening, fill and void/fill relations."""
    f = ifcopenshell.open(str(path))
    wall = _south(f)
    body = _body(f)
    rel = next(r for r in wall.HasOpenings if r.RelatedOpeningElement.Name == "A opening")
    op = rel.RelatedOpeningElement
    fill = op.HasFillings[0].RelatedBuildingElement
    x, y, z = [float(v) for v in op.ObjectPlacement.RelativePlacement.Location.Coordinates]
    m = re.search(r"([\d.]+)x([\d.]+)\s*m\)", fill.Name or "")
    w, h = float(m.group(1)), float(m.group(2))
    op2 = f.create_entity("IfcOpeningElement", GlobalId=guid.new(), Name=op.Name)
    op2.ObjectPlacement = _place(f, (x + shift, y, z), wall.ObjectPlacement)
    _add_solid(f, op2, _rect_profile(f, w, T), h, body)  # centred on its placement
    fill2 = f.create_entity(
        fill.is_a(),
        GlobalId=guid.new(),
        Name=fill.Name,
    )
    fill2.ObjectPlacement = _place(f, (0, 0, 0), op2.ObjectPlacement)
    _add_solid(f, fill2, _rect_profile(f, w, 0.04), h, body)
    f.create_entity(
        "IfcRelVoidsElement",
        GlobalId=guid.new(),
        RelatingBuildingElement=wall,
        RelatedOpeningElement=op2,
    )
    f.create_entity(
        "IfcRelFillsElement",
        GlobalId=guid.new(),
        RelatingOpeningElement=op2,
        RelatedBuildingElement=fill2,
    )
    for c in fill.ContainedInStructure:
        sp.assign_container(f, products=[fill2], relating_structure=c.RelatingStructure)
    f.write(str(path))
    return path


def fixture(path):
    return make_ifc_fixture(path)
