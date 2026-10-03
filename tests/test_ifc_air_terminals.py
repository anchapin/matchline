"""Diffusers survive an IFC round trip as IfcAirTerminal DIFFUSER in their IfcZone."""

from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from building_model import ComponentRef, Zone  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from tests.model_factory import make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _roundtrip(model, tmp_path):
    p = tmp_path / "m.ifc"
    _export_ifc(model, p)
    return p, import_ifc(p)


def test_export_writes_air_terminal_grouped_in_zone(tmp_path):
    p, _ = _roundtrip(make_clean_model(), tmp_path)
    f = ifcopenshell.open(str(p))
    terms = f.by_type("IfcAirTerminal")
    assert len(terms) == 1
    t = terms[0]
    assert (t.Name, t.Tag, t.PredefinedType) == ("D1", "D1", "DIFFUSER")
    zone = f.by_type("IfcZone")[0]
    grouped = [o for rel in zone.IsGroupedBy for o in rel.RelatedObjects]
    assert t in grouped
    assert any(o.is_a("IfcSpace") for o in grouped)


def test_air_terminal_is_contained_in_storey_at_ceiling(tmp_path):
    import ifcopenshell.util.placement as pl

    m = make_clean_model()
    p, _ = _roundtrip(m, tmp_path)
    f = ifcopenshell.open(str(p))
    t = f.by_type("IfcAirTerminal")[0]
    rel = t.ContainedInStructure[0]
    assert rel.RelatingStructure.is_a("IfcBuildingStorey")
    z = pl.get_local_placement(t.ObjectPlacement)[2][3]
    assert z > 2.0  # ceiling, not floor


def test_roundtrip_preserves_zone_diffusers(tmp_path):
    _, r = _roundtrip(make_clean_model(), tmp_path)
    z = r.zones["L1-Z1"]
    assert [(d.id, d.tag, d.type) for d in z.diffusers] == [("D1", "D1", "diffuser")]
    d = z.diffusers[0]
    assert d.x_m == pytest.approx(2.5, abs=1e-6)
    assert d.y_m == pytest.approx(3.0, abs=1e-6)
    assert d.provenance.method == "ifc_import:tier0:air_terminal"


def test_roundtrip_assigns_diffuser_to_containing_space(tmp_path):
    _, r = _roundtrip(make_clean_model(), tmp_path)
    assert [d.id for d in r.spaces["L1-101"].hvac.diffusers] == ["D1"]
    assert r.spaces["L1-102"].hvac.diffusers == []
    assert "inside L1-101" in r.zones["L1-Z1"].diffusers[0].provenance.note


def test_roundtrip_has_no_validation_errors(tmp_path):
    _, r = _roundtrip(make_clean_model(), tmp_path)
    rep = run_checks(r)
    assert [c.check_id for c in rep.errors] == []
    ids = {c.check_id: c for c in rep.results}
    assert ids["zone_nonempty"].severity == "pass"


def test_diffuser_outside_served_spaces_is_not_guessed(tmp_path):
    m = make_clean_model()
    m.zones["L1-Z1"].diffusers[0].x_m = 500.0  # nowhere near any room
    _, r = _roundtrip(m, tmp_path)
    assert [d.id for d in r.zones["L1-Z1"].diffusers] == ["D1"]
    assert all(not sp.hvac.diffusers for sp in r.spaces.values())
    assert "not assigned to a space" in r.zones["L1-Z1"].diffusers[0].provenance.note


def test_diffuser_shared_by_two_zones_is_one_terminal(tmp_path):
    m = make_clean_model()
    d = m.zones["L1-Z1"].diffusers[0]
    m.zones["L1-Z2"] = Zone(id="L1-Z2", level_id="L1", space_ids=["L1-102"], diffusers=[d])
    m.spaces["L1-102"].hvac.zone_ids.append("L1-Z2")
    p, r = _roundtrip(m, tmp_path)
    assert len(ifcopenshell.open(str(p)).by_type("IfcAirTerminal")) == 1
    assert [x.id for x in r.zones["L1-Z1"].diffusers] == ["D1"]
    assert [x.id for x in r.zones["L1-Z2"].diffusers] == ["D1"]
    # listed on exactly one space, whichever zone was read first
    owners = [sid for sid, sp in r.spaces.items() if sp.hvac.diffusers]
    assert owners == ["L1-101"]
    rep = run_checks(r)
    assert "assignment_uniqueness" not in [c.check_id for c in rep.errors]


def test_zone_without_diffusers_writes_no_terminal(tmp_path):
    m = make_clean_model()
    m.zones["L1-Z1"].diffusers.clear()
    p, r = _roundtrip(m, tmp_path)
    assert ifcopenshell.open(str(p)).by_type("IfcAirTerminal") == ()
    assert r.zones["L1-Z1"].diffusers == []
    assert isinstance(ComponentRef, type)
