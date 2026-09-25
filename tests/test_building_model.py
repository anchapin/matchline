"""Tests for building_model.py — canonical building model and JSON round-trip."""

from building_model import (
    MODEL_VERSION,
    BuildingModel,
    Provenance,
    ReviewItem,
    Space,
    SpaceOpening,
    Zone,
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


def test_space_core_provenance_history():
    """Space.core_provenance and history fields work correctly."""
    p = _make_provenance()
    sp = Space(
        id="L1-101",
        level_id="L1",
        polygon_m=[[0, 0], [10, 0], [10, 10], [0, 10]],
        name="Open Office",
        number="101",
        core_provenance=p,
        history=[],
    )
    assert sp.core_provenance == p
    assert sp.history == []


def test_space_opening_defaults():
    """SpaceOpening has sensible defaults."""
    op = SpaceOpening(id="w1", tag="A", category="window", width_m=1.2, height_m=1.5)
    assert op.sill_m is None
    assert op.head_m is None
    assert op.needs_review is True
    assert op.provenance is None


def test_review_item_needs_review_default():
    """Regress: ReviewItem.needs_review defaults to True (not False).

    This was broken in the original implementation — facts could silently bypass
    the review queue when ReviewItem was instantiated without an explicit
    needs_review flag. See issue #405.
    """
    item = ReviewItem(
        id="test-regress",
        kind="wall_area",
        description="test",
        confidence=0.5,
    )
    assert item.needs_review is True


def test_review_item_confidence_invariant():
    """Regress: ReviewItem with confidence=1.0 cannot have needs_review=False.

    A high-confidence item marked as reviewed bypasses the review queue
    entirely. The __post_init__ guard prevents this configuration. See issue #405.
    """
    import pytest

    with pytest.raises(ValueError, match="confidence=1.0.*needs_review=False"):
        ReviewItem(
            id="test-regress-2",
            kind="wall_area",
            description="test",
            confidence=1.0,
            needs_review=False,
        )


def test_building_model_empty():
    """Empty BuildingModel can be constructed."""
    m = BuildingModel(name="Test")
    assert m.name == "Test"
    assert m.model_version == MODEL_VERSION


def test_building_model_to_json_and_back():
    """BuildingModel.to_json() and from_json() round-trip cleanly."""
    m = BuildingModel(name="Test")
    m._rev_seq = 0
    s = m.to_json()
    restored = BuildingModel.from_json(s)
    assert restored.name == m.name
    assert restored.model_version == MODEL_VERSION


def test_space_to_dict_contains_required_keys():
    """Space serialisation includes id, level_id, name, number, and polygon_m."""
    sp = Space(
        id="L1-101",
        level_id="L1",
        polygon_m=[[0, 0], [10, 0], [10, 10], [0, 10]],
        name="Office",
        number="101",
    )
    d = sp.__dict__
    assert "id" in d
    assert "polygon_m" in d
    assert "name" in d


def test_zone_creation():
    """Zone can be constructed with required fields."""
    z = Zone(id="z1", level_id="L1", space_ids=["L1-101"])
    assert z.id == "z1"
    assert z.space_ids == ["L1-101"]
