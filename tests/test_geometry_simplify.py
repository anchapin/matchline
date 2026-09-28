"""Tests for geometry_simplify.py: happy-path, invariant, and defect-injection."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

import geometry_simplify as gs
from geometry_simplify import (
    _tri_area,
    envelope_area,
    footprint_from_regions,
    ring_edges,
    ring_perimeter,
    simplify_report,
    simplify_ring,
)

SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10]]


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestFootprintFromRegions:
    def test_single_rectangle(self):
        exterior = footprint_from_regions([SQUARE])
        assert Polygon(exterior).equals(Polygon(SQUARE))

    def test_single_rectangle_returns_valid_polygon(self):
        exterior = footprint_from_regions([SQUARE])
        assert Polygon(exterior).is_valid

    def test_empty_regions_returns_empty(self):
        exterior = footprint_from_regions([])
        assert exterior == []


class TestRingEdges:
    def test_simple_square_open_ring(self):
        edges = ring_edges(SQUARE)
        assert len(edges) == 4
        assert edges[0] == ((0, 0), (10, 0))
        assert edges[1] == ((10, 0), (10, 10))
        assert edges[2] == ((10, 10), (0, 10))
        assert edges[3] == ((0, 10), (0, 0))


class TestEnvelopeArea:
    def test_simple_rectangle_with_walls(self):
        area = envelope_area(SQUARE, wall_height=3.0)
        expected = 2 * 100.0 + 40.0 * 3.0  # floor + ceiling + 4 walls
        assert area == pytest.approx(expected)

    def test_envelope_without_walls(self):
        area = envelope_area(SQUARE, wall_height=None)
        assert area == pytest.approx(100.0)


class TestRingPerimeter:
    def test_simple_square(self):
        p = ring_perimeter(SQUARE)
        assert p == pytest.approx(40.0)


class TestSimplifyRing:
    def test_square_no_change_small_tol(self):
        res = simplify_ring(SQUARE, tol=0.001)
        assert res.original_count == 4
        assert res.simplified_count == 4
        assert res.original_area == pytest.approx(100.0)
        assert res.simplified_area == pytest.approx(100.0)
        assert res.area_delta_pct == pytest.approx(0.0)
        assert res.confidence == 1.0

    def test_ring_with_collinear_points_removed(self):
        ring = [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        assert res.simplified_count <= res.original_count


class TestSimplifyReport:
    def test_clean_simplification(self):
        res = simplify_ring(SQUARE, tol=0.001)
        report = simplify_report(res)
        assert report["original_surface_count"] == 4
        assert report["simplified_surface_count"] == 4
        assert report["reduction_pct"] == pytest.approx(0.0)
        assert report["area_delta_pct"] == pytest.approx(0.0)
        assert report["confidence"] == 1.0
        assert report["within_tolerance"] is True
        assert report["valid"] is True

    def test_simplification_report_valid_flag(self):
        ring = [[0, 0], [5, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        report = simplify_report(res)
        assert "valid" in report
        assert isinstance(report["valid"], bool)


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------


def test_simplify_ring_preserves_polygon_validity():
    """Simplified ring must still form a valid polygon."""
    res = simplify_ring(SQUARE, tol=0.001)
    poly = Polygon(res.ring)
    assert poly.is_valid


def test_simplify_ring_preserves_convexity():
    """If input ring is convex, simplified ring should also be convex."""
    angles = np.linspace(0, 2 * np.pi, 7)[:-1]
    convex_ring = [[5 + 4 * np.cos(a), 5 + 4 * np.sin(a)] for a in angles]
    res = simplify_ring(convex_ring, tol=0.01)
    poly = Polygon(res.ring)
    assert poly.is_valid, "simplified ring must form a valid polygon"
    assert poly.equals(poly.convex_hull), "simplified ring must remain convex"


def test_ring_perimeter_equals_shapely_perimeter():
    """ring_perimeter should match Shapely's Polygon.length for the same ring."""
    expected = Polygon(SQUARE).length
    assert ring_perimeter(SQUARE) == pytest.approx(expected)


def test_envelope_area_floor_plus_ceiling_plus_walls():
    """envelope_area with wall_height must include floor + ceiling + walls."""
    area_with_walls = envelope_area(SQUARE, wall_height=3.0)
    area_floor_only = envelope_area(SQUARE, wall_height=None)
    assert area_with_walls > area_floor_only
    assert area_floor_only == pytest.approx(100.0)


