"""Tests for the shared geometry kernel (geometry/geometry_kernel.py, #1338).

The kernel is the single home of `_shoelace`, `_ensure_ccw` and `_fmt`; the
sign convention, the CCW reversal rule and the 4-decimal formatting are
pinned here once, so the per-module re-exports stay honest.
"""

import pytest

from geometry.geometry_kernel import _ensure_ccw, _fmt, _shoelace

# ---------------------------------------------------------------------------
# _shoelace: sign convention (positive = CCW, negative = CW)
# ---------------------------------------------------------------------------

CCW_SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
CW_SQUARE = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


class TestShoelace:
    def test_ccw_ring_is_positive(self):
        assert _shoelace(CCW_SQUARE) == pytest.approx(1.0)

    def test_cw_ring_is_negative(self):
        assert _shoelace(CW_SQUARE) == pytest.approx(-1.0)

    def test_reversing_a_ring_flips_the_sign(self):
        assert _shoelace(list(reversed(CCW_SQUARE))) == pytest.approx(-_shoelace(CCW_SQUARE))

    def test_degenerate_ring_is_zero(self):
        # Fewer than 3 vertices cannot enclose an area: the signed area is 0.
        assert _shoelace([]) == 0.0
        assert _shoelace([(0.0, 0.0)]) == 0.0
        assert _shoelace([(0.0, 0.0), (1.0, 1.0)]) == 0.0

    def test_collinear_ring_is_zero(self):
        assert _shoelace([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]) == 0.0


# ---------------------------------------------------------------------------
# _ensure_ccw: reversal rule (reverse only when the signed area is negative)
# ---------------------------------------------------------------------------


class TestEnsureCcw:
    def test_ccw_ring_is_unchanged(self):
        assert _ensure_ccw(CCW_SQUARE) == CCW_SQUARE

    def test_cw_ring_is_reversed(self):
        assert _ensure_ccw(CW_SQUARE) == list(reversed(CW_SQUARE))

    def test_result_is_always_a_list(self):
        assert isinstance(_ensure_ccw(tuple(CCW_SQUARE)), list)
        assert isinstance(_ensure_ccw(tuple(CW_SQUARE)), list)

    def test_degenerate_ring_is_left_alone(self):
        # Signed area 0 is not negative -> no reversal, no crash.
        for ring in ([], [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)]):
            assert _ensure_ccw(ring) == list(ring)

    def test_input_is_not_mutated(self):
        ring = list(CW_SQUARE)
        _ensure_ccw(ring)
        assert ring == CW_SQUARE

    def test_result_has_non_negative_signed_area(self):
        assert _shoelace(_ensure_ccw(CW_SQUARE)) >= 0


# ---------------------------------------------------------------------------
# _fmt: shared number formatting
# ---------------------------------------------------------------------------


class TestFmt:
    def test_four_decimals(self):
        assert _fmt(1.23456) == "1.2346"
        assert _fmt(0.0) == "0.0000"
        assert _fmt(-2.5) == "-2.5000"

    def test_rounds_half_to_even_like_python_format(self):
        assert _fmt(0.12345) == f"{0.12345:.4f}"
        assert _fmt(12.0) == "12.0000"


# ---------------------------------------------------------------------------
# The kernel is the only definition: every former definer re-exports it.
# ---------------------------------------------------------------------------


class TestSingleDefinition:
    def test_former_definers_re_export_the_kernel(self):
        import bem.bem_export
        import bem.bem_geometry
        import bem.bem_helpers
        import ifc.ifc_import
        import run_pipeline

        assert bem.bem_geometry._shoelace is _shoelace
        assert bem.bem_geometry._ensure_ccw is _ensure_ccw
        assert bem.bem_geometry._fmt is _fmt
        assert bem.bem_helpers._fmt is _fmt
        assert bem.bem_export._shoelace is _shoelace
        assert bem.bem_export._ensure_ccw is _ensure_ccw
        assert ifc.ifc_import._shoelace is _shoelace
        assert run_pipeline._ensure_ccw is _ensure_ccw
