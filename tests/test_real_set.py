"""Whole pipeline on a real-style PDF set (#741): two floors, arch + mech.

The set is drawn the way a CAD export is (title blocks, scale notes, wall-mass
outlines with door gaps, room labels) and goes through ``run_pipeline`` with
``--set`` exactly as ``matchline run --set`` does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402
from test_plan_walls import DOOR, PARTITION, SHELL, T_EXT, _mass, _outline, _pt  # noqa: E402

import real_set  # noqa: E402
import run_pipeline  # noqa: E402

W, H = 1728, 1152
SCALE_NOTE = 'SCALE: 1/8" = 1\'-0"'


def _tb(number: str, title: str) -> str:
    out = line(1480, 0, 1480, H) + text(1500, 220, "MATCHLINE TEST CLINIC", 14)
    out += text(1500, 150, title, 12)
    return out + text(1560, 40, number, 24)


def _labels(level: int) -> str:
    a, b = _pt(1.5, 3), _pt(6.5, 3)
    return (
        text(*a, "OFFICE", 8)
        + text(a[0], a[1] - 10, f"{level}01", 8)
        + text(*b, "OPEN OFFICE", 8)
        + text(b[0], b[1] - 10, f"{level}02", 8)
    )


def _arch(number, title, level, walls=True):
    body = _outline(_mass(SHELL + PARTITION, [DOOR])) + _labels(level) if walls else ""
    return _tb(number, title) + text(100, 48, SCALE_NOTE, 8) + body


def _mech(number, title):
    # duct runs only: single lines, no wall pairs, no rooms
    x0, y = _pt(1, 4.5)
    x1, _ = _pt(9, 4.5)
    return _tb(number, title) + text(100, 48, SCALE_NOTE, 8) + line(x0, y, x1, y, 2)


def _set(tmp_path, pages):
    return write_pdf(tmp_path / "set.pdf", [PageSpec(width=W, height=H, content=c) for c in pages])


def _args(pdf, out, **kw):
    return SimpleNamespace(
        seed=None, image=None, aec_bench=None, set_pdf=pdf, out_dir=out, simplify_tol=0.02,
        storey_height=kw.get("storey_height"), elevation_key="elev_grid", open_office_span=False,
    )  # fmt: skip


TWO_FLOORS = [
    ("A-101", "FIRST FLOOR PLAN", 1, "A"),
    ("A-102", "SECOND FLOOR PLAN", 2, "A"),
    ("M-101", "FIRST FLOOR MECHANICAL PLAN", 1, "M"),
    ("M-102", "SECOND FLOOR MECHANICAL PLAN", 2, "M"),
]


def _two_floor_set(tmp_path):
    pages = [_arch(n, t, lv) if d == "A" else _mech(n, t) for n, t, lv, d in TWO_FLOORS]
    return _set(tmp_path, pages)


def test_set_model_two_floors(tmp_path):
    model, rep = real_set.build_set_model(_two_floor_set(tmp_path), tmp_path / "out")
    assert [lv.id for lv in model.levels] == ["L1", "L2"]
    assert [lv.elevation_z_m for lv in model.levels] == [0.0, 3.0]
    assert sorted(model.spaces) == ["L1-101", "L1-102", "L2-201", "L2-202"]
    sp = model.spaces["L1-101"]
    assert sp.name == "OFFICE" and sp.area_m2 == pytest.approx(24.0, abs=0.05)
    assert sp.volume_m3 == pytest.approx(72.0, abs=0.2)
    assert all(y <= 0 for _x, y in sp.polygon_m)  # canonical frame is y-down
    # the only plan gap is the interior door: counted, never put on the envelope
    assert all(not s.openings for s in model.spaces.values())
    assert [lv["plan_openings"] for lv in rep.levels] == [1, 1]
    assert rep.levels[0]["openings"] == {"exterior": 0, "interior": 1, "modelled": 0, "unsized": 0}
    env = [w for w in model.envelope if w.id.startswith("L2-")]
    assert sum(w.area_m2 for w in env) == pytest.approx(32 * 3.0, abs=0.1)
    assert {w.facade for w in env} == {"north", "south", "east", "west"}
    assert any(r.id == "rq-storey-height" and not r.needs_review for r in model.review_queue)
    d = rep.to_dict()
    by = {s["file"]: s for s in d["sheets"]}
    assert by["sheet_001.json"]["stages"]["walls_rooms"]["status"] == "ok"
    assert by["sheet_003.json"]["stages"]["walls_rooms"]["status"] == "skipped"
    # mechanical plans without detections fail the symbol stage loudly
    assert by["sheet_003.json"]["stages"]["symbols"]["status"] == "failed"
    assert d["failed_sheets"] == ["sheet_003.json", "sheet_004.json"]


def test_storey_height_from_config(tmp_path):
    model, rep = real_set.build_set_model(
        _two_floor_set(tmp_path), tmp_path / "out", storey_height_m=4.2
    )
    assert [lv.elevation_z_m for lv in model.levels] == [0.0, 4.2]
    assert not any(r.id == "rq-storey-height" for r in model.review_queue)
    assert rep.levels[1]["height_source"] == "config"


def test_failed_plan_sheet_is_reported_and_the_run_continues(tmp_path):
    pages = [
        _arch("A-101", "FIRST FLOOR PLAN", 1),
        _tb("A-102", "SECOND FLOOR PLAN") + _outline(_mass(SHELL)),  # no scale note
    ]
    model, rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    assert [lv.id for lv in model.levels] == ["L1"]
    st = {s.file: s.stages for s in rep.sheets}
    assert st["sheet_002.json"]["scale"]["status"] == "failed"
    assert st["sheet_002.json"]["walls_rooms"]["status"] == "failed"


def test_no_rooms_anywhere_stops_with_reasons(tmp_path):
    pages = [_arch("A-101", "FIRST FLOOR PLAN", 1, walls=False)]
    with pytest.raises(real_set.SetError):
        real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    d = json.loads((tmp_path / "out" / "stage_00_set_report.json").read_text())
    assert d["sheets"][0]["stages"]["walls_rooms"]["status"] == "failed"


def test_run_pipeline_set_exports_gbxml(tmp_path):
    out = tmp_path / "out"
    run_pipeline.main(_args(_two_floor_set(tmp_path), out))
    for n in ("00_set_report", "01_building", "02_model", "03_simplified", "04_validation"):
        assert (out / f"stage_{n}.json").exists(), n
    xml = (out / "stage_06_bem" / "set.xml").read_text()
    assert xml.count("<Space ") == 4
    assert xml.count("<BuildingStorey ") == 2
    assert (out / "stage_06_bem" / "set.ifc").exists()
    tr = json.loads((out / "package" / "trust_report.json").read_text())
    assert tr["inputs"][0]["role"] == "drawing set" and len(tr["inputs"][0]["sha256"]) == 64


def test_openstudio_reads_the_set_export(tmp_path):
    openstudio = pytest.importorskip("openstudio")
    out = tmp_path / "out"
    run_pipeline.main(_args(_two_floor_set(tmp_path), out))
    m = openstudio.gbxml.GbXMLReverseTranslator().loadModel(
        openstudio.path(str(out / "stage_06_bem" / "set.xml"))
    )
    assert m.is_initialized()
    m = m.get()
    assert len(m.getBuildingStorys()) == 2 and len(m.getSpaces()) == 4


def test_set_schedules_reach_the_model(tmp_path):
    """A schedule sheet in the set (#746): rows land in model.schedules, mechanical
    rows in the report's equipment list, and the sheet's stage says what it read."""
    from test_pdf_schedules import door_schedule, vav_schedule

    pages = [_arch(n, t, lv) if d == "A" else _mech(n, t) for n, t, lv, d in TWO_FLOORS]
    pages.append(_tb("A-601", "DOOR SCHEDULE") + door_schedule(100, 1000))
    pages.append(_tb("M-601", "MECHANICAL SCHEDULES") + vav_schedule(100, 1000))
    model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    assert sorted(model.schedules) == ["D1", "D2", "D3"]
    assert model.schedules["D2"]["width_m"] == pytest.approx(72 * 0.0254)
    rep = report.to_dict()
    assert [e["tag"] for e in rep["schedules"]["equipment"]] == ["VAV-1", "VAV-2", "VAV-3"]
    assert rep["schedules"]["equipment"][0]["sheet"] == "M-601"
    by = {s["file"]: s for s in rep["sheets"]}
    st = by["sheet_005.json"]["stages"]["schedules"]
    assert (st["status"], st["n"], st["kinds"]) == ("ok", 1, ["door"])
    assert by["sheet_001.json"]["stages"]["schedules"]["status"] == "skipped"
    assert (tmp_path / "run" / "sheets" / "schedules_005.json").exists()


def test_set_unreadable_schedule_goes_to_review(tmp_path):
    from test_pdf_schedules import door_schedule

    dup = [("D1", "3'-0\"", "7'-0\"", "HM"), ("D1", "3'-0\"", "8'-0\"", "HM")]
    pages = [_arch(n, t, lv) if d == "A" else _mech(n, t) for n, t, lv, d in TWO_FLOORS]
    pages.append(_tb("A-601", "DOOR SCHEDULE") + door_schedule(100, 1000, rows=dup))
    model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    assert model.schedules == {}
    rq = [r for r in model.review_queue if r.kind == "fixture_schedule"]
    assert len(rq) == 1 and "D1 appears more than once" in rq[0].description
    rep = report.to_dict()
    assert "sheet_005.json" in rep["failed_sheets"]


# ---- exterior plan openings sized by the schedule (#741) -----------------

WIN = ((7, 0), (1, 0), 1.2, T_EXT)  # 1.2 m gap in the south shell, east room


def _arch_win(number, title, level):
    body = _outline(_mass(SHELL + PARTITION, [DOOR, WIN])) + _labels(level)
    return _tb(number, title) + text(100, 48, SCALE_NOTE, 8) + body


def _window_schedule(rows):
    from test_pdf_schedules import door_schedule

    return door_schedule(100, 1000, rows=rows).replace("DOOR SCHEDULE", "WINDOW SCHEDULE")


def _one_floor(tmp_path, schedule_rows=None):
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1)]
    if schedule_rows:
        pages.append(_tb("A-601", "WINDOW SCHEDULE") + _window_schedule(schedule_rows))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")


def test_exterior_gap_matched_by_width_becomes_a_window(tmp_path):
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W2", "3'-0\"", "4'-0\"", "FIXED")]
    model, rep = _one_floor(tmp_path, rows)
    ops = [o for s in model.spaces.values() for o in s.openings]
    assert len(ops) == 1
    op = ops[0]
    assert (op.category, op.tag, op.host_facade) == ("window", "W1", "south")
    assert op.width_m == pytest.approx(48 * 0.0254)  # the schedule's size; the plan gap places it
    assert op.height_m == pytest.approx(60 * 0.0254, abs=1e-3)  # schedule height
    assert op.provenance.method == "plan_gap_schedule_width"
    assert model.spaces["L1-102"].openings == [op]  # the room whose wall holds the gap
    assert rep.levels[0]["openings"] == {"exterior": 1, "interior": 1, "modelled": 1, "unsized": 0}


def test_exterior_gap_without_a_schedule_row_is_not_invented(tmp_path):
    model, rep = _one_floor(tmp_path)
    assert all(not s.openings for s in model.spaces.values())
    rq = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert len(rq) == 1 and rq[0].needs_review and "south" in rq[0].description
    assert rep.levels[0]["openings"]["unsized"] == 1


def test_exterior_gap_with_disagreeing_schedule_rows_goes_to_review(tmp_path):
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W3", "4'-0\"", "7'-0\"", "FIXED")]
    model, _rep = _one_floor(tmp_path, rows)
    assert all(not s.openings for s in model.spaces.values())
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "W1" in rq.description and "W3" in rq.description
    assert rq.target["kind"] == "wall" and rq.target["id"]  # #796: no opening to edit yet


def test_set_window_reaches_the_gbxml(tmp_path):
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1)]
    pages.append(_tb("A-601", "WINDOW SCHEDULE") + _window_schedule(rows))
    out = tmp_path / "run"
    run_pipeline.main(_args(_set(tmp_path, pages), out))
    xml = (out / "stage_06_bem" / "set.xml").read_text()
    assert xml.count('openingType="FixedWindow"') == 1


def test_single_sheet_image_fails_loudly_and_points_to_the_set(tmp_path, capsys):
    """--image on one raster sheet has no walls or rooms: a StageError naming the
    --set route, after stage 1 has written its takeoff (#741)."""
    from PIL import Image

    img = tmp_path / "sheet_01.png"
    Image.new("L", (200, 100), 255).save(img)
    dets = tmp_path / "detections.json"
    dets.write_text(
        json.dumps(
            {
                "image": "sheet_01.png",
                "width": 200,
                "height": 100,
                "preds": [{"cls": 0, "conf": 0.9, "x0": 10.0, "y0": 10.0, "x1": 30.0, "y1": 40.0}],
            }
        )
    )
    out = tmp_path / "out"
    args = _args(None, out)
    args.image, args.detections, args.schedule_csv = img, dets, None
    with pytest.raises(SystemExit) as ei:
        run_pipeline.main(args)
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "[Stage 2: build_model]" in err and "no walls or rooms" in err
    assert "hint: Run the PDF drawing set instead: matchline run --set" in err
    assert json.loads((out / "stage_01_building.json").read_text())["n_detections"] == 1


def _one_floor_thermal(tmp_path, rows, u_head="U-FACTOR (BTU/HR-SF-F)"):
    from test_pdf_schedules import window_thermal_schedule

    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1)]
    pages.append(_tb("A-601", "WINDOW SCHEDULE") + window_thermal_schedule(u_head, rows, 100, 1000))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")