def test_footprint_from_regions_produces_valid_polygon():
    """footprint_from_regions must produce a valid polygon ring or empty list."""
    result = footprint_from_regions([SQUARE])
    poly = Polygon(result)
    assert poly.is_valid


def test_simplify_result_confidence_bounded():
    """confidence must be between 0 and 1."""
    res = simplify_ring(SQUARE, tol=0.001)
    assert 0.0 <= res.confidence <= 1.0


def test_simplify_result_valid_is_true_for_good_input():
    """Valid input should produce a valid SimplifyResult."""
    res = simplify_ring(SQUARE, tol=0.001)
    assert res.valid is True


def test_simplify_report_within_tolerance_for_small_tol():
    """simplify_report.within_tolerance should be True when tol is small."""
    res = simplify_ring(SQUARE, tol=0.001)
    report = simplify_report(res)
    assert report["within_tolerance"] is True


# ---------------------------------------------------------------------------
# Defect-injection tests
# ---------------------------------------------------------------------------


class TestDefectInjection:
    def test_empty_ring_does_not_crash(self):
        res = simplify_ring([], tol=0.01)
        assert res.ring == []
        assert res.valid is True

    def test_single_point_ring_raises(self):
        ring = [[0, 0], [0, 0]]
        with pytest.raises(ValueError, match="linearring"):
            simplify_ring(ring, tol=0.01)

    def test_identical_consecutive_points(self):
        ring = [[0, 0], [0, 0], [10, 0], [10, 10], [0, 10]]
        res = simplify_ring(ring, tol=0.01)
        assert res.valid is True

    def test_tri_area_with_collinear_points_is_zero(self):
        area = _tri_area([0, 0], [5, 0], [10, 0])
        assert area == pytest.approx(0.0)

    def test_tri_area_positive_for_ccw_triangle(self):
        area = _tri_area([0, 0], [10, 0], [0, 10])
        assert area == pytest.approx(50.0)

    def test_tri_area_positive_for_cw_triangle(self):
        area = _tri_area([0, 0], [0, 10], [10, 0])
        assert area == pytest.approx(50.0)

    def test_simplify_report_area_delta_pct_reasonable(self):
        """area_delta_pct should be near zero for a perfect square."""
        res = simplify_ring(SQUARE, tol=0.001)
        report = simplify_report(res)
        assert abs(report["area_delta_pct"]) < 0.01

    def test_simplify_report_tolerance_pct_equals_tol_times_100(self):
        """tolerance_pct should be tol * 100."""
        res = simplify_ring(SQUARE, tol=0.05)
        report = simplify_report(res)
        assert report["tolerance_pct"] == pytest.approx(5.0)

    def test_envelope_area_raises_for_degenerate_ring(self):
        """envelope_area raises ValueError for degenerate ring."""
        with pytest.raises(ValueError, match="linearring"):
            envelope_area([[0, 0]], wall_height=3.0)

    def test_simplify_ring_concave_polygon_area_never_grows(self):
        """Concave vertex removal must not cause envelope area to grow.

        An L-shaped polygon has a reflex (concave) vertex at (10, 10).  The
        greedy algorithm removes the least-area-change vertex each step.  For a
        concave vertex the removal technically *increases* area (removing a notch
        fills it in), so the per-step area cap must block such removals when
        they would exceed max_single_step, and the cumulative budget must block
        them when the total delta would exceed tol.

        Deterministic acceptance test: L-shape, tol=5%, max_single_step=0.25%.
        """
        L_SHAPE = [
            [0.0, 0.0],
            [20.0, 0.0],
            [20.0, 10.0],
            [10.0, 10.0],
            [10.0, 20.0],
            [0.0, 20.0],
        ]
        res = simplify_ring(L_SHAPE, tol=0.05, max_single_step=0.0025)
        assert res.simplified_area <= res.original_area, (
            f"area grew: {res.simplified_area} > {res.original_area}"
        )
        assert Polygon(res.ring).is_valid, "simplified ring must be valid"


# ---------------------------------------------------------------------------
# Hard-case fixtures: oblique boundaries, circulation continuity, partitions
# ---------------------------------------------------------------------------
#
# These three families are the geometry most likely to break the area-budgeted
# simplifier, because its greedy min-area-loss removal is a *local* decision:
# on a shallow-angle oblique edge the wrong vertex looks locally near-free while
# shifting the envelope ring globally, and a corridor pinch-point is a narrow,
# high-vertex-count feature that is both surface-expensive and load-bearing.
#
# Every assertion below respects the area budget -- a fixture that passed by
# silently breaching it would be worse than no fixture. Where the budget refuses
# an operation, the test asserts the refusal is *recorded* in `skipped`.


