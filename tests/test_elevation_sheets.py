"""Elevation sheets read in the drawing-set run (#810, first slice)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_fixtures import rect, text  # noqa: E402
from test_grid_detect import bubble, dashed  # noqa: E402
from test_plan_walls import T_EXT, K  # noqa: E402
from test_real_set import SCALE_NOTE, _arch_win, _set, _tb, _window_schedule  # noqa: E402

import elevation_sheets as ES  # noqa: E402
import real_set  # noqa: E402

L = 10.0 + T_EXT  # exterior length of the 10 m x 6 m test shell
E0, BASE = 100.0, 300.0  # elevation drawing origin on its sheet, pt (y up)
WIN_X = 6.4  # the plan's south window gap runs 6.4..7.6 m (test coords)


def _elev(title, wins=((WIN_X + T_EXT / 2, 0.9, 1.2, 1.5),), length=L, doors=(), extra=""):
    """An elevation sheet: outline, windows (s, sill, w, h) and doors (s, w, h)."""
    body = rect(E0, BASE, length * K, 3.5 * K, 1.5)
    for s, sill, w, h in wins:
        body += rect(E0 + s * K, BASE + sill * K, w * K, h * K)
        body += rect(E0 + s * K + 3, BASE + sill * K + 3, w * K - 6, h * K - 6)  # glass
    for s, w, h in doors:
        body += rect(E0 + s * K, BASE, w * K, h * K)
    return _tb("A-201", title) + text(100, 48, SCALE_NOTE, 8) + body + extra


W1 = ("W1", "4'-0\"", "5'-0\"", "FIXED")  # 1.219 m x 1.524 m, fits the 1.2 m plan gap
W1_H = 60 * 0.0254


def _run(tmp_path, *elevs, plan_extra="", rows=(W1,)):
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1) + plan_extra, *elevs]
    if rows:
        pages.append(_tb("A-601", "WINDOW SCHEDULE") + _window_schedule(list(rows)))
    model, rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    return model, rep, tmp_path / "out" / "sheets"


@pytest.mark.parametrize(
    "title,facade",
    [
        ("SOUTH ELEVATION", "south"),
        ("NORTH  ELEVATION", "north"),
        ("East Elevation", "east"),
        ("EXTERIOR ELEVATIONS", None),
        ("NORTH & SOUTH ELEVATIONS", None),
        ("FIRST FLOOR PLAN", None),
    ],
)
def test_facade_from_title(title, facade):
    assert ES.facade_from_title(title) == facade


def test_south_elevation_window_is_registered_to_the_plan(tmp_path):
    model, rep, sheets = _run(tmp_path, _elev("SOUTH ELEVATION", doors=((1.0, 0.9, 2.1),)))
    st = next(s for s in rep.sheets if s.number == "A-201")
    assert st.stages["elevation"]["status"] == "ok", st.stages["elevation"]
    assert st.stages["elevation"]["windows"] == 1 and st.stages["elevation"]["doors"] == 1
    er = json.loads((sheets / st.stages["elevation"]["file"]).read_text())
    assert er["facade"] == "south" and er["registration"]["method"] == "geometric"
    (w,) = [o for o in er["openings"] if o["kind"] == "window"]
    # s from the facade's west end (exterior face), z from the outline base
    assert w["s0_m"] == pytest.approx(WIN_X + T_EXT / 2, abs=0.02)
    assert w["width_m"] == pytest.approx(1.2, abs=0.02)
    assert w["sill_m"] == pytest.approx(0.9, abs=0.02)
    assert w["head_m"] == pytest.approx(2.4, abs=0.02)
    (d,) = [o for o in er["openings"] if o["kind"] == "door"]
    assert d["sill_m"] == pytest.approx(0.0, abs=0.02) and d["id"] == "A-201-D1"
    assert rep.to_dict()["elevations"][0]["windows"] == 1
    # the plan has no door on the south wall, so the elevation's door goes to review
    (rq,) = [r for r in model.review_queue if r.id.startswith("rq-elev-")]
    assert rq.id == "rq-elev-A-201-A-201-D1" and "has no plan door" in rq.description
    (sh,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "carry no named floor level marks" in sh.description


def test_north_elevation_runs_right_to_left(tmp_path):
    # seen from outside, the north facade's west end is on the drawing's right
    _m, rep, sheets = _run(tmp_path, _elev("NORTH ELEVATION", wins=((1.0, 0.9, 1.2, 1.5),)))
    st = next(s for s in rep.sheets if s.number == "A-201")
    er = json.loads((sheets / st.stages["elevation"]["file"]).read_text())
    (w,) = er["openings"]
    assert w["s0_m"] == pytest.approx(L - 2.2, abs=0.02)
    assert w["s1_m"] == pytest.approx(L - 1.0, abs=0.02)


def test_shared_grid_registers_the_elevation(tmp_path):
    # grid lines 1 and 2 at plan x = 2 m and 8 m; the elevation shows both
    from test_plan_walls import _pt

    plan_g = ""
    for lab, x in (("1", 2.0), ("2", 8.0)):
        (px, y0), (_, y1) = _pt(x, -2.0), _pt(x, 8.0)
        plan_g += dashed(px, y0, px, y1) + bubble(px, y1 + 10, lab)
    elev_g = ""
    for lab, x in (("1", 2.0), ("2", 8.0)):
        u = E0 + (x + T_EXT / 2) * K
        elev_g += dashed(u, BASE - 20, u, BASE + 4.5 * K) + bubble(u, BASE + 4.5 * K + 10, lab)
    model, rep, sheets = _run(tmp_path, _elev("SOUTH ELEVATION", extra=elev_g), plan_extra=plan_g)
    st = next(s for s in rep.sheets if s.number == "A-201")
    assert st.stages["elevation"]["status"] == "ok", st.stages["elevation"]
    er = json.loads((sheets / st.stages["elevation"]["file"]).read_text())
    assert er["registration"]["method"] == "grid", er["registration"]
    assert er["registration"]["grid_labels_used"] == ["1", "2"]
    (w,) = [o for o in er["openings"] if o["kind"] == "window"]
    assert w["s0_m"] == pytest.approx(WIN_X + T_EXT / 2, abs=0.02)
    assert rep.to_dict()["elevations"][0]["confidence"] > 0.9


def test_unnamed_elevation_goes_to_review(tmp_path):
    model, rep, _s = _run(tmp_path, _elev("EXTERIOR ELEVATIONS"))
    st = next(s for s in rep.sheets if s.number == "A-201")
    assert st.stages["elevation"]["status"] == "failed"
    assert "names no single facade" in st.stages["elevation"]["reason"]
    (rq,) = [r for r in model.review_queue if r.id == "rq-elev-A-201"]
    assert rq.kind == "elevation_extraction" and rq.target["id"] == "A-201"


def test_outline_length_that_disagrees_with_the_plan_goes_to_review(tmp_path):
    model, _rep, _s = _run(tmp_path, _elev("SOUTH ELEVATION", length=12.0))
    (rq,) = [r for r in model.review_queue if "12.00 m wide on the elevation" in r.description]
    assert rq.id.startswith("rq-elev-A-201-")


# --- joined to the plan's openings (#810, second slice) ---------------------


def _ops(model):
    return [o for sp in model.spaces.values() for o in sp.openings]


def test_elevation_gives_the_plan_window_its_sill_and_head(tmp_path):
    model, rep, _s = _run(tmp_path, _elev("SOUTH ELEVATION"))
    (op,) = _ops(model)
    assert op.tag == "W1" and op.host_facade == "south"
    assert op.sill_m == pytest.approx(0.9, abs=0.02)
    assert op.head_m == pytest.approx(0.9 + W1_H, abs=0.02)  # sill + the scheduled height
    assert op.height_m == pytest.approx(W1_H, abs=1e-3)  # schedule size stands
    (h,) = [p for p in op.history if p.method == "elevation_join"]
    assert h.sheet_id == "A-201" and "sill 0.90 m" in h.note
    e = rep.to_dict()["elevations"][0]
    assert (e["matched"], e["unmatched_elevation"], e["unmatched_plan"]) == (1, 0, 0)
    assert not [r for r in model.review_queue if r.id.startswith("rq-elev-")]


def test_height_that_disagrees_with_the_schedule_goes_to_review(tmp_path):
    model, _rep, _s = _run(
        tmp_path, _elev("SOUTH ELEVATION", wins=((WIN_X + T_EXT / 2, 0.9, 1.2, 2.1),))
    )
    (op,) = _ops(model)
    assert op.sill_m == pytest.approx(0.9, abs=0.02)
    assert op.head_m == pytest.approx(0.9 + W1_H, abs=0.02)
    (rq,) = [r for r in model.review_queue if r.id.startswith("rq-elev-")]
    assert "2.10 m tall on the elevation, 1.52 m scheduled" in rq.description
    assert {k: rq.target[k] for k in ("kind", "id", "sheet")} == {
        "kind": "opening",
        "id": op.id,
        "sheet": "A-201",
    }
    assert [e["side"] for e in rq.target["ends"]] == ["elevation", "plan"]


def test_window_far_from_any_plan_window_is_not_joined(tmp_path):
    model, rep, _s = _run(tmp_path, _elev("SOUTH ELEVATION", wins=((2.0, 0.9, 1.2, 1.5),)))
    (op,) = _ops(model)
    assert op.sill_m is None and op.head_m is None
    ids = sorted(r.id for r in model.review_queue if r.id.startswith("rq-elev-"))
    assert ids == ["rq-elev-A-201-A-201-W1", f"rq-elev-A-201-{op.id}"]
    e = rep.to_dict()["elevations"][0]
    assert (e["matched"], e["unmatched_elevation"], e["unmatched_plan"]) == (0, 1, 1)


def test_other_facades_are_left_alone(tmp_path):
    # a north elevation says nothing about the south window
    model, _rep, _s = _run(tmp_path, _elev("NORTH ELEVATION", wins=()))
    (op,) = _ops(model)
    assert op.sill_m is None
    assert not [r for r in model.review_queue if r.id.startswith("rq-elev-")]


# --- storey height from level marks (#810, second slice) --------------------

FT12 = 12 * 12 * 0.0254  # 3.6576 m
FT10 = 10 * 12 * 0.0254


def _mark(name, value, z_m, x=None):
    """A level mark at height z_m above the outline base: name over value."""
    x = E0 + L * K + 12 if x is None else x
    y = BASE + z_m * K + 2
    out = text(x, y, value, 8)
    return out + (text(x, y + 10, name, 8) if name else "")


def _marks(storey=FT12, top="12'-0\"", extra=""):
    return _mark("FIRST FLOOR", "EL. 100'-0\"", 0.0) + _mark("ROOF", f"EL. 1{top}", storey) + extra


def _elev_marks(title="SOUTH ELEVATION", **kw):
    return _elev(title, extra=_marks(**kw))


def test_level_marks_read_and_checked_against_the_drawing(tmp_path):
    from test_grid_detect import _sheet

    sheet = _sheet(tmp_path, _elev_marks(extra=_mark(None, "EL. 150'-0\"", 0.0, x=E0 + 40)),
                   "e", w=1728, h=1152)  # fmt: skip
    marks = ES.level_marks(sheet, 96 * 0.0254 / 72)
    # the stray "EL. 150'-0\"" sits on the base, so its value disagrees with its height
    assert [m.name for m in marks] == ["FIRST FLOOR", "ROOF"]
    assert marks[0].value_m == pytest.approx(100 * 12 * 0.0254)
    assert ES.storey_heights(marks) == [pytest.approx(FT12, abs=1e-3)]


@pytest.mark.parametrize(
    "s,m",
    [
        ("EL. 100'-0\"", 30.48),
        ("EL 112'-6\"", (112 * 12 + 6) * 0.0254),
        ("ELEV. 98'-4 1/2\"", (98 * 12 + 4.5) * 0.0254),
        ("EL. +3.600", 3.6),
        ("ELEVATION: 0.000", 0.0),
    ],
)
def test_level_values(s, m):
    assert ES._value_m(ES.LEVEL_VALUE_RE.search(s)) == pytest.approx(m, abs=1e-4)


def test_parapet_and_plate_are_not_storeys():
    mk = ES.LevelMark
    marks = [mk("FIRST FLOOR", 0.0, 0, ""), mk("T.O. PLATE", 2.9, 0, ""),
             mk("ROOF", 3.6, 0, ""), mk("PARAPET", 4.2, 0, ""), mk(None, 5.0, 0, "")]  # fmt: skip
    assert ES.storey_heights(marks) == [3.6]


def test_storey_height_comes_from_the_elevation(tmp_path):
    model, rep, _s = _run(tmp_path, _elev_marks())
    assert model.levels[0].wall_height_m == pytest.approx(FT12, abs=1e-3)
    assert rep.levels[0]["height_source"] == "elevation_level_marks"
    assert any("from elevation level marks (A-201: 3.66 m)" in n for n in rep.notes)
    assert not [r for r in model.review_queue if r.id == "rq-storey-height"]
    sp = next(iter(model.spaces.values()))
    assert sp.volume_m3 == pytest.approx(sp.area_m2 * FT12, rel=1e-3)


def test_config_storey_height_beats_the_marks(tmp_path):
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1), _elev_marks()]
    model, rep = real_set.build_set_model(
        _set(tmp_path, pages), tmp_path / "out", storey_height_m=4.0
    )
    assert model.levels[0].wall_height_m == 4.0 and rep.levels[0]["height_source"] == "config"


def test_marks_that_disagree_keep_the_default_and_say_why(tmp_path):
    e2 = _elev_marks("NORTH ELEVATION", storey=FT10, top="10'-0\"").replace("A-201", "A-202")
    model, rep, _s = _run(tmp_path, _elev_marks(), e2)
    assert rep.levels[0]["height_source"] == "storey_height_default"
    (rq,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "level marks disagree (A-201: 3.66 m; A-202: 3.05 m)" in rq.description


def test_unnamed_marks_give_no_storey_height(tmp_path):
    extra = _mark(None, "EL. 100'-0\"", 0.0) + _mark(None, "EL. 112'-0\"", FT12)
    model, rep, _s = _run(tmp_path, _elev("SOUTH ELEVATION", extra=extra))
    assert rep.levels[0]["height_source"] == "storey_height_default"
    (rq,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "carry no named floor level marks" in rq.description


# --- both ends on review items, for the review page (#810, third slice) -----


def _plan_pt(x, y):
    from test_plan_walls import _pt

    px, py = _pt(x, y)  # PDF points, y up
    return px, 1152 - py  # sheet points, y down (test sheets are 1728 x 1152)


def test_size_mismatch_item_names_both_sheets(tmp_path):
    model, _rep, _s = _run(
        tmp_path, _elev("SOUTH ELEVATION", wins=((WIN_X + T_EXT / 2, 0.9, 1.2, 2.1),))
    )
    (rq,) = [r for r in model.review_queue if r.id.startswith("rq-elev-")]
    ev, pl = rq.target["ends"]
    assert (ev["sheet"], ev["sheet_id"], ev["el"]) == ("sheet_002.json", "A-201", "elev:A-201-W1")
    # the window's box on the elevation sheet, in points (y down)
    x0, y0, x1, y1 = ev["box"]
    assert x0 == pytest.approx(E0 + (WIN_X + T_EXT / 2) * K, abs=1)
    assert x1 - x0 == pytest.approx(1.2 * K, abs=1) and y1 - y0 == pytest.approx(2.1 * K, abs=1)
    (op,) = _ops(model)
    assert (pl["sheet"], pl["sheet_id"], pl["el"]) == ("sheet_001.json", "A-101", f"op:{op.id}")
    # the gap's centre on the plan sheet: x = 7 m on the south wall (y = 0)
    assert pl["point"] == pytest.approx(list(_plan_pt(WIN_X + 0.6, 0.0)), abs=1.5)


def test_unmatched_items_carry_the_end_they_have(tmp_path):
    model, _rep, _s = _run(tmp_path, _elev("SOUTH ELEVATION", wins=((2.0, 0.9, 1.2, 1.5),)))
    (op,) = _ops(model)
    ev_only = next(r for r in model.review_queue if r.id == "rq-elev-A-201-A-201-W1")
    assert [e["side"] for e in ev_only.target["ends"]] == ["elevation"]
    plan_only = next(r for r in model.review_queue if r.id == f"rq-elev-A-201-{op.id}")
    assert [e["side"] for e in plan_only.target["ends"]] == ["plan"]
    assert plan_only.target["ends"][0]["el"] == f"op:{op.id}"


def test_elevation_json_records_the_matched_plan_opening(tmp_path):
    model, rep, sheets = _run(tmp_path, _elev("SOUTH ELEVATION", doors=((1.0, 0.9, 2.1),)))
    (op,) = _ops(model)
    st = next(s for s in rep.sheets if s.number == "A-201")
    er = json.loads((sheets / st.stages["elevation"]["file"]).read_text())
    assert er["plan"] == {"sheet": "sheet_001.json", "sheet_id": "A-101"}
    by_id = {o["id"]: o for o in er["openings"]}
    assert by_id["A-201-W1"]["plan_opening"] == op.id
    assert by_id["A-201-W1"]["plan_point"] == pytest.approx(
        list(_plan_pt(WIN_X + 0.6, 0.0)), abs=1.5
    )
    assert by_id["A-201-D1"]["plan_opening"] is None and by_id["A-201-D1"]["plan_point"] is None


# --- #817: join items use the review queue's window kinds --------------------


def test_join_items_use_the_window_kinds(tmp_path):
    import run_review

    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    m1, _r, _s = _run(
        tmp_path / "a", _elev("SOUTH ELEVATION", wins=((WIN_X + T_EXT / 2, 0.9, 1.2, 2.1),))
    )
    (mismatch,) = [r for r in m1.review_queue if r.id.startswith("rq-elev-")]
    assert mismatch.kind == "elevation_conflict" and len(mismatch.target["ends"]) == 2
    m2, _r, _s = _run(tmp_path / "b", _elev("SOUTH ELEVATION", wins=((2.0, 0.9, 1.2, 1.5),)))
    (op,) = _ops(m2)
    kinds = {r.id: r.kind for r in m2.review_queue}
    assert kinds["rq-elev-A-201-A-201-W1"] == "window_room_link"
    assert kinds[f"rq-elev-A-201-{op.id}"] == "elevation_conflict"
    # same triage task as the building-JSON path's items and the old kind
    tasks = {run_review._kind_to_task(k) for k in ("elevation_conflict", "window_room_link")}
    assert tasks == {run_review._kind_to_task("elevation_extraction")} == {"route_to_review"}


# --- per-level storey heights from level marks (#814) ------------------------


@pytest.mark.parametrize(
    "name,o",
    [("FIRST FLOOR", 1), ("GROUND FLOOR", 1), ("2ND FLOOR", 2), ("LEVEL 3", 3),
     ("5TH FLOOR", 5), ("BASEMENT", 0), ("ROOF", None), (None, None)],
)  # fmt: skip
def test_storey_ordinal(name, o):
    assert ES.storey_ordinal(name) == o


def test_storey_steps_name_each_storey():
    mk = ES.LevelMark
    marks = [mk("FIRST FLOOR", 0.0, 0, ""), mk("SECOND FLOOR", 4.5, 0, ""),
             mk("ROOF", 8.1, 0, ""), mk("PARAPET", 8.7, 0, "")]  # fmt: skip
    assert ES.storey_steps(marks) == [(1, 4.5), (2, 3.6)]


def _tall(title="SOUTH ELEVATION", roof=8.1, sheet="A-201"):
    """First floor 4.5 m, second floor to roof ``roof - 4.5`` m, stated by marks in metres."""
    extra = (_mark("FIRST FLOOR", "EL. +0.000", 0.0) + _mark("SECOND FLOOR", "EL. +4.500", 4.5)
             + _mark("ROOF", f"EL. +{roof:.3f}", roof))  # fmt: skip
    return _elev(title, extra=extra).replace("A-201", sheet)


def _two_storey(tmp_path, *elevs, storey_height_m=None):
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1), _arch_win("A-102", "SECOND FLOOR PLAN", 2),
             *elevs]  # fmt: skip
    return real_set.build_set_model(
        _set(tmp_path, pages), tmp_path / "out", storey_height_m=storey_height_m
    )


def test_each_level_takes_its_own_height_from_the_marks(tmp_path):
    model, rep = _two_storey(tmp_path, _tall())
    l1, l2 = model.levels
    assert (l1.id, l2.id) == ("L1", "L2")
    assert l1.wall_height_m == pytest.approx(4.5) and l2.wall_height_m == pytest.approx(3.6)
    assert l2.elevation_z_m == pytest.approx(4.5)
    assert [lv["height_source"] for lv in rep.levels] == ["elevation_level_marks"] * 2
    assert [lv["height_m"] for lv in rep.levels] == [pytest.approx(4.5), pytest.approx(3.6)]
    for sp in model.spaces.values():
        h = 4.5 if sp.level_id == "L1" else 3.6
        assert sp.volume_m3 == pytest.approx(sp.area_m2 * h, rel=1e-3)
    for w in model.envelope:
        assert w.height_m == pytest.approx(4.5 if w.id.startswith("L1-") else 3.6)
    assert any("storey heights from elevation level marks: L1 4.5 m, L2 3.6 m" in n
               for n in rep.notes)  # fmt: skip
    assert not [r for r in model.review_queue if r.id == "rq-storey-height"]


def test_marks_that_disagree_on_one_level_send_only_that_level_to_review(tmp_path):
    # the north elevation agrees on the first floor (4.5 m) but says 4.0 m for the second
    north = _tall("NORTH ELEVATION", roof=8.5, sheet="A-202")
    model, rep = _two_storey(tmp_path, _tall(), north)
    l1, l2 = model.levels
    assert l1.wall_height_m == pytest.approx(4.5)
    assert rep.levels[0]["height_source"] == "elevation_level_marks"
    assert l2.wall_height_m == real_set.DEFAULT_STOREY_HEIGHT_M
    assert l2.elevation_z_m == pytest.approx(4.5)
    assert rep.levels[1]["height_source"] == "storey_height_default"
    (rq,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert rq.target == {"kind": "level", "ids": ["L2"]} and rq.needs_review
    assert "default for L2 (elevation level marks disagree: A-201: 3.60 m; A-202: 4.00 m)" in (
        rq.description
    )


def test_a_level_with_no_mark_keeps_the_default_and_is_named(tmp_path):
    # FIRST FLOOR and ROOF only: the first floor's height, nothing for the second
    model, rep = _two_storey(tmp_path, _elev_marks())
    assert model.levels[0].wall_height_m == pytest.approx(FT12, abs=1e-3)
    assert model.levels[1].wall_height_m == real_set.DEFAULT_STOREY_HEIGHT_M
    assert model.levels[1].elevation_z_m == pytest.approx(FT12, abs=1e-3)
    (rq,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "default for L2 (no level mark)" in rq.description and not rq.needs_review
    assert rq.target == {"kind": "level", "ids": ["L2"]}


def test_config_height_still_overrides_every_level(tmp_path):
    model, rep = _two_storey(tmp_path, _tall(), storey_height_m=4.0)
    assert [lv.wall_height_m for lv in model.levels] == [4.0, 4.0]
    assert model.levels[1].elevation_z_m == 4.0
    assert [lv["height_source"] for lv in rep.levels] == ["config", "config"]


# --- matching level marks beyond numbered names (#822) -----------------------


def test_ground_then_first_is_british_numbering():
    mk = ES.LevelMark
    marks = [mk("GROUND FLOOR", 0.0, 0, ""), mk("FIRST FLOOR", 4.5, 0, ""),
             mk("ROOF", 8.1, 0, "")]  # fmt: skip
    assert ES.storey_steps(marks) == [(1, 4.5), (2, 3.6)]


@pytest.mark.parametrize(
    "name,key",
    [("MEZZANINE", "MEZZ"), ("MEZZANINE LEVEL", "MEZZ"), ("PENTHOUSE", "PH"),
     ("FIRST FLOOR", 1), ("ROOF", None)],
)  # fmt: skip
def test_storey_key(name, key):
    assert ES.storey_key(name) == key


def test_penthouse_roof_is_a_roof_not_a_storey():
    m = ES.LEVEL_NAME_RE.search("PENTHOUSE ROOF")
    assert m and m.group(0) == "ROOF"
    assert ES.LEVEL_NAME_RE.search("MEZZANINE").group(0) == "MEZZANINE"


def _british(sheet="A-201"):
    extra = (_mark("GROUND FLOOR", "EL. +0.000", 0.0) + _mark("FIRST FLOOR", "EL. +4.500", 4.5)
             + _mark("ROOF", "EL. +8.100", 8.1))  # fmt: skip
    return _elev("SOUTH ELEVATION", extra=extra).replace("A-201", sheet)


def test_british_numbered_set_gives_each_level_its_height(tmp_path):
    model, rep = _two_storey(tmp_path, _british())
    assert [lv.wall_height_m for lv in model.levels] == [pytest.approx(4.5), pytest.approx(3.6)]
    assert [lv["height_match"] for lv in rep.levels] == ["name", "name"]
    assert not [r for r in model.review_queue if r.id == "rq-storey-height"]


def _hundreds(storeys=2, sheet="A-201"):
    """Marks named by datum (LEVEL 100, LEVEL 115, ...): no plan level matches by name."""
    extra = _mark("LEVEL 100", "EL. +0.000", 0.0) + _mark("LEVEL 115", "EL. +4.500", 4.5)
    if storeys == 2:
        extra += _mark("ROOF", "EL. +8.100", 8.1)
    return _elev("SOUTH ELEVATION", extra=extra).replace("A-201", sheet)


def test_marks_that_name_no_plan_level_match_by_order(tmp_path):
    model, rep = _two_storey(tmp_path, _hundreds())
    assert [lv.wall_height_m for lv in model.levels] == [pytest.approx(4.5), pytest.approx(3.6)]
    assert model.levels[1].elevation_z_m == pytest.approx(4.5)
    assert [lv["height_match"] for lv in rep.levels] == ["order", "order"]
    assert any("matched to the plan levels by order" in n for n in rep.notes)
    assert not [r for r in model.review_queue if r.id == "rq-storey-height"]


def test_a_storey_count_that_does_not_match_keeps_the_default_and_says_so(tmp_path):
    model, rep = _two_storey(tmp_path, _hundreds(storeys=1))  # one storey, two plan levels
    assert [lv["height_source"] for lv in rep.levels] == ["storey_height_default"] * 2
    assert "height_match" not in rep.levels[0]
    (rq,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "storey counts (A-201: 1) do not match the 2 plan levels" in rq.description


def test_levels_with_no_number_match_by_order():
    per_sheet = [("A-201", [4.5, 3.6])]
    out, miss = real_set._level_heights(["L?1", "L?2"], {}, per_sheet, None)
    assert [out[lv][0] for lv in ("L?1", "L?2")] == [4.5, 3.6] and not miss
    assert {out[lv][3] for lv in out} == {"order"}
    # one level matched by name: no order fallback for the rest
    out, _ = real_set._level_heights(["L1", "MEZZ"], {1: [("A-201", 4.5)]}, per_sheet, None)
    assert out["L1"][0] == 4.5 and out["MEZZ"][1] == "storey_height_default"