def test_scheduled_window_u_shgc_vt_become_its_construction(tmp_path):
    model, _rep = _one_floor_thermal(tmp_path, [("W1", "4'-0\"", "5'-0\"", "0.36", "0.38", "0.42")])
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    c = model.constructions[op.construction_id]
    assert c.u_value_w_m2k == pytest.approx(0.36 * 5.678263, abs=1e-4)
    assert (c.shgc, c.vt) == (0.38, 0.42)
    assert c.provenance.method == "pdf_schedule_thermal" and c.provenance.confidence == 0.9
    assert c.provenance.sheet_id == "A-601"  # the schedule sheet, not the plan
    assert "first placed on plan A-101" in c.provenance.note
    # Table 5.5 never replaces it
    from construction_library import apply_construction_library

    apply_construction_library(model, climate_zone="5A")
    assert op.construction_id == c.id


def test_same_width_windows_with_different_u_go_to_review(tmp_path):
    rows = [
        ("W1", "4'-0\"", "5'-0\"", "0.36", "0.38", "0.42"),
        ("W2", "4'-0\"", "5'-0\"", "0.30", "0.25", "0.40"),
    ]
    model, _rep = _one_floor_thermal(tmp_path, rows)
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    assert op.construction_id == ""  # size agrees, thermal values do not
    (rq,) = [r for r in model.review_queue if r.kind == "opening_thermal_ambiguous"]
    assert "W1" in rq.description and "W2" in rq.description
    assert rq.target == {"kind": "opening", "id": op.id, "field": "construction_id"}  # #796