def _bowed_slant(n=24, bow=0.8, length=12.0, rise=2.0):
    """A shallow, over-segmented sloped edge bowed outward by ``bow``.

    Sampled at ``n + 1`` points, so the edge is a dense polyline with a real
    (sub-metre) deviation from its chord. This is the oblique case that naive
    Douglas-Peucker at a moderate ``eps`` would flatten.
    """
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append((length * t, rise * t + bow * math.sin(math.pi * t)))
    return [(0.0, 0.0), *pts, (length, 0.0)]


#: 27-vertex ring whose long sloped edge is finely sampled and bowed.
OBLIQUE_SLANT = _bowed_slant()

#: Circulation: two 6x10 bays joined by a 2-wide neck, with near-collinear
#: wobble densified along every wall (48 vertices). The wobble is cheap to
#: remove; the neck is not.
_CORRIDOR_BASE = [
    (0.0, 0.0),
    (6.0, 0.0),
    (6.0, 4.0),
    (14.0, 4.0),
    (14.0, 0.0),
    (20.0, 0.0),
    (20.0, 10.0),
    (14.0, 10.0),
    (14.0, 6.0),
    (6.0, 6.0),
    (6.0, 10.0),
    (0.0, 10.0),
]


def _densify(ring, per_edge=3, amp=0.10):
    """Insert a near-collinear wobble along each edge: high vertex count, low cost."""
    out = []
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        out.append(a)
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        nx, ny = -dy / L, dx / L
        for k in range(1, per_edge + 1):
            t = k / (per_edge + 1)
            s = amp if k % 2 else -amp
            out.append((a[0] + dx * t + nx * s, a[1] + dy * t + ny * s))
    return out


CORRIDOR_PINCH = _densify(_CORRIDOR_BASE)


def _width_at(poly, x):
    """Width of *poly* along the vertical line x=const (0.0 if disjoint)."""
    hit = poly.intersection(LineString([(x, -1.0), (x, 11.0)]))
    return 0.0 if hit.is_empty else hit.length


def _neck(poly):
    """Width of the corridor neck: 2.0 by design (walls at y=4 and y=6)."""
    return _width_at(poly, 10.0)


def _bay(poly):
    """Width of a bay: 10.0 by design."""
    return _width_at(poly, 3.0)


#: The densified corridor's *measured* neck and bay. The wobble inserted by
#: `_densify` widens the neck from the 2.0 design value, so every invariant below
#: is stated against the fixture's own geometry ("simplification must not change
#: the neck") rather than against the design intent.
_NECK_IN = _neck(Polygon(CORRIDOR_PINCH))
_BAY_IN = _bay(Polygon(CORRIDOR_PINCH))


class TestObliqueBoundary:
    """Oblique/shallow-angle sloped edges (SALI-FP F.1, F.6)."""

    def test_conservative_dp_reduces_surfaces_within_budget(self):
        """eps below the bow: DP runs, surfaces drop, budget respected."""
        res = simplify_ring(OBLIQUE_SLANT, tol=0.02, dp_eps=0.05, wall_height=3.0)
        assert res.valid
        assert res.simplified_count < res.original_count
        assert abs(res.area_delta_pct) <= res.tol * 100
        assert not [s for s in res.skipped if s["op"] == "douglas_peucker"]

    def test_aggressive_dp_breach_is_recorded_not_silently_absorbed(self):
        """eps above the bow: the candidate deviates past tol, so it is refused *and logged*.

        The deviation must never be absorbed silently -- either the operation is
        applied inside the budget, or the refusal appears in `skipped`.
        """
        res = simplify_ring(OBLIQUE_SLANT, tol=0.02, dp_eps=0.2, wall_height=3.0)
        assert res.valid
        refusals = [s for s in res.skipped if s["op"] == "douglas_peucker"]
        assert len(refusals) == 1, "an over-budget DP pass must be recorded exactly once"
        assert "> tol" in refusals[0]["reason"]
        assert abs(res.area_delta_pct) <= res.tol * 100

    @pytest.mark.parametrize("dp_eps", [None, 0.05, 0.1, 0.2, 0.5, 1.0])
    def test_budget_holds_and_every_refusal_is_logged(self, dp_eps):
        """Invariant across the eps ladder: budget always holds, refusals never vanish."""
        res = simplify_ring(OBLIQUE_SLANT, tol=0.02, dp_eps=dp_eps, wall_height=3.0)
        assert res.valid, f"dp_eps={dp_eps} produced an invalid ring"
        assert abs(res.area_delta_pct) <= res.tol * 100
        for entry in res.skipped:
            assert entry["op"] and entry["reason"], "a skip must say what and why"

    def test_oblique_slant_is_a_real_hard_case(self):
        """Guard the fixture itself: it must actually be dense and bowed.

        Without this, a future edit that flattens the fixture would leave the
        tests above passing vacuously.
        """
        poly = Polygon(OBLIQUE_SLANT)
        assert len(OBLIQUE_SLANT) >= 20, "fixture must stay over-segmented"
        assert poly.is_valid
        # Straight-chord version of the same outline. The difference is the bow
        # -- the area a Douglas-Peucker pass would flatten away, and the reason
        # the fixture can breach a budget at all.
        chord = [(0.0, 0.0), (12.0, 2.0), (12.0, 0.0)]
        bow_area = poly.area - Polygon(chord).area
        assert bow_area > 0.5, f"slant no longer bows meaningfully (bow={bow_area:.3f})"


