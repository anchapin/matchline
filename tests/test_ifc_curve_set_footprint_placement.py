"""Curve-set space footprints honour the space's placement chain (#1229).

The FootPrint representation's polylines are space-local; the import used to
return them raw, parking every polygon at the space-local origin while walls
sit in the world frame, so Tier 1 found no spaces to probe (esplanades:
0 of 212 walls classified despite 285 valid footprints).
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

from ifc.ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402

_ROOM_A = [(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)]
_ROOM_B = [(6.0, 0.0), (12.0, 0.0), (12.0, 4.0), (6.0, 4.0)]


def _curve_set_two_rooms(path, offset=(100.0, 50.0), refdir=None):
    """Two rooms sharing partition MID, each space a space-local curve set.

    The Body representation goes away (the solid path would transform
    correctly on its own) and the storey placement moves by ``offset`` --
    rotating when ``refdir`` says so -- so the raw polylines only reach the
    walls' world frame once the placement chain is composed.
    """
    b = IfcBuilder()
    b.space("ROOM A", _ROOM_A)
    b.space("ROOM B", _ROOM_B)
    b.wall("S1", (0, 0), (6, 0))
    b.wall("E", (12, 0), (12, 4))
    b.wall("N1", (6, 4), (0, 4))
    b.wall("W", (0, 4), (0, 0))
    b.wall("S2", (6, 0), (12, 0))
    b.wall("N2", (12, 4), (6, 4))
    b.wall("MID", (6, 0), (6, 4))
    outlines = {"ROOM A": _ROOM_A, "ROOM B": _ROOM_B}
    for sp in b.f.by_type("IfcSpace"):
        sp.Representation.Representations = [
            r for r in sp.Representation.Representations if r.RepresentationIdentifier != "Body"
        ]
        pts = outlines[sp.Name] + [outlines[sp.Name][0]]
        poly = b.f.create_entity("IfcPolyline", Points=[b._pt(p) for p in pts])
        cs = b.f.create_entity("IfcGeometricCurveSet", Elements=[poly])
        rep = b.f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=b.body,
            RepresentationIdentifier="FootPrint",
            RepresentationType="GeometricCurveSet",
            Items=[cs],
        )
        sp.Representation.Representations = tuple(sp.Representation.Representations) + (rep,)
    rp = b.storey.ObjectPlacement.RelativePlacement
    rp.Location = b._pt((offset[0], offset[1], 0.0))
    if refdir is not None:
        rp.RefDirection = b._dir(refdir)
    return b.write(path)


def test_curve_set_footprint_follows_placement_chain(tmp_path):
    p = _curve_set_two_rooms(tmp_path / "a.ifc")
    model = import_ifc(str(p))
    # offset (100, 50) alone: the plan lands there, not at the origin
    want = {(100.0, -50.0), (106.0, -50.0), (106.0, -54.0), (100.0, -54.0)}
    got = [
        sp
        for sp in model.spaces.values()
        if {(round(x, 1), round(y, 1)) for x, y in sp.polygon_m} == want
    ]
    assert got, "the curve-set footprint must be placed by the placement chain"


def test_tier1_classifies_when_spaces_are_curve_set(tmp_path):
    p = _curve_set_two_rooms(tmp_path / "b.ifc", refdir=(0.0, 1.0, 0.0))
    model = import_ifc(str(p))
    # 90-degree storey rotation + offset: canonical MID line sits at x=106
    mids = [
        w
        for w in model.envelope
        if abs(w.from_m[0] - 106.0) < 1e-6 and abs(w.to_m[0] - 106.0) < 1e-6
    ]
    assert mids == [], "a real interior partition leaves the envelope"
    assert [b for b in model.bim_elements if b.name == "MID"], "BimElement stays"
    west = [w for w in model.envelope if abs(w.from_m[0] - 100.0) < 1e-6]
    assert west and all(w.facade for w in west), "a perimeter wall gets a facade"
    assert "tier1:facade" in west[0].provenance.method


def test_export_writes_footprint_points_space_local(tmp_path):
    """The exporter must not undo the reader's fix (#1229).

    ``write_ifc4`` used to write the FootPrint polyline in world coordinates
    while also placing the space at its centroid, so a conformant reader
    composed the placement a second time and the exported file lost its
    openings on re-import (42 round-trip tests failed). The points are
    space-local now, and the round trip preserves the polygon exactly.
    """
    b = IfcBuilder()
    box_plan(b)
    m1 = import_ifc(b.write(tmp_path / "a.ifc"))
    import ifcopenshell  # noqa: E402

    from ifc.ifc_export import _export_ifc  # noqa: E402

    out = tmp_path / "b.ifc"
    _export_ifc(m1, out)
    f = ifcopenshell.open(str(out))
    checked = 0
    for sp in f.by_type("IfcSpace"):
        for shape_rep in getattr(sp.Representation, "Representations", None) or []:
            if shape_rep.RepresentationIdentifier != "FootPrint":
                continue
            for item in shape_rep.Items:
                for curve in item.Elements:
                    pts = [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in curve.Points]
                    assert len(pts) >= 3
                    cx = sum(x for x, _ in pts) / len(pts)
                    cy = sum(y for _, y in pts) / len(pts)
                    assert abs(cx) < 1e-6 and abs(cy) < 1e-6, (
                        "FootPrint points must be relative to the space's own placement"
                    )
                    checked += 1
    assert checked >= 1, "the export writes a FootPrint curve set"
    m2 = import_ifc(str(out))
    got = {s.id: [(round(x, 6), round(y, 6)) for x, y in s.polygon_m] for s in m2.spaces.values()}
    want = {s.id: [(round(x, 6), round(y, 6)) for x, y in s.polygon_m] for s in m1.spaces.values()}
    assert got == want
    assert {s.id: sorted(o.id for o in s.openings) for s in m2.spaces.values()} == {
        s.id: sorted(o.id for o in s.openings) for s in m1.spaces.values()
    }
