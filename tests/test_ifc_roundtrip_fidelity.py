"""IFC round-trip fidelity: export -> import keeps windows, facades, volumes, zones.

Roadmap item 7, Tier 1 envelope classification, plus the export/import gaps
the round trip exposed (windows merged on export, dimensionless wall
openings, unprefixed envelope ids, missing space volumes, one-way zone links).
"""

import pytest

pytest.importorskip("ifcopenshell")

from building_model import BuildingModel, EnvelopeWall, Provenance, Space  # noqa: E402
from ifc_export import _bem_from_model, _export_ifc  # noqa: E402
from ifc_import import _classify_envelope, import_ifc  # noqa: E402
from tests.model_factory import make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


@pytest.fixture
def roundtrip(tmp_path):
    m = make_clean_model()
    return m, import_ifc(_export_ifc(m, tmp_path / "rt.ifc"))


def test_export_keeps_one_unit_per_window():
    m = make_clean_model()
    n_wall_ops = sum(1 for s in m.spaces.values() for o in s.openings if o.category != "skylight")
    assert n_wall_ops == 2
    assert len(_bem_from_model(m).openings) == n_wall_ops


def test_wall_openings_come_back_with_dimensions(roundtrip):
    _, bm = roundtrip
    ops = [o for e in bm.bim_elements for o in e.openings]
    assert len(ops) == 2
    for o in ops:
        assert o.width_m == pytest.approx(1.5, abs=1e-3)
        assert o.height_m == pytest.approx(1.2, abs=1e-3)
        assert o.sill_m == pytest.approx(0.9, abs=1e-3)
        assert o.provenance.method == "ifc_import:tier0:opening:solid"


def test_envelope_ids_are_level_prefixed(roundtrip):
    _, bm = roundtrip
    assert sorted(w.id for w in bm.envelope) == ["L1-EW1", "L1-EW2", "L1-EW3", "L1-EW4"]


def test_facades_classified_and_areas_match(roundtrip):
    m, bm = roundtrip
    orig = {w.facade: w for w in m.envelope}
    got = {w.facade: w.area_m2 for w in bm.envelope}
    assert set(got) == {"north", "south", "east", "west"}
    # the exporter centres walls on the ring edge (#609), so the centreline
    # segments import puts back (#579) are the original edges, full length
    for f, w in orig.items():
        assert got[f] == pytest.approx(w.area_m2, rel=1e-3)
    for w in bm.envelope:
        assert w.provenance.method == "ifc_import:tier1:facade"


def test_space_id_set_only_when_one_room_behind_wall(roundtrip):
    _, bm = roundtrip
    by_f = {w.facade: w for w in bm.envelope}
    assert by_f["west"].space_id == "L1-101"
    assert by_f["east"].space_id == "L1-102"
    # north/south run along both rooms: no single space, said so in the note
    for f in ("north", "south"):
        assert by_f[f].space_id == ""
        assert "spans L1-101, L1-102" in by_f[f].provenance.note


def test_space_volume_derived_from_wall_height(roundtrip):
    _, bm = roundtrip
    for s in bm.spaces.values():
        assert s.volume_m3 == pytest.approx(s.area_m2 * 3.0, rel=1e-6)
        assert "volume derived" in s.core_provenance.note


def test_zone_links_reciprocated(roundtrip):
    _, bm = roundtrip
    assert bm.spaces["L1-101"].hvac.zone_ids == ["L1-Z1"]
    assert bm.spaces["L1-102"].hvac.zone_ids == []


def test_roundtrip_leaves_no_validation_errors(roundtrip):
    """Diffusers now export as IfcAirTerminal in their IfcZone, so the last
    round-trip error (zone_nonempty) is gone too."""
    _, bm = roundtrip
    errors = {r.check_id for r in run_checks(bm).results if r.severity == "error"}
    assert errors == set()


def _bare_model():
    m = BuildingModel(name="t")
    for sid, poly in (
        ("L1-101", [[0, 0], [5, 0], [5, 6], [0, 6]]),
        ("L1-102", [[5, 0], [10, 0], [10, 6], [5, 6]]),
    ):
        m.spaces[sid] = Space(id=sid, level_id="L1", polygon_m=poly, area_m2=30.0)
    return m


def _wall(wid, a, b):
    return EnvelopeWall(
        id=wid,
        facade="",
        from_m=list(a),
        to_m=list(b),
        provenance=Provenance(
            sheet_id="t", revision=1, method="ifc_import:tier0:envelope", confidence=0.95, note=""
        ),
    )


def test_interior_partition_leaves_envelope():
    m = _bare_model()
    m.envelope = [_wall("L1-EW1", (5, 0), (5, 6)), _wall("L1-EW2", (0, 6), (5, 6))]
    counts = _classify_envelope(m)
    assert counts == {"exterior": 1, "interior": 1, "unclassified": 0}
    assert [w.id for w in m.envelope] == ["L1-EW2"]
    assert m.envelope[0].facade == "south" and m.envelope[0].space_id == "L1-101"


def test_wall_with_no_room_either_side_is_not_guessed():
    m = _bare_model()
    m.envelope = [_wall("L1-EW1", (20, 0), (20, 6))]
    counts = _classify_envelope(m)
    assert counts["unclassified"] == 1
    w = m.envelope[0]
    assert w.facade == "" and w.space_id == ""
    assert w.provenance.confidence == 0.5