def test_scheduled_window_construction_reaches_the_gbxml(tmp_path):
    from test_pdf_schedules import window_thermal_schedule

    pages = [_arch_win("A-101", "FIRST FLOOR PLAN", 1)]
    pages.append(
        _tb("A-601", "WINDOW SCHEDULE")
        + window_thermal_schedule(
            "U-FACTOR (BTU/HR-SF-F)",
            [("W1", "4'-0\"", "5'-0\"", "0.36", "0.38", "0.42")],
            100,
            1000,
        )
    )
    out = tmp_path / "run"
    run_pipeline.main(_args(_set(tmp_path, pages), out))
    xml = (out / "stage_06_bem" / "set.xml").read_text()
    assert "<WindowType" in xml and "SCHED-WINDOW-U2.0442" in xml


# ---- a door swing on the plan settles a door/window width tie (#743) -----


def test_plan_door_swing_picks_the_door_over_a_same_width_window(tmp_path):
    from test_pdf_schedules import door_schedule
    from test_plan_walls import _door_symbol

    swing = _door_symbol((6.4, 0), (6.4, 1.2), (7.6, 0))  # hinged on the west jamb, swings in
    plan = _arch_win("A-101", "FIRST FLOOR PLAN", 1) + swing
    win = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    door = [("D9", "4'-0\"", "7'-0\"", "HM")]
    pages = [
        plan,
        _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(win),
        _tb("A-602", "DOOR SCHEDULE") + door_schedule(100, 1000, rows=door),
    ]
    model, rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    ops = [o for s in model.spaces.values() for o in s.openings]
    assert [(o.category, o.tag) for o in ops] == [("door", "D9")]
    assert "door swing drawn on the plan" in ops[0].provenance.note
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


