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
from test_real_set import SCALE_NOTE, _arch_win, _set, _tb  # noqa: E402

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


def _run(tmp_path, *elevs, plan_extra=""):
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1) + plan_extra, *elevs]
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
    assert not [r for r in model.review_queue if r.id.startswith("rq-elev-")]
    (sh,) = [r for r in model.review_queue if r.id == "rq-storey-height"]
    assert "elevations were read for windows" in sh.description


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
    (rq,) = [r for r in model.review_queue if r.id.startswith("rq-elev-A-201-")]
    assert "12.00 m wide on the elevation" in rq.description