class TestCirculationContinuity:
    """Corridor pinch-points and doglegs (SALI-FP F.2, F.7, F.8)."""

    def test_pinch_survives_simplification(self):
        """The narrow neck must not be opened into a straight run.

        A pinch is narrow and high-vertex-count: expensive by surface count, but
        load-bearing. Collapsing it changes circulation *and* the envelope ring.
        """
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        assert res.valid
        assert _neck(Polygon(res.ring)) == pytest.approx(_NECK_IN, abs=0.05)
        assert _bay(Polygon(res.ring)) == pytest.approx(_BAY_IN, abs=0.5)

    def test_surfaces_are_reduced_while_the_neck_holds(self):
        """Simplification must actually do work -- the neck survives, count drops.

        Guards against a fixture where nothing is removable, which would make the
        "pinch survives" assertion pass without exercising anything.
        """
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        assert res.original_count > 40, "fixture must stay high-vertex-count"
        assert res.simplified_count < res.original_count, "no surface was removed"
        assert _neck(Polygon(res.ring)) == pytest.approx(_NECK_IN, abs=0.05)

    def test_corridor_partition_keeps_bays_distinct(self):
        """Simplifying a corridor must not fuse the two bays into one mass."""
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        poly = Polygon(res.ring)
        assert poly.is_valid
        # still exactly two bays, joined by a neck
        assert _bay(poly) > _neck(poly), "bays must stay wider than the neck"

    @pytest.mark.parametrize("tol", [0.02, 0.05, 0.10])
    def test_pinch_survives_even_at_loose_tol(self, tol):
        """A loose total budget must not licence pinch removal.

        tol governs total area drift; the pinch is protected by the per-step cap
        and the reflex guard, so relaxing tol must not widen the neck.
        """
        res = simplify_ring(CORRIDOR_PINCH, tol=tol, max_single_step=0.0025)
        assert res.valid
        assert _neck(Polygon(res.ring)) == pytest.approx(_NECK_IN, abs=0.05)


class TestObliqueWithPartitions:
    """Oblique outer boundary with internal partitions (SALI-FP F.1).

    Exercises the union -> exterior-ring step, not just a single ring: internal
    partitions must be dropped from the BEM envelope (interior walls are not
    envelope surfaces) while the oblique outer boundary survives intact.
    """

    @staticmethod
    def _oblique_shell():
        # An oblique quadrilateral shell with a shallow sloped top edge.
        return [[0.0, 0.0], [12.0, 0.0], [12.0, 3.0], [3.0, 9.0], [0.0, 9.0]]

    @staticmethod
    def _partitions():
        return [
            [[0.5, 0.5], [6.0, 0.5], [6.0, 4.0], [0.5, 4.0]],  # room A
            [[6.5, 1.0], [11.0, 1.5], [10.0, 2.5], [6.5, 2.0]],  # room B (oblique)
        ]

    def test_union_drops_internal_partitions(self):
        """footprint_from_regions must return only the exterior ring."""
        regions = [self._oblique_shell(), *self._partitions()]
        ring = footprint_from_regions(regions)
        poly = Polygon(ring)
        assert poly.is_valid
        assert not poly.interiors, "interior partitions must not become holes"
        # exterior area == the shell alone; the rooms are interior, not additive
        assert poly.area == pytest.approx(Polygon(self._oblique_shell()).area)

    def test_simplifying_the_union_keeps_the_oblique_edge(self):
        """The union -> simplify path must preserve the oblique boundary's area."""
        regions = [self._oblique_shell(), *self._partitions()]
        ring = footprint_from_regions(regions)
        res = simplify_ring(ring, tol=0.02, wall_height=3.0)
        assert res.valid
        assert abs(res.area_delta_pct) <= res.tol * 100
        assert Polygon(res.ring).is_valid

    def test_internal_partition_only_input_is_still_a_valid_envelope(self):
        """Defect: partitions with no enclosing shell must not crash the union."""
        ring = footprint_from_regions(self._partitions())
        assert Polygon(ring).is_valid  # or [] when the union is degenerate

    def test_degenerate_partition_is_dropped(self):
        """Defect: a 2-point / sliver partition must not corrupt the exterior ring."""
        shell = self._oblique_shell()
        good = footprint_from_regions([shell, *self._partitions()])
        with_slit = footprint_from_regions([shell, *self._partitions(), [[6.0, 2.0], [6.0, 2.5]]])
        # a <3-vertex polygon is filtered out; the exterior ring is unchanged
        assert Polygon(with_slit).area == pytest.approx(Polygon(good).area)


