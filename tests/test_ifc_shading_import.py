"""Roadmap item 5, wave 4: IfcShadingDevice back into BuildingModel.shading.

The importer hosts each plate on the wall line under its inner edge and
measures placement in that wall's frame, so export -> import -> export puts
every shade back on the same absolute quad.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

from bem_geometry import BEMShade  # noqa: E402
from bem_ifc4 import write_ifc4  # noqa: E402
from ifc_export import _bem_from_model, _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from tests.model_factory import add_shading, make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _shaded():
    m = make_clean_model()
    add_shading(m)
    return m


def _roundtrip(tmp_path, m=None):
    m = m or _shaded()
    path = _export_ifc(m, tmp_path / "rt.ifc")
    return m, import_ifc(path)


def _quad_key(verts):
    return sorted(tuple(round(c, 4) for c in v) for v in verts)


def test_shades_come_back_hosted_with_ids_and_kinds(tmp_path):
    _, bm = _roundtrip(tmp_path)
    by_id = {s.id: s for s in bm.shading}
    assert set(by_id) == {"SH-OVH-1", "SH-FIN-1"}
    assert by_id["SH-OVH-1"].kind == "overhang"
    assert by_id["SH-FIN-1"].kind == "fin"
    env_ids = {w.id for w in bm.envelope}
    for s in bm.shading:
        assert s.host_wall_id in env_ids
        assert s.provenance.confidence == pytest.approx(0.9)
        assert s.provenance.method == "ifc_import:tier0:shading"


def test_overhang_and_fin_sizes_survive(tmp_path):
    m, bm = _roundtrip(tmp_path)
    by_id = {s.id: s for s in bm.shading}
    o = by_id["SH-OVH-1"]
    assert o.width_m == pytest.approx(4.0, abs=1e-3)
    assert o.depth_m == pytest.approx(0.6, abs=1e-3)
    assert o.z_m == pytest.approx(2.2, abs=1e-3)
    fin = by_id["SH-FIN-1"]
    assert fin.depth_m == pytest.approx(0.4, abs=1e-3)
    assert fin.z_m == pytest.approx(0.0, abs=1e-3)
    assert fin.height_m == pytest.approx(m.envelope[0].height_m, abs=1e-3)


def test_reexport_puts_shades_on_the_same_quads(tmp_path):
    m, bm = _roundtrip(tmp_path)
    before = {s.id: _quad_key(s.vertices) for s in _bem_from_model(m).shades}
    after = {s.id: _quad_key(s.vertices) for s in _bem_from_model(bm).shades}
    assert after == before


def test_imported_shading_passes_the_host_check(tmp_path):
    _, bm = _roundtrip(tmp_path)
    res = next(r for r in run_checks(bm).results if r.check_id == "shading_host_reference")
    assert res.severity == "pass", res.message


def test_plate_off_every_wall_comes_back_unhosted(tmp_path):
    bem = _bem_from_model(_shaded())
    far = [(50.0, 50.0, 2.0), (52.0, 50.0, 2.0), (52.0, 51.0, 2.0), (50.0, 51.0, 2.0)]
    bem.shades.append(BEMShade(id="SH-FAR", kind="overhang", host_wall_id="x", vertices=far))
    bm = import_ifc(write_ifc4(bem, tmp_path / "far.ifc"))
    far_sh = next(s for s in bm.shading if s.id == "SH-FAR")
    assert far_sh.host_wall_id == ""
    assert far_sh.kind == "overhang"
    assert far_sh.along_m is None and far_sh.depth_m is None
    assert far_sh.provenance.confidence == pytest.approx(0.5)
    res = next(r for r in run_checks(bm).results if r.check_id == "shading_host_reference")
    assert res.severity in ("warn", "error") and "SH-FAR" in res.entities


def test_duplicate_reference_gets_a_globalid_id(tmp_path):
    bem = _bem_from_model(_shaded())
    dup = bem.shades[0]
    bem.shades.append(
        BEMShade(id=dup.id, kind=dup.kind, host_wall_id=dup.host_wall_id, vertices=dup.vertices)
    )
    bm = import_ifc(write_ifc4(bem, tmp_path / "dup.ifc"))
    ids = [s.id for s in bm.shading]
    assert len(ids) == 3 and len(set(ids)) == 3
    assert ids.count("SH-OVH-1") == 1
    assert any(i.startswith("SH-") and i not in ("SH-OVH-1", "SH-FIN-1") for i in ids)


def test_file_without_shading_imports_none(tmp_path):
    _, bm = _roundtrip(tmp_path, make_clean_model())
    assert bm.shading == []
