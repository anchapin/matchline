"""Authored IsExternal keeps a wall in the envelope when the space probe is fooled (#1219).

Tier 1 classifies a wall with spaces on both sides as an interior
partition and drops it. Space polygons can bleed past a perimeter wall's
outer face and an atrium can be modelled as a space, so a wall the pset
explicitly calls IsExternal stays in the envelope, facade left for
review rather than guessed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api  # noqa: E402

from ifc.ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder  # noqa: E402


def _two_rooms(path, mark_external=False):
    """Two rooms sharing the partition wall MID on the x=6 line."""
    b = IfcBuilder()
    b.space("ROOM A", [(0, 0), (6, 0), (6, 4), (0, 4)])
    b.space("ROOM B", [(6, 0), (12, 0), (12, 4), (6, 4)])
    b.wall("S1", (0, 0), (6, 0))
    b.wall("E", (12, 0), (12, 4))
    b.wall("N1", (6, 4), (0, 4))
    b.wall("W", (0, 4), (0, 0))
    b.wall("S2", (6, 0), (12, 0))
    b.wall("N2", (12, 4), (6, 4))
    mid = b.wall("MID", (6, 0), (6, 4))
    if mark_external:
        pset = ifcopenshell.api.run("pset.add_pset", b.f, product=mid, name="Pset_WallCommon")
        ifcopenshell.api.run("pset.edit_pset", b.f, pset=pset, properties={"IsExternal": True})
    return b.write(path)


def _mid_wall(model):
    for w in model.envelope:
        (x0, _), (x1, _) = w.from_m, w.to_m
        if abs(x0 - 6.0) < 1e-6 and abs(x1 - 6.0) < 1e-6:
            return w
    return None


def test_pset_exterior_wall_kept_when_both_sides_show_spaces(tmp_path):
    p = _two_rooms(tmp_path / "a.ifc", mark_external=True)
    model = import_ifc(str(p))
    wall = _mid_wall(model)
    assert wall is not None, "a pset-exterior wall must stay in the envelope"
    assert wall.facade == ""
    assert "pset IsExternal" in wall.provenance.note
    assert any(r.kind == "facade_unclear" for r in model.review_queue)


def test_no_pset_wall_still_dropped_as_interior(tmp_path):
    p = _two_rooms(tmp_path / "b.ifc")
    model = import_ifc(str(p))
    assert _mid_wall(model) is None, "a partition without the pset still leaves the envelope"
    # its BimElement stays in the takeoff inventory
    assert any(be.name == "MID" for be in model.bim_elements)


def _two_rooms_detached(path, mark_external=False):
    """The two-room plan plus a freestanding wall no space sits beside."""
    b = IfcBuilder()
    b.space("ROOM A", [(0, 0), (6, 0), (6, 4), (0, 4)])
    b.space("ROOM B", [(6, 0), (12, 0), (12, 4), (6, 4)])
    b.wall("S1", (0, 0), (6, 0))
    b.wall("E", (12, 0), (12, 4))
    b.wall("N1", (6, 4), (0, 4))
    b.wall("W", (0, 4), (0, 0))
    b.wall("S2", (6, 0), (12, 0))
    b.wall("N2", (12, 4), (6, 4))
    mid = b.wall("MID", (6, 0), (6, 4))
    far = b.wall("FAR", (14, 0), (14, 4))
    if mark_external:
        pset = ifcopenshell.api.run("pset.add_pset", b.f, product=far, name="Pset_WallCommon")
        ifcopenshell.api.run("pset.edit_pset", b.f, pset=pset, properties={"IsExternal": True})
    return b.write(path), mid is not None


def _far_wall(model):
    for w in model.envelope:
        (x0, _), (x1, _) = w.from_m, w.to_m
        if abs(x0 - 14.0) < 1e-6 and abs(x1 - 14.0) < 1e-6:
            return w
    return None


def test_unflagged_wall_no_space_sides_not_counted_as_exterior(tmp_path):
    p, _ = _two_rooms_detached(tmp_path / "c.ifc", mark_external=False)
    model = import_ifc(str(p))
    wall = _far_wall(model)
    assert wall is not None, "the wall stays in the model for review"
    assert wall.provenance.method == "ifc_import:tier1:unclassified"
    assert "not counted as exterior envelope length" in wall.provenance.note
    assert any(r.kind == "facade_unclear" for r in model.review_queue)


def test_pset_external_wall_unclassified_keeps_tier0_method(tmp_path):
    p, _ = _two_rooms_detached(tmp_path / "d.ifc", mark_external=True)
    model = import_ifc(str(p))
    wall = _far_wall(model)
    assert wall is not None
    assert wall.provenance.method == "ifc_import:tier0:envelope"
    assert "pset IsExternal" in wall.provenance.note