class TestHardCaseDefectInjection:
    """Defect injection proving the hard-case tests are load-bearing.

    Each case disables a specific guard in geometry_simplify and asserts the
    fixture then *breaks* in the way the corresponding invariant test would
    catch. A regression that removed the guard cannot pass silently.
    """

    def test_pinch_collapses_when_guards_are_removed(self, monkeypatch):
        """With the cost model and reflex guard both neutralised, the neck is destroyed.

        This is the regression the TestCirculationContinuity assertions exist to
        catch. Note what it does *not* trip: the result stays a valid polygon and
        the area delta stays finite, so neither the validity check nor the area
        budget would flag it -- only a geometric probe of the neck does.
        """
        monkeypatch.setattr(gs, "_tri_area", lambda a, b, c: 0.0)
        monkeypatch.setattr(gs, "_signed_tri_area", lambda a, b, c: 1.0)
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        assert res.valid, "precondition: the broken result is still a valid polygon"
        widened = _neck(Polygon(res.ring))
        assert widened > _NECK_IN + 0.5, (
            f"expected the pinch to open once the guards are removed, "
            f"got neck={widened:.3f} vs input {abs(_NECK_IN):.3f}"
        )

    def test_reflex_guard_removal_trips_the_growth_rollback(self, monkeypatch):
        """Neutralising only the reflex guard must be caught by the hard growth limit.

        Reflex vertices are the corridor's neck. Removing them *adds* area, so the
        per-step cap and the total budget (which bound shrinkage) do not stop it;
        the final growth limit is the backstop, and it must roll the ring back
        rather than export a grown envelope.
        """
        monkeypatch.setattr(gs, "_signed_tri_area", lambda a, b, c: 1.0)
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        assert res.valid is False, "a grown envelope must be rolled back, not returned"
        assert res.simplified_area == pytest.approx(res.original_area)
        assert res.ring == [tuple(v) for v in CORRIDOR_PINCH]
        assert any("growth" in s["reason"] for s in res.skipped), "the rollback must record why"

    def test_cost_model_alone_is_not_a_safety_net(self, monkeypatch):
        """A zeroed cost model collapses the ring: the budget is what holds the line.

        Documents *why* every op is area-budgeted. With costs reported as free,
        the greedy removes until only four vertices survive, so the corridor is
        destroyed. The only reason a caller sees a safe result is the budget and
        the per-step cap acting on real costs.
        """
        monkeypatch.setattr(gs, "_tri_area", lambda a, b, c: 0.0)
        res = simplify_ring(CORRIDOR_PINCH, tol=0.02, max_single_step=0.0025)
        assert res.simplified_count <= 4
        assert res.simplified_area < 0.1 * res.original_area

    def test_bowtie_ring_is_rejected_as_degenerate(self):
        """Defect: a self-intersecting (bow-tie) ring must not reach the simplifier.

        Its shoelace area is zero, so the module raises rather than quietly
        producing an envelope. Conservation laws block export; this blocks the
        degenerate input even earlier.
        """
        bowtie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]
        with pytest.raises(ValueError, match="degenerate ring"):
            simplify_ring(bowtie, tol=0.02)

    def test_skipped_budget_breach_is_reported_with_tol_in_reason(self):
        """The budget refusal must name tol, so an auditor can see the constraint."""
        res = simplify_ring(OBLIQUE_SLANT, tol=0.02, dp_eps=0.5, wall_height=3.0)
        dp = [s for s in res.skipped if s["op"] == "douglas_peucker"]
        assert dp
        assert "tol" in dp[0]["reason"]
