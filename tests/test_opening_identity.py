"""Direct unit tests for the same-physical-opening rule (#1128; rule #664).

opening_identity is exercised indirectly by the link-dedup and validation
batteries; these tests pin its edge rules directly:

- one opening = same facade + tag + category, same along-wall centre within
  ``CROSS_LEVEL_DEDUP_TOL_M``, agreeing width, same or adjacent level;
- across levels, absolute vertical extents must overlap or touch: the
  continuous window a two-storey facade shows on two adjacent elevation
  sheets is one opening, the stacked pair split by a spandrel is two;
- an opening with no along-wall position is never matched (unplaced, not
  guessed).
"""

import opening_identity as oi
from building_model import SpaceOpening


def op(**kw) -> SpaceOpening:
    base = dict(
        id="o1",
        tag="A",
        category="window",
        width_m=1.2,
        height_m=1.5,
        sill_m=0.9,
        s_center_m=5.0,
        host_facade="N",
    )
    base.update(kw)
    return SpaceOpening(**base)


LEVELS = {"L1": (0, 0.0), "L2": (1, 3.0), "L3": (2, 6.0)}


class TestSameLevel:
    def test_same_facade_tag_position_matches(self):
        assert oi.same_opening(op(), "L1", op(id="o2"), "L1", LEVELS)

    def test_within_tolerance_matches(self):
        # 0.04 m apart, tol is 0.05 m: same physical opening
        assert oi.same_opening(op(), "L1", op(id="o2", s_center_m=5.04), "L1", LEVELS)

    def test_beyond_tolerance_is_two(self):
        assert not oi.same_opening(op(), "L1", op(id="o2", s_center_m=5.06), "L1", LEVELS)

    def test_width_disagreement_is_two(self):
        assert not oi.same_opening(op(), "L1", op(id="o2", width_m=1.4), "L1", LEVELS)

    def test_height_disagreement_on_same_level_is_two(self):
        assert not oi.same_opening(op(), "L1", op(id="o2", height_m=1.8), "L1", LEVELS)


class TestUnplacedNeverGuessed:
    def test_no_along_wall_position_is_not_matched(self):
        a = op(s_center_m=None, host_interval_m=None)
        assert oi.opening_s(a) is None
        assert not oi.same_opening(a, "L1", op(id="o2"), "L1", LEVELS)

    def test_interval_fallback_supplies_the_centre(self):
        assert oi.opening_s(op(s_center_m=None, host_interval_m=[4.0, 5.0])) == 4.5

    def test_one_side_unplaced_is_not_matched(self):
        # a placed opening never matches an unplaced one, even at the same tag
        b = op(id="o2", s_center_m=None, host_interval_m=None)
        assert not oi.same_opening(op(), "L1", b, "L1", LEVELS)


class TestCrossLevel:
    def test_two_storey_window_seen_on_two_sheets_is_one(self):
        # L1 sheet shows the lower part, L2 sheet the upper part of the same
        # continuous opening: absolute extents [0.9, 4.2] and [1.2, 4.2] overlap
        lower = op(height_m=3.3, head_m=4.2)
        upper = op(id="o2", height_m=3.0, sill_m=-1.8, head_m=1.2)
        assert oi.same_opening(lower, "L1", upper, "L2", LEVELS)

    def test_stacked_windows_split_by_spandrel_are_two(self):
        # punched pair: [0.9, 2.4] on L1 and [3.9, 5.4] on L2, spandrel between
        lower = op(height_m=1.5, head_m=2.4)
        upper = op(id="o2")
        assert not oi.same_opening(lower, "L1", upper, "L2", LEVELS)

    def test_touching_extents_still_match(self):
        # L1 ends exactly where L2 begins: [0.9, 3.0] and [3.0, 4.5]
        lower = op(height_m=2.1, head_m=3.0)
        upper = op(id="o2", sill_m=0.0, head_m=1.5)
        assert oi.same_opening(lower, "L1", upper, "L2", LEVELS)

    def test_non_adjacent_levels_are_two_even_when_extents_overlap(self):
        a = op(height_m=3.3, head_m=4.2)
        b = op(id="o2", height_m=3.0, sill_m=-1.8, head_m=1.2)
        assert not oi.same_opening(a, "L1", b, "L3", LEVELS)

    def test_missing_sill_is_not_matched_across_levels(self):
        assert not oi.same_opening(op(sill_m=None), "L1", op(id="o2"), "L2", LEVELS)


class TestHelpers:
    def test_group_key_untagged(self):
        assert oi.group_key(op(tag=None)) == ("N", "@untagged", "window")

    def test_is_wall_opening_rejects_roof_and_blank(self):
        assert not oi.is_wall_opening(op(host_facade="roof"))
        assert not oi.is_wall_opening(op(host_facade=""))
        assert oi.is_wall_opening(op())

    def test_opening_z_head_fallback(self):
        # head None: top = sill + height
        assert oi.opening_z(op(), 3.0) == (3.9, 5.4)
        assert oi.opening_z(op(sill_m=None), 3.0) is None

    def test_level_table_orders_by_elevation(self):
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        m.levels[0].elevation_z_m = 0.0
        table = oi.level_table(m)
        assert table == {"L1": (0, 0.0)}
