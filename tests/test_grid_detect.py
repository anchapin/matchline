"""Column grids and bubbles on vector sheets (#742). Hermetic: every PDF is
written by ``tests/pdf_fixtures.py`` and read back through ``pdf_ingest``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

import grid_detect as G  # noqa: E402
from pdf_ingest import ingest_pdf  # noqa: E402
from registration import Facade, register_elevation_grid  # noqa: E402

K = 0.5523


def circle(cx, cy, r, w=1.0) -> str:
    k = K * r
    return (
        f"{w} w {cx + r} {cy} m "
        f"{cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
        f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
        f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
        f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c h S\n"
    )


def dashed(x0, y0, x1, y1, w=0.5) -> str:
    return f"[12 3 2 3] 0 d {w} w {x0} {y0} m {x1} {y1} l S [] 0 d\n"


def dash_dot_pieces(x, y0, y1) -> str:
    out, y = "", y0
    while y < y1:
        out += line(x, y, x, min(y + 10, y1), 0.5) + line(x, y + 12, x, min(y + 13, y1), 0.5)
        y += 15
    return out


def bubble(cx, cy, lab, r=10) -> str:
    return circle(cx, cy, r) + text(cx - 3.5 * len(lab), cy - 4, lab, 11)


def _sheet(tmp_path, content, name, w=842, h=595):
    out = tmp_path / name
    ingest_pdf(write_pdf(tmp_path / f"{name}.pdf", [PageSpec(w, h, content)]), out_dir=out)
    return json.loads((out / "sheet_001.json").read_text())


PLAN_X = {"A": 100.0, "B": 250.0, "C": 400.0, "D": 640.0}
PLAN_Y = {"1": 120.0, "2": 300.0, "3": 450.0}


def plan_content(style="dashed") -> str:
    c = ""
    for lab, x in PLAN_X.items():
        if style == "pieces":
            c += dash_dot_pieces(x, 90, 500)
        elif style == "solid":
            c += line(x, 90, x, 500, 0.5)
        else:
            c += dashed(x, 90, x, 500)
        c += bubble(x, 515, lab)
    for lab, y in PLAN_Y.items():
        c += dashed(70, y, 680, y) + bubble(55, y, lab) + bubble(695, y, lab)
    # clutter: walls, a door swing arc, a room tag circle, a dimension string
    c += "2 w 90 110 m 660 110 l 660 470 l 90 470 l h S\n"
    c += "1 w 300 110 m 300 140 l S 300 140 m 316.6 140 330 126.6 330 110 c S\n"
    c += circle(330, 380, 12) + text(320, 376, "101", 9)
    c += line(100, 60, 640, 60, 0.3) + text(160, 64, "15000", 8)
    return c


def elev_content(labels=("A", "B", "C", "D"), scale=0.8, u0=80.0) -> str:
    c = "2 w 60 100 m 760 100 l S\n"  # ground line
    c += f"1.5 w {u0} 100 m {u0 + 540 * scale} 100 l {u0 + 540 * scale} 300 l {u0} 300 l h S\n"
    for lab in labels:
        u = u0 + (PLAN_X[lab] - 100) * scale
        c += dashed(u, 300, u, 350) + bubble(u, 362, lab)
    return c


def test_plan_grid_lines_and_labels(tmp_path):
    gs = G.detect_grids(_sheet(tmp_path, plan_content(), "plan"), "A-101")
    v, h = gs.by_label("v"), gs.by_label("h")
    assert sorted(v) == list("ABCD") and sorted(h) == ["1", "2", "3"]
    for lab, x in PLAN_X.items():
        assert v[lab].coord_pt == pytest.approx(x, abs=0.5)
        assert v[lab].dashed and v[lab].confidence == G.CONF_DASHED
    # bubbles at both ends of the horizontal grids agree: one line, two bubbles
    assert len(h["2"].bubbles) == 2 and h["2"].confidence > G.CONF_DASHED
    assert "101" not in v and "101" not in h  # room tag circle has no grid line
    kinds = {r["kind"] for r in gs.review}
    assert kinds <= {"grid_bubble_without_line"}


@pytest.mark.parametrize("style,conf", [("pieces", G.CONF_DASHED), ("solid", G.CONF_SOLID)])
def test_dash_dot_pieces_and_solid_grid_lines(tmp_path, style, conf):
    gs = G.detect_grids(_sheet(tmp_path, plan_content(style), "plan"), "A-101")
    v = gs.by_label("v")
    assert sorted(v) == list("ABCD")
    assert {g.confidence for g in v.values()} == {conf}


def test_label_drawn_twice_goes_to_review(tmp_path):
    c = plan_content() + dashed(560, 90, 560, 500) + bubble(560, 515, "B")
    gs = G.detect_grids(_sheet(tmp_path, c, "plan"), "A-101")
    assert "B" not in gs.by_label()
    assert any(r["kind"] == "grid_label_conflict" and r["label"] == "B" for r in gs.review)


@pytest.mark.parametrize("labels", [("A", "B", "C", "D"), ("B", "D")])
def test_plan_and_elevation_grids_register(tmp_path, labels):
    plan = G.detect_grids(_sheet(tmp_path, plan_content(), "plan"), "A-101")
    elev = G.detect_grids(_sheet(tmp_path, elev_content(labels), "elev"), "A-201")
    m_per_pt = 0.05
    pg = G.plan_grid_m(plan, "v", m_per_pt, origin_pt=PLAN_X["A"])
    eb = G.elevation_bubbles(elev)
    assert [b["label"] for b in eb] == list(labels)
    fac = Facade(name="south", ref_corner_m=(0.0, 0.0), length_m=27.0, fixed_coord_m=0.0)
    reg = register_elevation_grid("A-201", fac, pg, eb, 100.0, 40.0, 1)
    assert reg.method == "grid" and reg.grid_labels_used == list(labels)
    assert reg.confidence == pytest.approx(0.95)
    # every bubble maps back onto its plan grid position
    for e in eb:
        assert reg.to_facade(e["u_px"], 100.0)[0] == pytest.approx(pg[e["label"]], abs=0.02)
    # a point midway between A and D on the elevation lands midway on the plan
    mid_u = 80.0 + (PLAN_X["D"] - PLAN_X["A"]) * 0.8 / 2
    assert reg.to_facade(mid_u, 100.0)[0] == pytest.approx(
        (PLAN_X["D"] - PLAN_X["A"]) * m_per_pt / 2, abs=0.05
    )


def test_one_shared_label_does_not_register(tmp_path):
    plan = G.detect_grids(_sheet(tmp_path, plan_content(), "plan"), "A-101")
    elev = G.detect_grids(_sheet(tmp_path, elev_content(("C",)), "elev"), "A-201")
    fac = Facade(name="south", ref_corner_m=(0.0, 0.0), length_m=27.0, fixed_coord_m=0.0)
    with pytest.raises(ValueError):
        register_elevation_grid(
            "A-201", fac, G.plan_grid_m(plan, "v", 0.05), G.elevation_bubbles(elev), 100.0, 40.0, 1
        )
