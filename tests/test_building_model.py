"""Tests for building_model.py — canonical building model and JSON round-trip."""

from building_model import (
    MODEL_VERSION,
    BuildingModel,
    Provenance,
    Space,
    SpaceOpening,
)


def _make_provenance():
    return Provenance(
        sheet_id="arch_A101",
        revision=1,
        method="test",
        confidence=0.95,
    )


def test_model_version_constant():
    """MODEL_VERSION is a non-empty string."""
    assert isinstance(MODEL_VERSION, str)
    assert len(MODEL_VERSION) > 0


def test_provenance_roundtrip():
    """Provenance dataclass can be serialised and deserialised."""
    p = _make_provenance()
    d = p.__dict__.copy()
    restored = Provenance(**d)
    assert restored.sheet_id == p.sheet_id
    assert restored.revision == p.revision
    assert restored.confidence == p.confidence


def test_space_provenance_history():
    """Space.provenance and history fields work correctly."""
    p = _make_provenance()
    sp = Space(
        id="L1-101",
        level_id="L1",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        name="Open Office",
        number="101",
        provenance=p,
        history=[],
    )
    assert sp.provenance == p
    assert sp.history == []


def test_space_opening_defaults():
    """SpaceOpening has sensible defaults."""
    op = SpaceOpening(id="w1", tag="A", category="window", width_m=1.2, height_m=1.5)
    assert op.sill_m is None
    assert op.head_m is None
    assert op.needs_review is False
    assert op.provenance is None


def test_building_model_empty():
    """Empty BuildingModel can be constructed."""
    m = BuildingModel(building_id="bldg_1", building_name="Test", levels=[], spaces={})
    assert m.building_id == "bldg_1"
    assert m.model_version == MODEL_VERSION


def test_building_model_to_json_and_back():
    """BuildingModel.to_json() and from_json() round-trip cleanly."""
    m = BuildingModel(building_id="bldg_1", building_name="Test", levels=[], spaces={})
    m._rev_seq = 0
    s = m.to_json()
    restored = BuildingModel.from_json(s)
    assert restored.building_id == m.building_id
    assert restored.building_name == m.building_name
    assert restored.model_version == MODEL_VERSION


def test_space_to_dict_contains_required_keys():
    """Space serialisation includes id, level_id, name, number, and polygon_m."""
    sp = Space(
        id="L1-101",
        level_id="L1",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        name="Office",
        number="101",
    )
    d = sp.__dict__
    assert "id" in d
    assert "polygon_m" in d
    assert "name" in d
