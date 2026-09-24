"""Tests for jesse.py skeleton invariant functions and gd_complex_row detection."""

import numpy as np

from jesse import (
    is_complex_invariant,
    skeleton_invariants,
    zhang_suen,
)


class TestIsComplexInvariant:
    """Table 9.4 simple glyphs should NOT be flagged as complex.
    Compound/ambiguous glyphs SHOULD be flagged.
    """

    def test_circularity_not_complex(self):
        # E=0, J=0, b1=1 → simple
        inv = dict(endpoints=0, t_junctions=0, x_junctions=0, holes=1)
        assert not is_complex_invariant(inv)

    def test_concentricity_not_complex(self):
        # E=0, J=0, b1=2 → simple
        inv = dict(endpoints=0, t_junctions=0, x_junctions=0, holes=2)
        assert not is_complex_invariant(inv)

    def test_perpendicularity_not_complex(self):
        # E=3, JT=1, b1=0 → simple (one T-junction is within threshold)
        inv = dict(endpoints=3, t_junctions=1, x_junctions=0, holes=0)
        assert not is_complex_invariant(inv)

    def test_straightness_not_complex(self):
        # E=2, J=0, b1=0 → simple
        inv = dict(endpoints=2, t_junctions=0, x_junctions=0, holes=0)
        assert not is_complex_invariant(inv)

    def test_parallelism_not_complex(self):
        # E=4 (at upper bound), no junctions, no holes → simple
        inv = dict(endpoints=4, t_junctions=0, x_junctions=0, holes=0)
        assert not is_complex_invariant(inv)

    def test_too_many_endpoints_is_complex(self):
        # E=5 exceeds upper bound of 4 → complex
        inv = dict(endpoints=5, t_junctions=0, x_junctions=0, holes=0)
        assert is_complex_invariant(inv)

    def test_too_many_t_junctions_is_complex(self):
        # JT=2 exceeds threshold of 1 → complex
        inv = dict(endpoints=3, t_junctions=2, x_junctions=0, holes=0)
        assert is_complex_invariant(inv)

    def test_too_many_x_junctions_is_complex(self):
        # JX=2 exceeds threshold of 1 → complex
        inv = dict(endpoints=3, t_junctions=0, x_junctions=2, holes=0)
        assert is_complex_invariant(inv)

    def test_too_many_holes_is_complex(self):
        # b1=3 exceeds threshold of 2 → complex
        inv = dict(endpoints=0, t_junctions=0, x_junctions=0, holes=3)
        assert is_complex_invariant(inv)

    def test_compound_both_t_and_x_junctions_is_complex(self):
        # True Position style: both T and X junctions present → compound → complex
        inv = dict(endpoints=4, t_junctions=4, x_junctions=1, holes=1)
        assert is_complex_invariant(inv)

    def test_compound_style_complex(self):
        # Compound glyph: E=4, JT=4, JX=1, b1=1 (True Position convention)
        inv = dict(endpoints=4, t_junctions=4, x_junctions=1, holes=1)
        assert is_complex_invariant(inv)

    def test_missing_keys_default_to_zero(self):
        # Empty dict → all zeros → not complex
        inv = {}
        assert not is_complex_invariant(inv)

    def test_partial_keys(self):
        # Only endpoints provided
        inv = dict(endpoints=10)
        assert is_complex_invariant(inv)


class TestSkeletonInvariantsSimpleGlyphs:
    """Verify that clean 1px glyphs reproduce Table 9.4 exactly."""

    def _bresenham_circle(self, n, cx, cy, r):
        img = np.zeros((n, n), np.uint8)
        x, y, d = r, 0, 1 - r
        while y <= x:
            for px, py in [
                (cx + x, cy + y),
                (cx - x, cy + y),
                (cx + x, cy - y),
                (cx - x, cy - y),
                (cx + y, cy + x),
                (cx - y, cy + x),
                (cx + y, cy - x),
                (cx - y, cy - x),
            ]:
                if 0 <= px < n and 0 <= py < n:
                    img[py, px] = 1
            y += 1
            if d <= 0:
                d += 2 * y + 1
            else:
                x -= 1
                d += 2 * (y - x) + 1
        return img

    def test_circularity(self):
        # ○ E=0, J=0, b1=1, chi=0
        img = self._bresenham_circle(112, 56, 56, 30)
        inv = skeleton_invariants(zhang_suen(img))
        assert inv["endpoints"] == 0
        assert inv["t_junctions"] == 0
        assert inv["x_junctions"] == 0
        assert inv["holes"] == 1
        assert inv["chi"] == 0
        assert not is_complex_invariant(inv)

    def test_perpendicularity(self):
        # ⊥ E=3, JT=1, b1=0
        img = np.zeros((112, 112), np.uint8)
        img[30, 20:92] = 1
        img[30:92, 56] = 1
        inv = skeleton_invariants(zhang_suen(img))
        assert inv["endpoints"] == 3
        assert inv["t_junctions"] == 1
        assert inv["holes"] == 0
        assert not is_complex_invariant(inv)

    def test_straightness(self):
        # — E=2, J=0, b1=0
        img = np.zeros((112, 112), np.uint8)
        img[56, 20:92] = 1
        inv = skeleton_invariants(zhang_suen(img))
        assert inv["endpoints"] == 2
        assert inv["t_junctions"] == 0
        assert inv["x_junctions"] == 0
        assert inv["holes"] == 0
        assert not is_complex_invariant(inv)

    def test_parallelism(self):
        # ∥ E=4, J=0, b1=0
        img = np.zeros((112, 112), np.uint8)
        img[20:92, 40] = 1
        img[20:92, 72] = 1
        inv = skeleton_invariants(zhang_suen(img))
        assert inv["endpoints"] == 4
        assert inv["t_junctions"] == 0
        assert inv["x_junctions"] == 0
        assert inv["holes"] == 0
        assert not is_complex_invariant(inv)

    def test_compound_crosshair_is_complex(self):
        # Circle with cross: both T/X junctions → compound glyph
        img = np.zeros((112, 112), np.uint8)
        cx = self._bresenham_circle(112, 56, 56, 20)
        img[30, 20:92] = 1  # horizontal bar
        img[30:92, 56] = 1  # vertical bar
        img |= cx
        inv = skeleton_invariants(zhang_suen(img))
        # This compound glyph has both T and X junctions → complex
        assert is_complex_invariant(inv)
