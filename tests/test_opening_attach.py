"""Direct unit tests for reviewer-driven opening attachment (#1128; rule #799).

ifc_import leaves openings over no space flagged for review (kind
``opening_attachment``); opening_attach lets a reviewer's edit attach them,
built the same way the automatic Tier 1 paths build the opening, and undo
it on revert. These tests pin that contract directly.
"""

import pytest

import opening_attach as oa
from building_model import BimElement, BimOpening
from tests.model_factory import make_clean_model


def wall_model(s_center_m=5.0, u=None) -> tuple:
    """Clean model plus one wall element hosting one window opening."""
    m = make_clean_model()
    bo = BimOpening(
        id="bo-1",
        category="window",
        tag="A",
        width_m=1.2,
        height_m=1.5,
        sill_m=0.9,
        s_center_m=s_center_m,
        u_value_w_m2k=u,
    )
    el = BimElement(
        global_id="wall-1",
        ifc_class="IfcWall",
        level_id="L1",
        length_m=20.0,
        openings=[bo],
    )
    m.bim_elements.append(el)
    return m, el, bo


def skylight_model() -> tuple:
    """Clean model plus one slab element hosting one skylight over a void."""
    m = make_clean_model()
    bo = BimOpening(
        id="bo-2",
        category="skylight",
        width_m=2.0,
        height_m=3.0,
        plan_center_m=[2.5, 3.0],
        tilt_deg=0.0,
        azimuth_deg=0.0,
    )
    el = BimElement(global_id="slab-1", ifc_class="IfcSlab", level_id="L1", openings=[bo])
    m.bim_elements.append(el)
    return m, el, bo


class TestCheck:
    def test_missing_opening_refuses(self):
        m, _, _ = wall_model()
        with pytest.raises(ValueError, match="not in the model"):
            oa.check_opening_attachment(m, "nope", "L1-101")

    def test_missing_space_refuses(self):
        m, _, _ = wall_model()
        with pytest.raises(ValueError, match="not a space id"):
            oa.check_opening_attachment(m, "bo-1", "L1-999")

    def test_level_mismatch_refuses(self):
        m, _, _ = wall_model()
        m.bim_elements[0].level_id = "L2"
        with pytest.raises(ValueError, match="is on level"):
            oa.check_opening_attachment(m, "bo-1", "L1-101")

    def test_already_attached_may_move(self):
        m, _, bo = wall_model()
        oa.attach_opening(m, "bo-1", "L1-101")
        # moving it to the neighbouring room is a legal review edit
        oa.check_opening_attachment(m, "bo-1", "L1-102")


class TestAttachWallOpening:
    def test_attach_builds_the_tier1_opening(self):
        m, el, bo = wall_model()
        oa.attach_opening(m, "bo-1", "L1-101")
        sp = m.spaces["L1-101"]
        assert [o.id for o in sp.openings if o.id == "bo-1"] == ["bo-1"]
        o = next(o for o in sp.openings if o.id == "bo-1")
        # same fields the automatic Tier 1 paths build: sill and along-wall
        # interval kept, size and tag carried, reviewer resolved the flag
        assert (o.id, o.tag, o.category) == ("bo-1", "A", "window")
        assert o.width_m == 1.2 and o.height_m == 1.5 and o.sill_m == 0.9
        assert o.s_center_m == 5.0
        assert o.host_interval_m == [4.4, 5.6]
        assert o.needs_review is False
        assert o.adjacent_space_id is None

    def test_attach_sets_construction_id_from_stated_u(self):
        m, _, _ = wall_model(u=2.0)
        oa.attach_opening(m, "bo-1", "L1-101")
        got = next(o for o in m.spaces["L1-101"].openings if o.id == "bo-1")
        assert got.construction_id == "IFC-WINDOW-U2.0000"

    def test_interval_clamps_at_the_wall_end(self):
        m, _, _ = wall_model(s_center_m=19.5)
        oa.attach_opening(m, "bo-1", "L1-101")
        got = next(o for o in m.spaces["L1-101"].openings if o.id == "bo-1")
        assert got.host_interval_m == [18.9, 20.0]

    def test_undetermined_along_wall_builds_without_interval(self):
        # the wall item whose along-wall direction is undetermined is
        # record-only upstream; an attach here builds the opening but never
        # guesses a position
        m, _, _ = wall_model(s_center_m=None)
        oa.attach_opening(m, "bo-1", "L1-101")
        o = next(o for o in m.spaces["L1-101"].openings if o.id == "bo-1")
        assert o.host_interval_m is None and o.s_center_m is None
        assert o.needs_review is False

    def test_attach_moves_from_another_room(self):
        m, _, _ = wall_model()
        oa.attach_opening(m, "bo-1", "L1-101")
        oa.attach_opening(m, "bo-1", "L1-102")
        assert oa.host_space(m, "bo-1") is m.spaces["L1-102"]
        assert all(o.id != "bo-1" for o in m.spaces["L1-101"].openings)

    def test_attach_of_unknown_ids_is_a_noop(self):
        m, _, _ = wall_model()
        before = [o.id for o in m.spaces["L1-101"].openings]
        oa.attach_opening(m, "nope", "L1-101")
        oa.attach_opening(m, "bo-1", "L1-999")
        assert [o.id for o in m.spaces["L1-101"].openings] == before


class TestRevertAndSkylight:
    def test_revert_removes_cleanly(self):
        m, _, _ = wall_model()
        oa.attach_opening(m, "bo-1", "L1-101")
        oa.un_attach(m, "bo-1")
        assert all(o.id != "bo-1" for o in m.spaces["L1-101"].openings)
        oa.un_attach(m, "bo-1")  # idempotent

    def test_skylight_attach_keeps_plan_geometry_and_daylights_the_room(self):
        m, _, _ = skylight_model()
        oa.attach_opening(m, "bo-2", "L1-101")
        sp = m.spaces["L1-101"]
        o = next(o for o in sp.openings if o.id == "bo-2")
        assert o.category == "skylight" and o.host_facade == "roof"
        assert o.plan_center_m == [2.5, 3.0]
        assert o.tilt_deg == 0.0 and o.azimuth_deg == 0.0
        assert o.area_m2 == 6.0 and o.needs_review is False
        # daylight re-ran on the room under the skylight
        assert sp.daylight.toplit, "skylight over the room should be top-lit"

    def test_skylight_revert_leaves_no_stale_toplit(self):
        m, _, _ = skylight_model()
        oa.attach_opening(m, "bo-2", "L1-101")
        oa.un_attach(m, "bo-2")
        sp = m.spaces["L1-101"]
        assert all(o.id != "bo-2" for o in sp.openings)
        assert not sp.daylight.toplit and sp.daylight.toplit_m2 == 0.0
