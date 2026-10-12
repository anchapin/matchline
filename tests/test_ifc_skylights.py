"""IFC skylights (roadmap item 3, wave 2b): writer and Tier-0 import.

The writer puts skylights on a roof IfcSlab as IfcWindow
PredefinedType=SKYLIGHT, in the same spots the gbXML writer uses. The importer
reads slab-hosted windows back as skylights and attaches each to the space
under it, so export -> import keeps the skylight on the right room.
"""

from __future__ import annotations

import math

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4  # noqa: E402
from bem_ifc4 import validate_ifc4  # noqa: E402
from ifc_import import import_ifc  # noqa: E402

RING = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
WEST = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
EAST = [(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)]


def _model(openings):
    return BEMModel(
        building_name="Sky IFC",
        spaces=[
            BEMSpace("sp-w", "WEST 101", "101", list(WEST), 100.0, 300.0),
            BEMSpace("sp-e", "EAST 102", "102", list(EAST), 100.0, 300.0),
        ],
        openings=openings,
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
    )


def _sky(tag, sid="", w=1.2, h=0.9):
    return BEMOpeningUnit("skylight", tag, w, h, space_sid=sid)


def test_ifc_with_skylights_validates(tmp_path):
    m = _model([BEMOpeningUnit("window", "A", 1.2, 1.5), _sky("SK-1"), _sky("SK-2")])
    path = write_ifc4(m, tmp_path / "sky.ifc")
    ok, errors = validate_ifc4(path)
    assert ok, errors
    f = ifcopenshell.open(str(path))
    skys = [w for w in f.by_type("IfcWindow") if w.PredefinedType == "SKYLIGHT"]
    assert sorted(w.Name.split()[0] for w in skys) == ["SK-1", "SK-2"]
    for w in skys:
        assert math.isclose(w.OverallWidth, 1.2) and math.isclose(w.OverallHeight, 0.9)
        assert w.ContainedInStructure
    assert any("Roof slab with 2 of 2 skylight(s)" in n for n in m.notes)


def test_skylight_kept_over_its_space(tmp_path):
    from bem_helpers import _place_skylights_on_roof

    units = [_sky("SK-E", "sp-e"), _sky("SK-W", "sp-w")]
    placed, notes = _place_skylights_on_roof(units, RING, regions={"sp-w": WEST, "sp-e": EAST})
    assert not notes
    by_tag = {p["unit"].tag: p["rect"] for p in placed}
    assert all(x >= 10.0 for x, _ in by_tag["SK-E"])
    assert all(x <= 10.0 for x, _ in by_tag["SK-W"])


def test_skylight_too_big_for_its_space_is_named(tmp_path):
    from bem_helpers import _place_skylights_on_roof

    placed, notes = _place_skylights_on_roof(
        [_sky("SK-BIG", "sp-w", w=12.0, h=2.0)], RING, regions={"sp-w": WEST}
    )
    assert placed == [] and "SK-BIG" in notes[0] and "space sp-w" in notes[0]


def test_roundtrip_attaches_skylight_to_the_right_space(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _model([_sky("SK-E", "sp-e"), _sky("SK-W", "sp-w")])
    write_ifc4(m, tmp_path / "rt.ifc")
    bm = import_ifc(tmp_path / "rt.ifc")
    got = {}
    for sp in bm.spaces.values():
        for o in sp.openings:
            if o.category == "skylight":
                got[o.tag] = sp
                assert o.host_facade == "roof"
                assert math.isclose(o.width_m, 1.2, abs_tol=1e-3)
                assert math.isclose(o.height_m, 0.9, abs_tol=1e-3)
                assert math.isclose(o.area_m2, 1.08, abs_tol=1e-3)
    assert set(got) == {"SK-E", "SK-W"}
    assert got["SK-E"].number == "102" and got["SK-W"].number == "101"
    roofs = [e for e in bm.bim_elements if e.ifc_class == "IfcSlab"]
    assert len(roofs) == 1 and len(roofs[0].openings) == 2


def test_unfilled_slab_void_is_not_read_as_a_skylight(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _model([_sky("SK-1")])
    path = write_ifc4(m, tmp_path / "hole.ifc")
    f = ifcopenshell.open(str(path))
    for rel in list(f.by_type("IfcRelFillsElement")):
        f.remove(rel)
    f.write(str(path))
    bm = import_ifc(path)
    roofs = [e for e in bm.bim_elements if e.ifc_class == "IfcSlab"]
    assert roofs and roofs[0].openings == []
    assert not any(o.category == "skylight" for sp in bm.spaces.values() for o in sp.openings)
