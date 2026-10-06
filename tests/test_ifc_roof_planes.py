"""IFC roof geometry imports as RoofPlanes (#614)."""

import math

import pytest

from building_model import BimElement, BimOpening, BuildingModel, Level
from ifc_import import import_ifc
from ifc_roof_planes import orient_skylights
from roof_geometry import roof_plane
from tests.ifc_builder import IfcBuilder, box_plan

T30 = math.tan(math.radians(30))


def _import(tmp_path, b):
    path = b.write(tmp_path / "roof.ifc")
    return import_ifc(str(path))


def _gable(b, w=10.0, d=6.0, h=3.0):
    rise = (d / 2) * T30
    south = b.roof_slab("S", [(0, 0, h), (w, 0, h), (w, d / 2, h + rise), (0, d / 2, h + rise)])
    north = b.roof_slab("N", [(0, d / 2, h + rise), (w, d / 2, h + rise), (w, d, h), (0, d, h)])
    return south, north


def _by_az(m):
    return {round(rp.azimuth_deg): rp for rp in m.roof_planes}


def test_gable_gives_two_planes_facing_north_and_south(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    _gable(b)
    m = _import(tmp_path, b)
    planes = _by_az(m)
    assert sorted(planes) == [0, 180]
    for rp in planes.values():
        assert rp.tilt_deg == pytest.approx(30, abs=0.01)
        assert rp.area_m2 == pytest.approx(30 / math.cos(math.radians(30)), rel=1e-4)
        assert rp.provenance.method == "ifc_import:tier0:roof_plane"
        assert rp.level_id == m.levels[0].id


def test_hip_gives_four_compass_planes(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    h, r = 3.0, 3 * T30  # 30 deg on all four sides of a 10 x 6 plan
    b.roof_slab("S", [(0, 0, h), (10, 0, h), (7, 3, h + r), (3, 3, h + r)])
    b.roof_slab("N", [(10, 6, h), (0, 6, h), (3, 3, h + r), (7, 3, h + r)])
    b.roof_slab("E", [(10, 0, h), (10, 6, h), (7, 3, h + r)])
    b.roof_slab("W", [(0, 6, h), (0, 0, h), (3, 3, h + r)])
    m = _import(tmp_path, b)
    planes = _by_az(m)
    assert sorted(planes) == [0, 90, 180, 270]
    assert all(rp.tilt_deg == pytest.approx(30, abs=0.01) for rp in planes.values())
    plan = sum(rp.area_m2 * math.cos(math.radians(rp.tilt_deg)) for rp in planes.values())
    assert plan == pytest.approx(60, rel=1e-4)


def test_flat_roof_slab_is_one_flat_plane(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    b.slab("Roof", [(0, 0), (10, 0), (10, 6), (0, 6)], z=3.0, thickness=0.3, predefined="ROOF")
    m = _import(tmp_path, b)
    assert len(m.roof_planes) == 1
    rp = m.roof_planes[0]
    assert rp.tilt_deg == 0 and rp.azimuth_deg is None
    assert rp.area_m2 == pytest.approx(60, rel=1e-6)
    assert {round(v[2], 6) for v in rp.vertices_m} == {3.3}


def test_heights_are_above_the_level_floor(tmp_path):
    b = IfcBuilder(storey_elevation=4.0)
    box_plan(b)
    _gable(b)
    m = _import(tmp_path, b)
    zs = [v[2] for rp in m.roof_planes for v in rp.vertices_m]
    assert min(zs) == pytest.approx(3.0, abs=1e-6)
    assert max(zs) == pytest.approx(3.0 + 3 * T30, abs=1e-6)


def test_canonical_frame_is_y_down(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    south, _ = _gable(b)
    m = _import(tmp_path, b)
    rp = next(p for p in m.roof_planes if p.host_global_id == south.GlobalId)
    eave = [v for v in rp.vertices_m if abs(v[2] - 3.0) < 1e-6]
    assert {round(v[1], 6) for v in eave} == {0.0}
    ridge = [v for v in rp.vertices_m if v[2] > 3.1]
    assert {round(v[1], 6) for v in ridge} == {-3.0}


def test_ifcroof_aggregating_slabs_reads_each_slab_once(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    south, north = _gable(b)
    for s in (south, north):  # parts belong to the roof, not the storey
        for rel in list(s.ContainedInStructure):
            rel.RelatedElements = [e for e in rel.RelatedElements if e != s]
    roof = b.roof("Roof", [south, north])
    m = _import(tmp_path, b)
    assert len(m.roof_planes) == 2
    assert {rp.host_global_id for rp in m.roof_planes} == {south.GlobalId, north.GlobalId}
    assert all(rp.host_global_id != roof.GlobalId for rp in m.roof_planes)
    assert all(rp.level_id == m.levels[0].id for rp in m.roof_planes)


def test_roof_without_geometry_is_flagged_not_guessed(tmp_path):
    import ifcopenshell.api.root as R

    b = IfcBuilder()
    box_plan(b)
    s = R.create_entity(b.f, ifc_class="IfcSlab", name="Ghost")
    s.PredefinedType = "ROOF"
    b._contain(s)
    m = _import(tmp_path, b)
    assert m.roof_planes == []
    items = [r for r in m.review_queue if r.kind == "roof_plane"]
    assert len(items) == 1 and s.GlobalId in items[0].description
    assert any("1 flagged for review" in r.note for r in m.revision_log)


def test_revision_summary_counts_planes(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    _gable(b)
    m = _import(tmp_path, b)
    assert any("roof planes: 2 from 2 roof element(s)" in r.note for r in m.revision_log)


def test_no_roof_elements_leaves_roof_planes_empty(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    m = _import(tmp_path, b)
    assert m.roof_planes == []
    assert not any("roof planes" in r.note for r in m.revision_log)


def _sky_model(center):
    m = BuildingModel(levels=[Level(id="L1")])
    south = roof_plane(
        "RF-S", [(0, 0, 3), (10, 0, 3), (10, -3, 4.73), (0, -3, 4.73)], "L1", "SLAB-S"
    )
    north = roof_plane(
        "RF-N", [(0, -3, 4.73), (10, -3, 4.73), (10, -6, 3), (0, -6, 3)], "L1", "SLAB-N"
    )
    m.roof_planes = [south, north]
    op = BimOpening(id="SK1", category="skylight", fill_global_id="W1", plan_center_m=list(center))
    m.bim_elements = [BimElement(global_id="SLAB-S", ifc_class="IfcSlab", openings=[op])]
    return m, op


def test_skylight_takes_its_roof_planes_tilt_and_azimuth():
    m, op = _sky_model((5, -1.5))
    assert orient_skylights(m) == 1
    assert op.azimuth_deg == pytest.approx(180, abs=0.01)
    assert op.tilt_deg == pytest.approx(30, abs=0.1)


def test_skylight_over_no_plane_stays_unknown():
    m, op = _sky_model((50, 50))
    assert orient_skylights(m) == 0
    assert op.tilt_deg is None and op.azimuth_deg is None
