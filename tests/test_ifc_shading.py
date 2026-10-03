"""Roadmap item 5, wave 3: shading surfaces into IFC as IfcShadingDevice.

Each BEMShade becomes a thin swept plate on the same absolute quad the gbXML
Shade surface uses, so both exports put a shade in the same place.
"""

from __future__ import annotations

import math

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

import ifcopenshell.util.element as _El  # noqa: E402

from bem_geometry import BEMShade  # noqa: E402
from bem_ifc4 import SHADE_THICKNESS_M, validate_ifc4, write_ifc4  # noqa: E402
from ifc_export import _bem_from_model, _export_ifc  # noqa: E402
from tests.model_factory import add_shading, make_clean_model  # noqa: E402


def _shaded_bem():
    m = make_clean_model()
    add_shading(m)
    return m, _bem_from_model(m)


def _devices(path):
    f = ifcopenshell.open(str(path))
    return f, {d.Name: d for d in f.by_type("IfcShadingDevice")}


def _solid(dev):
    rep = dev.Representation.Representations[0]
    assert rep.RepresentationType == "SweptSolid"
    return rep.Items[0]


def test_shades_written_as_shading_devices_and_validate(tmp_path):
    _, bem = _shaded_bem()
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    ok, errors = validate_ifc4(path)
    assert ok, errors
    _, devs = _devices(path)
    assert set(devs) == {"SH-OVH-1", "SH-FIN-1"}
    assert devs["SH-OVH-1"].PredefinedType == "USERDEFINED"
    assert devs["SH-OVH-1"].ObjectType == "OVERHANG"
    assert devs["SH-FIN-1"].ObjectType == "FIN"
    for d in devs.values():
        assert d.ContainedInStructure
    assert any("2 shading device(s)" in n for n in bem.notes)


def test_device_sits_on_the_same_quad_as_the_gbxml_shade(tmp_path):
    _, bem = _shaded_bem()
    by_id = {s.id: s for s in bem.shades}
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    _, devs = _devices(path)
    for sid, dev in devs.items():
        loc = dev.ObjectPlacement.RelativePlacement.Location.Coordinates
        v0 = by_id[sid].vertices[0]
        assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(loc, v0))


def test_overhang_is_a_horizontal_plate_with_drawn_extents(tmp_path):
    _, bem = _shaded_bem()
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    _, devs = _devices(path)
    d = devs["SH-OVH-1"]
    axis = d.ObjectPlacement.RelativePlacement.Axis.DirectionRatios
    assert math.isclose(abs(axis[2]), 1.0)
    prof = _solid(d).SweptArea
    assert sorted([round(prof.XDim, 6), round(prof.YDim, 6)]) == [0.6, 4.0]
    assert math.isclose(_solid(d).Depth, SHADE_THICKNESS_M)


def test_fin_is_a_vertical_plate_with_drawn_extents(tmp_path):
    m, bem = _shaded_bem()
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    _, devs = _devices(path)
    d = devs["SH-FIN-1"]
    axis = d.ObjectPlacement.RelativePlacement.Axis.DirectionRatios
    assert math.isclose(axis[2], 0.0, abs_tol=1e-9)
    prof = _solid(d).SweptArea
    h = m.envelope[0].height_m
    assert sorted([round(prof.XDim, 6), round(prof.YDim, 6)]) == sorted([0.4, round(h, 6)])


def test_source_ids_kept_in_psets(tmp_path):
    _, bem = _shaded_bem()
    hosts = {s.id: s.host_wall_id for s in bem.shades}
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    _, devs = _devices(path)
    for sid, d in devs.items():
        psets = _El.get_psets(d)
        assert psets["Pset_ShadingDeviceCommon"]["Reference"] == sid
        assert psets["Matchline_ShadingSource"]["HostWallId"] == hosts[sid]


def test_no_shades_writes_no_shading_devices(tmp_path):
    bem = _bem_from_model(make_clean_model())
    path = write_ifc4(bem, tmp_path / "plain.ifc")
    _, devs = _devices(path)
    assert devs == {}
    assert not any("shading device" in n for n in bem.notes)


def test_degenerate_quad_skipped_with_note(tmp_path):
    _, bem = _shaded_bem()
    flat = [(1.0, 1.0, 2.0)] * 4
    bem.shades.append(BEMShade(id="SH-BAD", kind="overhang", host_wall_id="x", vertices=flat))
    path = write_ifc4(bem, tmp_path / "shade.ifc")
    ok, errors = validate_ifc4(path)
    assert ok, errors
    _, devs = _devices(path)
    assert "SH-BAD" not in devs
    assert any("shade SH-BAD skipped: degenerate quad" in n for n in bem.notes)


def test_full_export_path_with_shading(tmp_path):
    m = make_clean_model()
    add_shading(m)
    path = _export_ifc(m, tmp_path / "full.ifc")
    _, devs = _devices(path)
    assert len(devs) == 2
