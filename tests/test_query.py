"""Tests for the query/ natural-language query layer over the parsed building model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest

from building_model import (
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceOpening,
    Zone,
)
from query import QueryEngine, QueryResponse, _bearing_to_cardinal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakePolygon:
    """Minimal Polygon stand-in for tests."""

    exterior: Any = field(default_factory=list)
    holes: Any = field(default_factory=list)
    area_m2: float = 0.0


def _mk_space(
    space_id: str,
    name: str,
    area_m2: float,
    level_id: str = "L1",
    openings: Optional[List[SpaceOpening]] = None,
) -> Space:
    """Create a minimal Space for testing."""
    return Space(
        id=space_id,
        name=name,
        level_id=level_id,
        area_m2=area_m2,
        openings=openings or [],
    )


def _mk_zone(zone_id: str, name: str, space_ids: List[str], level_id: str = "L1") -> Zone:
    """Create a minimal Zone for testing."""
    return Zone(
        id=zone_id,
        level_id=level_id,
        space_ids=space_ids,
        provenance=None,
    )


def _mk_envelope_wall(
    wall_id: str,
    area_m2: float,
    facade: str = "north",
) -> EnvelopeWall:
    """Create a minimal EnvelopeWall for testing."""
    return EnvelopeWall(
        id=wall_id,
        facade=facade,
        area_m2=area_m2,
        provenance=None,
    )


@pytest.fixture
def model_minimal() -> BuildingModel:
    """A minimal model with 2 spaces, no zones or envelope."""
    m = BuildingModel(
        levels=[Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0)],
        spaces={
            "sp1": _mk_space("sp1", "Space 1", area_m2=50.0),
            "sp2": _mk_space("sp2", "Space 2", area_m2=75.0),
        },
        zones={},
        envelope=[],
    )
    return m


@pytest.fixture
def model_full() -> BuildingModel:
    """A fully populated model with zones, envelope, and orientation."""
    sp1_openings = [
        SpaceOpening(
            id="op1",
            tag="W1",
            category="window",
            width_m=1.5,
            height_m=3.0,
            area_m2=5.0,
            host_facade="north",
            provenance=Provenance(sheet_id="test", revision=1, method="test", confidence=1.0),
        ),
    ]

    m = BuildingModel(
        levels=[
            Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0),
            Level(id="L2", name="Level 2", elevation_z_m=3.0, wall_height_m=3.0),
        ],
        spaces={
            "sp1": _mk_space("sp1", "Office A", area_m2=100.0, openings=sp1_openings),
            "sp2": _mk_space("sp2", "Office B", area_m2=200.0),
            "sp3": _mk_space("sp3", "Storage", area_m2=50.0),
        },
        zones={
            "zone_a": _mk_zone("zone_a", "Office Zone", space_ids=["sp1", "sp2"]),
            "zone_b": _mk_zone("zone_b", "Storage Zone", space_ids=["sp3"]),
        },
        envelope=[
            _mk_envelope_wall("ew1", area_m2=200.0, facade="north"),
            _mk_envelope_wall("ew2", area_m2=150.0, facade="east"),
        ],
    )
    m.orientation = 90.0
    return m


# ---------------------------------------------------------------------------
# Helper query assertions
# ---------------------------------------------------------------------------


class TestQueryResult:
    def test_has_answer_attribute(self):
        @dataclass
        class SimpleModel:
            pass

        @dataclass
        class FakeSpaces:
            pass

        engine = QueryEngine(model=SimpleModel())
        result = engine.count_spaces()
        assert hasattr(result, "answer")
        assert hasattr(result, "provenance")
        assert hasattr(result, "method")


class TestQueryResponse:
    def test_has_results_list(self):
        @dataclass
        class SimpleModel:
            pass

        engine = QueryEngine(model=SimpleModel())
        response = engine.query("how many spaces")
        assert isinstance(response, QueryResponse)
        assert isinstance(response.results, list)


# ---------------------------------------------------------------------------
# count_spaces
# ---------------------------------------------------------------------------


class TestCountSpaces:
    def test_happy_path_two_spaces(self, model_minimal):
        engine = QueryEngine(model=model_minimal)
        result = engine.count_spaces()
        assert result.answer == 2
        assert result.method == "count_spaces"

    def test_happy_path_three_spaces(self, model_full):
        engine = QueryEngine(model=model_full)
        result = engine.count_spaces()
        assert result.answer == 3

    def test_invariant_dispatch_matches_direct(self, model_full):
        engine = QueryEngine(model=model_full)
        direct = engine.count_spaces()
        dispatched = engine.query("how many spaces")
        assert dispatched.results[0].answer == direct.answer

    def test_dispatch_variants(self, model_minimal):
        engine = QueryEngine(model=model_minimal)
        for phrase in [
            "how many spaces are there",
            "count the spaces",
            "count spaces",
            "number of spaces",
            "COUNT Spaces",
        ]:
            resp = engine.query(phrase)
            assert resp.results[0].answer == 2, f"Failed for: {phrase}"

    def test_defect_empty_model(self):
        @dataclass
        class EmptyModel:
            pass

        m = EmptyModel()
        m.spaces = {}
        engine = QueryEngine(model=m)
        result = engine.count_spaces()
        assert result.answer == 0

    def test_defect_missing_spaces_attr(self):
        @dataclass
        class NoSpacesModel:
            pass

        engine = QueryEngine(model=NoSpacesModel())
        result = engine.count_spaces()
        assert result.answer == 0


# ---------------------------------------------------------------------------
# total_floor_area
# ---------------------------------------------------------------------------


class TestTotalFloorArea:
    def test_happy_path(self, model_minimal):
        engine = QueryEngine(model=model_minimal)
        result = engine.total_floor_area()
        assert result.answer == pytest.approx(125.0)

    def test_happy_path_full_model(self, model_full):
        engine = QueryEngine(model=model_full)
        result = engine.total_floor_area()
        assert result.answer == pytest.approx(350.0)

    def test_invariant_dispatch_matches_direct(self, model_full):
        engine = QueryEngine(model=model_full)
        direct = engine.total_floor_area()
        dispatched = engine.query("what is the total floor area")
        assert dispatched.results[0].answer == direct.answer

    def test_dispatch_variants(self, model_minimal):
        engine = QueryEngine(model=model_minimal)
        for phrase in [
            "total floor area",
            "floor area",
            "conditioned area",
        ]:
            resp = engine.query(phrase)
            assert resp.results[0].answer == pytest.approx(125.0), f"Failed for: {phrase}"

    def test_defect_empty_model(self):
        @dataclass
        class EmptyModel:
            pass

        m = EmptyModel()
        m.spaces = {}
        engine = QueryEngine(model=m)
        result = engine.total_floor_area()
        assert result.answer == 0.0

    def test_defect_missing_area_m2(self, model_minimal):
        """Spaces without area_m2 default to 0."""
        m = model_minimal
        m.spaces["sp1"].area_m2 = None
        engine = QueryEngine(model=m)
        result = engine.total_floor_area()
        assert result.answer == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# building_orientation
# ---------------------------------------------------------------------------


class TestBuildingOrientation:
    def test_happy_path_east(self, model_full):
        engine = QueryEngine(model=model_full)
        result = engine.building_orientation()
        assert result.answer == "E"

    def test_happy_path_north(self, model_minimal):
        m = model_minimal
        m.orientation = 0.0
        engine = QueryEngine(model=m)
        result = engine.building_orientation()
        assert result.answer == "N"

    def test_happy_path_southwest(self, model_minimal):
        m = model_minimal
        m.orientation = 225.0
        engine = QueryEngine(model=m)
        result = engine.building_orientation()
        assert result.answer == "SW"

    def test_invariant_dispatch_matches_direct(self, model_full):
        engine = QueryEngine(model=model_full)
        direct = engine.building_orientation()
        dispatched = engine.query("what is the building orientation")
        assert dispatched.results[0].answer == direct.answer

    def test_dispatch_variants(self, model_full):
        engine = QueryEngine(model=model_full)
        for phrase in [
            "building orientation",
            "orientation of the building",
            "which direction does the building face",
            "compass bearing",
        ]:
            resp = engine.query(phrase)
            assert resp.results[0].answer == "E", f"Failed for: {phrase}"

    def test_defect_no_orientation(self, model_minimal):
        """Model without orientation returns 'unknown'."""
        m = model_minimal
        if hasattr(m, "orientation"):
            del m.orientation
        engine = QueryEngine(model=m)
        result = engine.building_orientation()
        assert result.answer == "unknown"

    def test_defect_invalid_orientation(self, model_minimal):
        m = model_minimal
        m.orientation = "not a number"
        engine = QueryEngine(model=m)
        result = engine.building_orientation()
        assert result.answer == "unknown"


# ---------------------------------------------------------------------------
# zone_list
# ---------------------------------------------------------------------------


class TestZoneList:
    def test_happy_path(self, model_full):
        engine = QueryEngine(model=model_full)
        result = engine.zone_list()
        assert result.answer == {
            "zone_a": ["sp1", "sp2"],
            "zone_b": ["sp3"],
        }

    def test_invariant_dispatch_matches_direct(self, model_full):
        engine = QueryEngine(model=model_full)
        direct = engine.zone_list()
        dispatched = engine.query("list all zones")
        assert dispatched.results[0].answer == direct.answer

    def test_dispatch_variants(self, model_full):
        engine = QueryEngine(model=model_full)
        for phrase in [
            "zones",
            "list zones",
            "thermal zones",
            "zone list",
        ]:
            resp = engine.query(phrase)
            assert "zone_a" in resp.results[0].answer, f"Failed for: {phrase}"

    def test_defect_empty_zones(self, model_minimal):
        """Model with no zones returns empty dict."""
        engine = QueryEngine(model=model_minimal)
        result = engine.zone_list()
        assert result.answer == {}

    def test_defect_missing_zones_attr(self):
        @dataclass
        class NoZonesModel:
            pass

        m = NoZonesModel()
        m.zones = {}
        engine = QueryEngine(model=m)
        result = engine.zone_list()
        assert result.answer == {}


# ---------------------------------------------------------------------------
# envelope_summary
# ---------------------------------------------------------------------------


class TestEnvelopeSummary:
    def test_happy_path(self, model_full):
        engine = QueryEngine(model=model_full)
        result = engine.envelope_summary()
        assert result.answer["total_wall_area_m2"] == pytest.approx(350.0)
        assert result.answer["total_window_area_m2"] == pytest.approx(5.0)
        assert result.answer["window_to_wall_ratio"] == pytest.approx(round(5.0 / 350.0, 4))
        assert result.answer["n_windows"] == 1
        assert result.answer["n_facades"] == 1
        assert result.answer["facades"] == ["north"]

    def test_invariant_dispatch_matches_direct(self, model_full):
        engine = QueryEngine(model=model_full)
        direct = engine.envelope_summary()
        dispatched = engine.query("envelope summary")
        assert dispatched.results[0].answer == direct.answer

    def test_dispatch_variants(self, model_full):
        engine = QueryEngine(model=model_full)
        for phrase in [
            "envelope summary",
            "window to wall ratio",
            "wwr",
            "window area",
        ]:
            resp = engine.query(phrase)
            assert resp.results[0].answer["n_windows"] == 1, f"Failed for: {phrase}"

    def test_defect_empty_envelope(self, model_minimal):
        engine = QueryEngine(model=model_minimal)
        result = engine.envelope_summary()
        assert result.answer["total_wall_area_m2"] == 0.0
        assert result.answer["window_to_wall_ratio"] == 0.0
        assert result.answer["n_facades"] == 0

    def test_defect_no_openings(self, model_minimal):
        """Model with walls but no openings has WWR of 0."""
        m = model_minimal
        m.envelope = [_mk_envelope_wall("ew1", area_m2=100.0, facade="south")]
        engine = QueryEngine(model=m)
        result = engine.envelope_summary()
        assert result.answer["window_to_wall_ratio"] == 0.0
        assert result.answer["n_facades"] == 0

    def test_defect_missing_envelope_attr(self, model_full):
        """Gracefully handles missing envelope attribute."""
        m = model_full
        m.envelope = []
        engine = QueryEngine(model=m)
        result = engine.envelope_summary()
        assert result.answer["total_wall_area_m2"] == 0.0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_all_methods_dispatched(self, model_full):
        engine = QueryEngine(model=model_full)
        queries = {
            "how many spaces": "count_spaces",
            "total floor area": "total_floor_area",
            "building orientation": "building_orientation",
            "zones": "zone_list",
            "envelope": "envelope_summary",
        }
        for query, expected_method in queries.items():
            resp = engine.query(query)
            assert resp.method == expected_method, (
                f"Query '{query}' → {resp.method}, expected {expected_method}"
            )

    def test_unknown_query_returns_none_method(self, model_full):
        engine = QueryEngine(model=model_full)
        resp = engine.query("what is the meaning of life")
        assert resp.method == "query_dispatch"
        assert resp.results[0].answer is None

    def test_case_insensitive_dispatch(self, model_full):
        engine = QueryEngine(model=model_full)
        resp_upper = engine.query("HOW MANY SPACES")
        resp_lower = engine.query("how many spaces")
        assert resp_upper.results[0].answer == resp_lower.results[0].answer

    def test_available_queries(self, model_full):
        engine = QueryEngine(model=model_full)
        queries = engine.available_queries()
        assert "count_spaces" in queries
        assert "total_floor_area" in queries
        assert "building_orientation" in queries
        assert "zone_list" in queries
        assert "envelope_summary" in queries
        assert len(queries) >= 5


# ---------------------------------------------------------------------------
# _bearing_to_cardinal helper
# ---------------------------------------------------------------------------


class TestBearingToCardinal:
    @pytest.mark.parametrize(
        ("degrees", "expected"),
        [
            (0, "N"),
            (22.4, "N"),
            (22.5, "NE"),
            (67.4, "NE"),
            (67.5, "E"),
            (112.4, "E"),
            (112.5, "SE"),
            (157.4, "SE"),
            (157.5, "S"),
            (202.4, "S"),
            (202.5, "SW"),
            (247.4, "SW"),
            (247.5, "W"),
            (292.4, "W"),
            (292.5, "NW"),
            (337.4, "NW"),
            (337.5, "N"),
            (360, "N"),
            (720, "N"),
        ],
    )
    def test_cardinal_boundaries(self, degrees, expected):
        assert _bearing_to_cardinal(degrees) == expected

    def test_invalid_input(self):
        assert _bearing_to_cardinal("north") == "unknown"
        assert _bearing_to_cardinal(None) == "unknown"

    def test_negative_degrees_wrapped(self):
        assert _bearing_to_cardinal(-45) == "NW"
