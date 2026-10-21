"""Millimetre-unit IFC files read at true size (#1217).

IfcOpenShell 0.9's geometry kernel returns SI metres by default, so a
MILLI-prefixed file came back 1000x too small wherever kernel vertices
fed a dimension (esplanades: 295 windows totaling 0.001 m2). The kernel
contract is file units -- callers multiply by the file scale themselves
-- so the kernel settings must convert back. A metre file is unaffected
(scale 1.0), which the rest of the suite already covers.
"""

from __future__ import annotations

import importlib.util

from tests.ifc_builder import IfcBuilder, box_plan


def _import_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _importer():
    return _import_module("ifc_import_for_units", "ifc/ifc_import.py")


def _windows(m):
    return [o for be in m.bim_elements for o in be.openings if o.category == "window"]


def _test_ifc(tmp_path, **kw):
    b = IfcBuilder(**kw)
    box_plan(b)
    path = tmp_path / "plan.ifc"
    b.write(path)
    return path


def test_mm_file_window_dimensions(tmp_path):
    """A MILLI-prefixed file yields true-size window openings."""
    imp = _importer()
    m = imp.import_ifc(str(_test_ifc(tmp_path, length_units="mm")))
    wins = _windows(m)
    assert len(wins) == 1
    win = wins[0]
    assert win.width_m is not None and win.height_m is not None
    assert abs(win.width_m - 2.0) < 1e-6, win.width_m
    assert abs(win.height_m - 1.5) < 1e-6, win.height_m
    assert abs(win.sill_m - 0.9) < 1e-6, win.sill_m


def test_mm_file_kernel_verts_in_file_units(tmp_path):
    """_geom_verts returns the file's own length units, not SI metres."""
    imp = _importer()
    import ifcopenshell

    f = ifcopenshell.open(str(_test_ifc(tmp_path, length_units="mm")))
    assert imp._length_scale(f) == 0.001
    win = next(e for e in f.by_type("IfcWindow"))
    rel = next(r for r in f.by_type("IfcRelFillsElement") if r.RelatedBuildingElement == win)
    opening = rel.RelatingOpeningElement
    verts = imp._geom_verts(opening)
    assert verts, "kernel returned no vertices"
    xs = verts[0::3]
    ys = verts[1::3]
    zs = verts[2::3]
    # The opening solid is 2.0 x wall-thickness x 1.5 in metres, written x1000.
    assert abs((max(xs) - min(xs)) - 2000.0) < 1e-3
    assert abs((max(ys) - min(ys)) - 200.0) < 1e-3
    assert abs((max(zs) - min(zs)) - 1500.0) < 1e-3
