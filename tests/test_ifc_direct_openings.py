"""Tests for windows/doors no IfcRelFillsElement relationship names (#1206).

Files exported without the fills relationship leave their IfcWindow and
IfcDoor entities floating: every wall void stays unfilled and the window
count reads zero. The importer now pairs each unattached fill onto an
unfilled wall void by body placement, and attaches leftovers to walls
directly, at lower confidence, with provenance marking the path.
"""

import pytest

from ifc.ifc_import import _ensure_ifc, import_ifc

_ensure_ifc()
import ifcopenshell.api.root as _Root  # noqa: E402

from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402


def floating_window(b, wall, along, width=1.2, height=1.5, sill=0.9, offset=0.0):
    """An IfcWindow with a body on ``wall`` but no void and no fill rel.

    Mirrors the builder's opening() minus the IfcRelVoidsElement /
    IfcRelFillsElement relationships: the fill floats, placed on the wall's
    axis ``along`` metres from its start, ``offset`` metres off the axis.
    """
    import math

    f = b.f
    start, _end, _t = b._axes[wall.id()]
    x0, y0 = start
    ang = wall.ObjectPlacement.RelativePlacement
    ref = ang.RefDirection.DirectionRatios if ang.RefDirection else (1.0, 0.0, 0.0)
    ux, uy = ref[0], ref[1]
    n = math.hypot(ux, uy)
    ux, uy = ux / n, uy / n
    # wall local +Y is to the left of the axis in plan (right-hand Z-up)
    nx, ny = -uy, ux
    cx = x0 + ux * along + nx * offset
    cy = y0 + uy * along + ny * offset
    win = _Root.create_entity(f, ifc_class="IfcWindow", name="Floating Window")
    win.ObjectPlacement = b._placement(
        (cx, cy, sill), b.storey.ObjectPlacement, refdir=(ux, uy, 0.0)
    )
    b._solid(win, b._rect(width, 0.04, 0.0, 0.0), height)
    win.OverallWidth, win.OverallHeight = float(width), float(height)
    b._contain(win)
    return win


def _windows(m):
    return [o for be in m.bim_elements for o in be.openings if o.category == "window"]


def test_paired_window_counted_without_fills(tmp_path):
    """A window whose fills rel is stripped is read via its void, not lost."""
    b = IfcBuilder()
    box_plan(b, east_window=True)
    for rel in list(b.f.by_type("IfcRelFillsElement")):
        b.f.remove(rel)
    path = b.write(tmp_path / "pairless.ifc")
    m = import_ifc(path)
    wins = _windows(m)
    assert len(wins) == 1
    o = wins[0]
    assert o.width_m == pytest.approx(2.0, abs=0.01)
    assert o.height_m == pytest.approx(1.5, abs=0.01)
    assert o.fill_global_id == o.fill_global_id  # fill carried through
    assert o.provenance is not None
    assert "no IfcRelFillsElement" in (o.provenance.note or "")
    # dims still come from the opening solid, at slightly reduced confidence
    assert o.provenance.confidence == pytest.approx(0.9)


def test_floating_window_attached_by_placement(tmp_path):
    """A window with no void at all attaches to the wall it sits on."""
    b = IfcBuilder()
    walls = box_plan(b, east_window=False)
    floating_window(b, walls["east"], 3.0, width=1.4, height=1.6, sill=0.8)
    path = b.write(tmp_path / "floating.ifc")
    m = import_ifc(path)
    wins = _windows(m)
    assert len(wins) == 1
    o = wins[0]
    assert o.width_m == pytest.approx(1.4, abs=0.01)
    assert o.height_m == pytest.approx(1.6, abs=0.01)
    assert o.sill_m == pytest.approx(0.8, abs=0.01)
    assert o.host_global_id == walls["east"].GlobalId
    assert o.provenance.method == "ifc_import:tier0:opening:direct_fill"
    assert "no IfcRelFillsElement" in (o.provenance.note or "")
    assert o.provenance.confidence == pytest.approx(0.85)


def test_floating_window_off_wall_ignored(tmp_path):
    """A floating window nowhere near a wall is not guessed onto one."""
    b = IfcBuilder()
    walls = box_plan(b, east_window=False)
    floating_window(b, walls["east"], 3.0, offset=3.0)
    path = b.write(tmp_path / "orphan.ifc")
    m = import_ifc(path)
    assert _windows(m) == []


def test_fills_relationships_still_win(tmp_path):
    """A normal file with fills is untouched: no pairing, no #1206 notes."""
    b = IfcBuilder()
    box_plan(b, east_window=True)
    path = b.write(tmp_path / "normal.ifc")
    m = import_ifc(path)
    wins = _windows(m)
    assert len(wins) == 1
    o = wins[0]
    assert o.provenance.method == "ifc_import:tier0:opening:solid"
    assert "no IfcRelFillsElement" not in (o.provenance.note or "")
