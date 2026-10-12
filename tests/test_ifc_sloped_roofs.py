"""IFC export of sloped roofs and the round trip (#619, roadmap item 2 wave 7)."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4  # noqa: E402
from bem_geometry import BEMRoof  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from roof_geometry import roof_plane  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402

T30 = math.tan(math.radians(30))


def _roofs(kind, H):
    """Canonical frame (y down the sheet): south eave y = 0, north eave y = -6."""
    zr = H + 3 * T30
    if kind == "gable":
        return [
            [(0, 0, H), (10, 0, H), (10, -3, zr), (0, -3, zr)],
            [(0, -3, zr), (10, -3, zr), (10, -6, H), (0, -6, H)],
        ]
    if kind == "hip":
        return [
            [(0, 0, H), (10, 0, H), (7, -3, zr), (3, -3, zr)],
            [(10, -6, H), (0, -6, H), (3, -3, zr), (7, -3, zr)],
            [(10, 0, H), (10, -6, H), (7, -3, zr)],
            [(0, -6, H), (0, 0, H), (3, -3, zr)],
        ]
    zt = H + 6 * math.tan(math.radians(10))
    return [[(0, 0, H), (10, 0, H), (10, -6, zt), (0, -6, zt)]]


def _model(tmp_path, kind):
    b = IfcBuilder()
    box_plan(b)
    m = import_ifc(b.write(tmp_path / "src.ifc"))
    H = m.levels[0].wall_height_m
    lid = m.levels[0].id
    m.roof_planes = [roof_plane(f"P{k}", v, level_id=lid) for k, v in enumerate(_roofs(kind, H))]
    return m


def _sig(model):
    return sorted(
        (
            round(rp.tilt_deg, 3),
            None if rp.azimuth_deg is None else round(rp.azimuth_deg % 360, 3),
            round(rp.area_m2, 3),
        )
        for rp in model.roof_planes
    )


@pytest.mark.parametrize("kind,n", [("gable", 2), ("hip", 4), ("shed", 1)])
def test_planes_round_trip_plane_for_plane_and_a_second_cycle_changes_nothing(tmp_path, kind, n):
    m0 = _model(tmp_path, kind)
    want = _sig(m0)
    _export_ifc(m0, tmp_path / "r1.ifc")
    m1 = import_ifc(tmp_path / "r1.ifc")
    assert len(m1.roof_planes) == n
    assert _sig(m1) == want
    _export_ifc(m1, tmp_path / "r2.ifc")
    m2 = import_ifc(tmp_path / "r2.ifc")
    assert _sig(m2) == want

    def flat(m):
        return sorted(round(c, 6) for rp in m.roof_planes for v in rp.vertices_m for c in v)

    assert flat(m2) == pytest.approx(flat(m1), abs=1e-6)


def test_vertices_come_back_in_the_canonical_frame(tmp_path):
    m0 = _model(tmp_path, "gable")
    _export_ifc(m0, tmp_path / "r1.ifc")
    m1 = import_ifc(tmp_path / "r1.ifc")
    got = sorted(tuple(round(c, 4) for c in v) for rp in m1.roof_planes for v in rp.vertices_m)
    want = sorted(tuple(round(c, 4) for c in v) for rp in m0.roof_planes for v in rp.vertices_m)
    assert got == want


def test_one_ifcroof_aggregates_one_roof_slab_per_plane_with_the_roof_u(tmp_path):
    m0 = _model(tmp_path, "hip")
    m0.roof_construction_id = None
    out = tmp_path / "r1.ifc"
    bem_u = 0.18
    from ifc_export import _bem_from_model

    b = _bem_from_model(m0)
    b.roof_u_value_w_m2k = bem_u
    write_ifc4(b, out)
    f = ifcopenshell.open(str(out))
    roofs = f.by_type("IfcRoof")
    assert len(roofs) == 1
    parts = [o for rel in roofs[0].IsDecomposedBy for o in rel.RelatedObjects]
    assert len(parts) == 4
    assert all(p.is_a("IfcSlab") and p.PredefinedType == "ROOF" for p in parts)
    import ifcopenshell.util.element as ue

    for p in parts:
        assert ue.get_pset(p, "Pset_SlabCommon")["ThermalTransmittance"] == pytest.approx(bem_u)


def test_skylight_on_a_sloped_plane_imports_with_that_planes_tilt(tmp_path):
    H = 3.0
    zr = H + 3 * T30
    box = [(0, 0), (10, 0), (10, 6), (0, 6)]
    b = BEMModel(
        building_name="t",
        spaces=[BEMSpace("SP1", "HALL 101", "101", box, 60.0, 180.0)],
        openings=[BEMOpeningUnit(category="skylight", tag="SK-1", width_m=1.0, height_m=1.2)],
        ring_m=box,
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
        roof_planes=[
            BEMRoof("S", [(0, 0, H), (10, 0, H), (10, 3, zr), (0, 3, zr)], 30.0, 180.0),
            BEMRoof("N", [(0, 3, zr), (10, 3, zr), (10, 6, H), (0, 6, H)], 30.0, 0.0),
        ],
    )
    out = tmp_path / "sky.ifc"
    write_ifc4(b, out)
    f = ifcopenshell.open(str(out))
    sky = [w for w in f.by_type("IfcWindow") if w.PredefinedType == "SKYLIGHT"]
    assert len(sky) == 1
    m = import_ifc(out)
    ops = [o for be in m.bim_elements for o in be.openings if o.category == "skylight"]
    assert len(ops) == 1
    assert ops[0].tilt_deg == pytest.approx(30.0, abs=1e-3)
    assert ops[0].azimuth_deg in (pytest.approx(180.0, abs=1e-3), pytest.approx(0.0, abs=1e-3))


def test_no_roof_planes_export_as_today(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    m = import_ifc(b.write(tmp_path / "src.ifc"))
    assert m.roof_planes == [] or all(rp.tilt_deg <= 0.5 for rp in m.roof_planes)
    _export_ifc(m, tmp_path / "flat.ifc")
    f = ifcopenshell.open(str(tmp_path / "flat.ifc"))
    assert not f.by_type("IfcRoof")
