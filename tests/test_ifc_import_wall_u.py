"""import_ifc reads Pset_WallCommon.ThermalTransmittance back (roadmap items 6 + 7)."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402

from constructions import apply_wall_u_rollup  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from tests.model_factory import add_wall_constructions, make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _rolled():
    m = make_clean_model()
    add_wall_constructions(m)
    apply_wall_u_rollup(m)
    return m


def _roundtrip(m, tmp_path, name="b.ifc"):
    p = tmp_path / name
    _export_ifc(m, p)
    return import_ifc(p)


def _check(m, cid):
    return next(c for c in run_checks(m).results if c.check_id == cid)


def test_one_construction_per_distinct_u(tmp_path):
    m = _rolled()
    back = _roundtrip(m, tmp_path)
    want = sorted({round(sp.wall_u_value_w_m2k, 6) for sp in m.spaces.values()})
    got = sorted(c.u_value_w_m2k for c in back.constructions.values())
    assert got == want


def test_exterior_walls_name_their_construction(tmp_path):
    m = _rolled()
    back = _roundtrip(m, tmp_path)
    assert back.envelope
    for w in back.envelope:
        c = back.constructions[w.construction_id]
        assert c.u_value_w_m2k == pytest.approx(m.spaces[w.space_id].wall_u_value_w_m2k)


def test_space_wall_u_survives_roundtrip(tmp_path):
    m = _rolled()
    back = _roundtrip(m, tmp_path)
    for sid, sp in m.spaces.items():
        assert back.spaces[sid].wall_u_value_w_m2k == pytest.approx(sp.wall_u_value_w_m2k)


def test_construction_provenance_names_the_source(tmp_path):
    back = _roundtrip(_rolled(), tmp_path)
    for c in back.constructions.values():
        assert c.provenance.method == "ifc_import:tier0:wall_u"
        assert "GlobalId=" in c.provenance.note


def test_coverage_check_passes_after_import(tmp_path):
    back = _roundtrip(_rolled(), tmp_path)
    assert _check(back, "wall_construction_coverage").severity == "pass"


def test_no_thermal_pset_leaves_constructions_empty(tmp_path):
    back = _roundtrip(make_clean_model(), tmp_path)
    assert back.constructions == {}
    assert all(w.construction_id == "" for w in back.envelope)
    assert all(sp.wall_u_value_w_m2k is None for sp in back.spaces.values())
    assert _check(back, "wall_construction_coverage").severity == "skip"


def test_non_positive_u_is_ignored(tmp_path):
    import ifcopenshell.api.pset as _Ps
    import ifcopenshell.util.element as E

    p = tmp_path / "b.ifc"
    _export_ifc(_rolled(), p)
    f = ifcopenshell.open(str(p))
    for w in f.by_type("IfcWall"):
        pid = E.get_psets(w)["Pset_WallCommon"]["id"]
        _Ps.edit_pset(f, pset=f.by_id(pid), properties={"ThermalTransmittance": 0.0})
    f.write(str(p))
    back = import_ifc(p)
    assert back.constructions == {}
    assert all(w.construction_id == "" for w in back.envelope)


def test_import_adds_no_new_validation_errors(tmp_path):
    # baseline is the same geometry exported without U-values, so the only
    # difference between the two files is Pset_WallCommon.ThermalTransmittance
    base_m = _rolled()
    for sp in base_m.spaces.values():
        sp.wall_u_value_w_m2k = None
    base = {c.check_id for c in run_checks(_roundtrip(base_m, tmp_path, "a.ifc")).errors}
    rolled = {c.check_id for c in run_checks(_roundtrip(_rolled(), tmp_path, "b.ifc")).errors}
    assert rolled <= base
