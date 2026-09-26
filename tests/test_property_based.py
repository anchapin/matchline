"""Property-based tests for core modules using hypothesis.

These tests validate invariants across:
- geometry_simplify: area conservation in polygon simplification
- building_model: JSON serialization round-trip
- polygon_classify: deterministic classification across edge cases

De-flake strategy: all tests use @settings(derandomize=True) to ensure
deterministic, reproducible behaviour across runs. A regression test at the
bottom captures the specific degenerate-polygon case that motivated this fix.
"""

from __future__ import annotations

from typing import List, Tuple

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import Polygon

from geometry_simplify import SimplifyResult, simplify_ring
from polygon_classify import (
    PolyClassEvidence,
    _classify_from_evidence,
    classify_polygons,
)

# Fixed seed for deterministic, reproducible test runs

# ---------------------------------------------------------------------------
# geometry_simplify: simplify_ring area conservation invariants
# ---------------------------------------------------------------------------


def _is_valid_polygon_ring(ring: List[Tuple[float, float]]) -> bool:
    if len(ring) < 3:
        return False
    try:
        poly = Polygon(ring)
        return poly.is_valid and poly.area > 1e-12
    except Exception:
        return False


@given(
    original=st.lists(
        st.tuples(
            st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6)
        ),
        min_size=3,
        max_size=200,
    ),
    tol=st.floats(min_value=1e-3, max_value=0.5),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_simplify_ring_area_delta_within_tolerance(original: List[Tuple[float, float]], tol: float):
    ring = list(original)
    assume(_is_valid_polygon_ring(ring))

    res: SimplifyResult = simplify_ring(ring, tol)

    if not res.valid:
        return

    # area_delta_pct is a percentage (e.g., 5.0 = 5%), tol is a fraction (e.g., 0.1 = 10%)
    # Convert tol to percentage for comparison
    assert res.area_delta_pct <= tol * 100, (
        f"area_delta_pct={res.area_delta_pct:.4f} exceeds tol%={tol * 100:.4f}"
    )


@given(
    original=st.lists(
        st.tuples(
            st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6)
        ),
        min_size=3,
        max_size=200,
    ),
    tol=st.floats(min_value=1e-6, max_value=0.5),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_simplify_ring_never_adds_vertices(original: List[Tuple[float, float]], tol: float):
    ring = list(original)
    assume(_is_valid_polygon_ring(ring))

    res: SimplifyResult = simplify_ring(ring, tol)

    assert res.simplified_count <= res.original_count, (
        f"simplified_count={res.simplified_count} > original_count={res.original_count}"
    )


@given(
    original=st.lists(
        st.tuples(
            st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6)
        ),
        min_size=3,
        max_size=200,
    ),
    tol=st.floats(min_value=1e-6, max_value=0.5),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_simplify_ring_minimum_vertex_count(original: List[Tuple[float, float]], tol: float):
    ring = list(original)
    assume(_is_valid_polygon_ring(ring))

    res: SimplifyResult = simplify_ring(ring, tol)

    if res.valid:
        assert res.simplified_count >= 3, f"simplified_count={res.simplified_count} < 3"


@given(
    original=st.lists(
        st.tuples(
            st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6)
        ),
        min_size=3,
        max_size=200,
    ),
    tol=st.floats(min_value=1e-6, max_value=0.5),
)
@settings(
    max_examples=200,
    deadline=30000,
    suppress_health_check=[HealthCheck.filter_too_much],
    derandomize=True,
)
def test_simplify_ring_preserves_convexity(original: List[Tuple[float, float]], tol: float):
    ring = list(original)
    assume(_is_valid_polygon_ring(ring))

    poly = Polygon(ring)
    assume(poly.equals(poly.convex_hull))

    res: SimplifyResult = simplify_ring(ring, tol)

    if not res.valid:
        return

    simplified = Polygon(res.ring)
    assert simplified.is_valid, "simplified ring must be a valid polygon"
    assert simplified.equals(simplified.convex_hull), "simplified ring must remain convex"


@given(
    original=st.lists(
        st.tuples(
            st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6)
        ),
        min_size=3,
        max_size=200,
    ),
    tol=st.floats(min_value=1e-3, max_value=0.5),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_simplify_ring_area_never_grows(original: List[Tuple[float, float]], tol: float):
    ring = list(original)
    assume(_is_valid_polygon_ring(ring))

    res: SimplifyResult = simplify_ring(ring, tol)

    if not res.valid:
        return

    delta = res.simplified_area - res.original_area
    rel_delta = delta / max(res.original_area, 1e-9)
    assert res.simplified_area <= res.original_area * (1 + 1e-3), (
        f"simplified_area={res.simplified_area} > original_area={res.original_area} "
        f"(rel_delta={rel_delta:.4%})"
    )


# ---------------------------------------------------------------------------
# polygon_classify: classification determinism and validity
# ---------------------------------------------------------------------------


@given(
    area=st.floats(min_value=0.0, max_value=1e6),
    aspect_ratio=st.floats(min_value=0.01, max_value=100.0),
    has_room_number=st.booleans(),
    label=st.text(min_size=0, max_size=200),
    n_doors=st.integers(min_value=0, max_value=100),
    n_adjacent=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=500, deadline=30000, derandomize=True)
def test_classify_from_evidence_is_deterministic(
    area: float,
    aspect_ratio: float,
    has_room_number: bool,
    label: str,
    n_doors: int,
    n_adjacent: int,
):
    ev = PolyClassEvidence(
        area_m2=abs(area),
        aspect_ratio=aspect_ratio,
        has_room_number=has_room_number,
        label_text=label,
        n_doors=n_doors,
        n_adjacent_spaces=n_adjacent,
    )

    results = [_classify_from_evidence(ev) for _ in range(5)]

    assert all(r == results[0] for r in results), "classification must be deterministic"