def test_same_width_tie_without_a_swing_still_goes_to_review(tmp_path):
    from test_pdf_schedules import door_schedule

    win = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    door = [("D9", "4'-0\"", "7'-0\"", "HM")]
    pages = [
        _arch_win("A-101", "FIRST FLOOR PLAN", 1),
        _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(win),
        _tb("A-602", "DOOR SCHEDULE") + door_schedule(100, 1000, rows=door),
    ]
    model, _rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    assert all(not s.openings for s in model.spaces.values())
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "D9" in rq.description and "W1" in rq.description


def test_plan_glazing_picks_the_window_over_a_same_width_door(tmp_path):
    from test_pdf_schedules import door_schedule
    from test_plan_walls import _glazing

    body = _outline(_mass(SHELL + PARTITION, [DOOR])) + _labels(1) + _glazing(6.4, 7.6)
    plan = _tb("A-101", "FIRST FLOOR PLAN") + text(100, 48, SCALE_NOTE, 8) + body
    win = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    door = [("D9", "4'-0\"", "7'-0\"", "HM")]
    pages = [
        plan,
        _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(win),
        _tb("A-602", "DOOR SCHEDULE") + door_schedule(100, 1000, rows=door),
    ]
    model, _rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    ops = [o for s in model.spaces.values() for o in s.openings]
    assert [(o.category, o.tag, o.host_facade) for o in ops] == [("window", "W1", "south")]
    assert "glazing line drawn on the plan" in ops[0].provenance.note