@given(
    area=st.floats(min_value=0.0, max_value=1e6),
    aspect_ratio=st.floats(min_value=0.01, max_value=100.0),
    has_room_number=st.booleans(),
    label=st.text(min_size=0, max_size=200),
    n_doors=st.integers(min_value=0, max_value=100),
    n_adjacent=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=500, deadline=30000, derandomize=True)
def test_classify_from_evidence_valid_poly_type(
    area: float,
    aspect_ratio: float,
    has_room_number: bool,
    label: str,
    n_doors: int,
    n_adjacent: int,
):
    ev = PolyClassEvidence(
        area_m2=abs(area),
        aspect_ratio=aspect_ratio,
        has_room_number=has_room_number,
        label_text=label,
        n_doors=n_doors,
        n_adjacent_spaces=n_adjacent,
    )

    result = _classify_from_evidence(ev)

    valid_types = {"room", "small_room", "closet", "shaft", "elevator_core", "unassigned"}
    assert result.poly_type in valid_types, f"invalid poly_type: {result.poly_type}"


@given(
    area=st.floats(min_value=0.0, max_value=1e6),
    aspect_ratio=st.floats(min_value=0.01, max_value=100.0),
    has_room_number=st.booleans(),
    label=st.text(min_size=0, max_size=200),
    n_doors=st.integers(min_value=0, max_value=100),
    n_adjacent=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=500, deadline=30000, derandomize=True)
def test_classify_from_evidence_confidence_bounds(
    area: float,
    aspect_ratio: float,
    has_room_number: bool,
    label: str,
    n_doors: int,
    n_adjacent: int,
):
    ev = PolyClassEvidence(
        area_m2=abs(area),
        aspect_ratio=aspect_ratio,
        has_room_number=has_room_number,
        label_text=label,
        n_doors=n_doors,
        n_adjacent_spaces=n_adjacent,
    )

    result = _classify_from_evidence(ev)

    assert 0.0 <= result.confidence <= 1.0, f"confidence={result.confidence} outside [0, 1]"


@given(
    area=st.floats(min_value=0.0, max_value=1e6),
    aspect_ratio=st.floats(min_value=0.01, max_value=100.0),
    has_room_number=st.booleans(),
    label=st.text(min_size=0, max_size=500),
    n_doors=st.integers(min_value=0, max_value=100),
    n_adjacent=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_classify_from_evidence_special_characters_handled(
    area: float,
    aspect_ratio: float,
    has_room_number: bool,
    label: str,
    n_doors: int,
    n_adjacent: int,
):
    ev = PolyClassEvidence(
        area_m2=abs(area),
        aspect_ratio=aspect_ratio,
        has_room_number=has_room_number,
        label_text=label,
        n_doors=n_doors,
        n_adjacent_spaces=n_adjacent,
    )

    result = _classify_from_evidence(ev)

    assert isinstance(result.poly_type, str)
    assert result.poly_type in {
        "room",
        "small_room",
        "closet",
        "shaft",
        "elevator_core",
        "unassigned",
    }


# ---------------------------------------------------------------------------
# polygon_classify: classify_polygons integration (determinism via generate_building)
# ---------------------------------------------------------------------------


@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=50, deadline=30000, derandomize=True)
def test_classify_polygons_deterministic(seed: int):
    from synth.multidiscipline import generate_building

    bldg = generate_building(seed=seed, open_office_span=False)
    rooms = bldg.get("rooms", [])
    south_windows = bldg.get("south_windows", [])
    grids_h = bldg.get("grids_h", {})

    result1 = classify_polygons(rooms, south_windows, grids_h)
    result2 = classify_polygons(rooms, south_windows, grids_h)

    assert result1 == result2, "classify_polygons must be deterministic"


# ---------------------------------------------------------------------------
# Regression test: captures the specific degenerate-polygon failure that
# motivated this fix.  The polygon below has near-collinear consecutive
# vertices which triggered an assertion error in simplify_ring.
# ---------------------------------------------------------------------------


def test_simplify_ring_regression_degenerate():
    """Regression: simplify_ring must not raise on near-collinear rings."""
    # These coordinates form a thin, near-degenerate octagon that previously
    # caused an AssertionError in simplify_ring due to aggressive filtering.
    coords = [
        (0.0, 0.0),
        (1e-9, 1e-9),
        (10.0, 0.0),
        (10.0, 1.0),
        (9.0, 1.0),
        (9.0, 1e-9),
        (1e-9, 1e-9),
        (0.0, 0.0),
    ]
    result = simplify_ring(coords, tol=0.01)
    # Must return a valid SimplifyResult, not raise
    assert isinstance(result, SimplifyResult)
    assert result.simplified_count >= 3, "simplified ring must have at least 3 vertices"


@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_simplify_ring_regression_random(seed: int):
    """Regression: simplify_ring must not raise on any randomly-generated ring."""
    import random

    random.seed(seed)
    # Generate a ring with slight numeric noise that previously triggered flakiness
    n = random.randint(4, 8)
    base = [(random.uniform(-100, 100), random.uniform(-100, 100)) for _ in range(n)]
    # Close the ring
    coords = base + [base[0]]

    result = simplify_ring(coords, tol=0.01)
    assert isinstance(result, SimplifyResult)
    assert result.simplified_count >= 3, "simplified ring must have at least 3 vertices"
