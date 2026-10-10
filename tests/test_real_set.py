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


def test_default_storey_height_is_listed_in_the_trust_report(tmp_path):
    import export_package as E

    model, _rep = _one_floor(tmp_path)
    tr = E.build_trust_report(model, SimpleNamespace(results=[], errors=[], ok=True), {})
    (row,) = [d for d in tr["defaults"] if d["method"] == "storey_height_default"]
    assert "3 m" in row["source"] and "no section or elevation" in row["source"]
    assert row["where"].startswith("model.review_queue")


# ---- a schedule tag written next to a plan gap names its row (#793) ------

ROWS_4FT = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W3", "4'-0\"", "7'-0\"", "FIXED")]


def _one_floor_tagged(tmp_path, tag, rows=ROWS_4FT, at=(7, -0.6)):
    page = _arch_win("A-101", "FIRST FLOOR PLAN", 1) + text(*_pt(*at), tag, 8)
    sched = _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(rows)
    return real_set.build_set_model(_set(tmp_path, [page, sched]), tmp_path / "out")


def test_plan_tag_picks_the_row_when_widths_disagree(tmp_path):
    # without the tag, W1 and W3 are both 4'-0" and disagree on height: unsized
    model, rep = _one_floor_tagged(tmp_path, "W3")
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    assert op.tag == "W3" and op.height_m == pytest.approx(84 * 0.0254, abs=1e-3)
    assert op.provenance.method == "plan_gap_tag" and op.provenance.confidence == 0.85
    assert "tagged W3" in op.provenance.note
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


def test_plan_tag_with_the_wrong_width_goes_to_review(tmp_path):
    rows = ROWS_4FT + [("W5", "6'-0\"", "5'-0\"", "FIXED")]
    model, _rep = _one_floor_tagged(tmp_path, "W5", rows)
    assert all(not s.openings for s in model.spaces.values())
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "tag W5 is scheduled 1.83 m wide" in rq.description
    assert rq.target["gap"]["candidates"] == ["W5"]


def test_unscheduled_or_far_tag_falls_back_to_width(tmp_path):
    # X9 is not in the schedule; W3 sits 3 m from the gap
    for k, (tag, at) in enumerate([("X9", (7, -0.6)), ("W3", (7, 3.0))]):
        d = tmp_path / str(k)
        d.mkdir()
        rows = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W3", "3'-0\"", "7'-0\"", "FIXED")]
        model, _rep = _one_floor_tagged(d, tag, rows, at)
        (op,) = [o for s in model.spaces.values() for o in s.openings]
        assert op.tag == "W1" and op.provenance.method == "plan_gap_schedule_width"


def test_room_numbers_and_names_are_not_tags():
    import plan_walls as PW

    for s in ("W1", "D-12", "SF-1", "SF1A"):
        assert PW.TAG_RE.match(s), s
    for s in ("101", "OFFICE", "N", "WINDOW-1234", "1/A-501"):
        assert not PW.TAG_RE.match(s), s


# ---- #793: a scheduled storefront tagged on a plain wall --------------------


def _tagged_wall(tmp_path, rows, tag="SF-1", at=(5, -0.6)):
    page = _arch("A-101", "FIRST FLOOR PLAN", 1) + text(*_pt(*at), tag, 8)
    sched = _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(rows)
    return real_set.build_set_model(_set(tmp_path, [page, sched]), tmp_path / "out")


def test_storefront_tag_spanning_the_wall_is_a_window(tmp_path):
    # the south wall is 10 m at centrelines; SF-1 is scheduled 32'-9" (9.98 m)
    model, _rep = _tagged_wall(tmp_path, [("SF-1", "32'-9\"", "8'-0\"", "FIXED")])
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    assert (op.tag, op.category, op.host_facade) == ("SF-1", "window", "south")
    assert op.width_m == pytest.approx(393 * 0.0254, abs=1e-3)
    assert op.height_m == pytest.approx(96 * 0.0254, abs=1e-3)
    assert op.provenance.method == "plan_wall_tag"
    assert op.provenance.confidence == real_set.WALL_TAG_CONFIDENCE
    assert "storefront" in op.provenance.note
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


def test_narrower_tagged_window_on_a_plain_wall_goes_to_review(tmp_path):
    model, _rep = _tagged_wall(tmp_path, [("SF-1", "12'-0\"", "8'-0\"", "FIXED")])
    assert all(not s.openings for s in model.spaces.values())
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "no opening is drawn; may be glazing" in rq.description
    gap = rq.target["gap"]
    assert gap["candidates"] == ["SF-1"] and gap["facade"] == "south"
    assert gap["width_m"] == pytest.approx(144 * 0.0254, abs=1e-3)
    assert rq.target["kind"] == "wall" and rq.target["field"] == "opening"


def test_unscheduled_tag_on_a_plain_wall_is_left_alone(tmp_path):
    model, _rep = _tagged_wall(tmp_path, [("W1", "4'-0\"", "5'-0\"", "FIXED")], tag="SF-1")
    assert all(not s.openings for s in model.spaces.values())
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


# ---- #747: wall-type tags resolved through the legend ------------------------

CMU = '8" CMU W/ 2" RIGID INSULATION'


def _legend_set(tmp_path, tags, legend, sched_rows=None):
    page = _arch("A-101", "FIRST FLOOR PLAN", 1)
    for tag, at in tags:
        page += text(*_pt(*at), tag, 8)
    leg = _tb("A-501", "WALL TYPES")
    for k, (tag, desc) in enumerate(legend):
        leg += text(100, 800 - 30 * k, tag, 10) + text(160, 800 - 30 * k, desc, 10)
    pages = [page, leg]
    if sched_rows:
        pages.append(_tb("A-601", "WINDOW SCHEDULE") + _window_schedule(sched_rows))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")


def test_wall_type_tag_gives_the_envelope_wall_its_legend_construction(tmp_path):
    from construction_library import apply_construction_library

    model, rep = _legend_set(tmp_path, [("W1", (5, -0.6))], [("W1", CMU)])
    (w,) = [w for w in model.envelope if w.construction_id]
    assert w.facade == "south" and w.construction_id == "LEG-W1"
    c = model.constructions["LEG-W1"]
    assert c.name == f"W1: {CMU}" and c.u_value_w_m2k is None
    assert c.provenance.method == "wall_type_legend" and c.provenance.sheet_id == "A-501"
    assert "Mass" in c.provenance.note
    assert rep.levels[0]["wall_types"] == 1
    assert any("wall-type legend: 1 tag(s) W1 (Mass, A-501)" in n for n in rep.notes)
    s = apply_construction_library(model, "4A")
    assert s.resolved["LEG-W1"]["construction_type"] == "Mass"
    assert c.u_value_w_m2k == pytest.approx(s.resolved["LEG-W1"]["u_si"])


def test_no_legend_leaves_walls_unassigned(tmp_path):
    model, rep = _legend_set(tmp_path, [("W1", (5, -0.6))], [])
    assert not any(w.construction_id for w in model.envelope)
    assert "wall_types" not in rep.levels[0]


def test_scheduled_window_tag_is_not_a_wall_type(tmp_path):
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    model, _rep = _legend_set(tmp_path, [("W1", (5, -0.6))], [("W1", CMU)], rows)
    assert not any(w.construction_id for w in model.envelope)
    assert "LEG-W1" not in model.constructions


def test_two_wall_types_on_one_wall_go_to_review(tmp_path):
    legend = [("W1", CMU), ("W2", '6" METAL STUD FRAMING')]
    model, _rep = _legend_set(tmp_path, [("W1", (5, -0.6)), ("W2", (8, -0.6))], legend)
    (rq,) = [r for r in model.review_queue if r.kind == "wall_type_ambiguous"]
    assert "W1, W2" in rq.description and rq.target["kind"] == "wall"
    assert not any(w.construction_id == "LEG-W1" for w in model.envelope if w.id == rq.target["id"])


def test_legend_disagreeing_with_itself_goes_to_review(tmp_path):
    legend = [("W1", CMU), ("W1", "WOOD STUD FRAMING")]
    model, _rep = _legend_set(tmp_path, [("W1", (5, -0.6))], legend)
    (rq,) = [r for r in model.review_queue if r.kind == "wall_type_conflict"]
    assert "W1" in rq.description
    assert not any(w.construction_id for w in model.envelope)


# ---- #829: a nearer wall-type mark does not take a window's schedule tag ----


def _window_and_wall_type(tmp_path, tags):
    page = _arch_win("A-101", "FIRST FLOOR PLAN", 1)
    for tag, at in tags:
        page += text(*_pt(*at), tag, 8)
    leg = _tb("A-501", "WALL TYPES") + text(100, 800, "EW1", 10) + text(160, 800, CMU, 10)
    sched = _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(ROWS_4FT)
    return real_set.build_set_model(_set(tmp_path, [page, leg, sched]), tmp_path / "out")


def test_scheduled_mark_wins_over_a_nearer_wall_type_tag(tmp_path):
    # EW1 (a wall type) sits 0.4 m off the gap, W3 (the window) 1.0 m off
    model, _rep = _window_and_wall_type(tmp_path, [("EW1", (7.3, -0.4)), ("W3", (6.7, -1.0))])
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    assert op.tag == "W3" and op.provenance.method == "plan_gap_tag"
    assert op.height_m == pytest.approx(84 * 0.0254, abs=1e-3)
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]
    (w,) = [w for w in model.envelope if w.construction_id]
    assert w.construction_id == "LEG-EW1" and w.facade == "south"


def test_wall_type_tag_alone_by_a_window_still_types_the_wall(tmp_path):
    model, _rep = _window_and_wall_type(tmp_path, [("EW1", (7.3, -0.4))])
    # no scheduled mark: W1 and W3 are both 4'-0" and disagree, so unsized
    assert all(not s.openings for s in model.spaces.values())
    assert [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert [w.construction_id for w in model.envelope if w.construction_id] == ["LEG-EW1"]


# ---- #746: scheduled mechanical tags placed in rooms from the mech plans ----


def _mech_tagged(number, title, tags, scale=SCALE_NOTE):
    body = _tb(number, title) + text(100, 48, scale, 8)
    for tag, at in tags:
        body += text(*_pt(*at), tag, 8)
    return body


def _mech_set(tmp_path, m101_tags, scale=SCALE_NOTE):
    from test_pdf_schedules import vav_schedule

    pages = [
        _arch("A-101", "FIRST FLOOR PLAN", 1),
        _mech_tagged("M-101", "FIRST FLOOR MECHANICAL PLAN", m101_tags, scale),
        _tb("M-601", "MECHANICAL SCHEDULES") + vav_schedule(100, 1000),
    ]
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")


def test_mech_tags_land_in_the_rooms_the_plan_shows(tmp_path):
    model, report = _mech_set(
        tmp_path, [("VAV-1", (1.5, 2)), ("VAV-2 800 CFM", (6.5, 2)), ("VAV-3", (14, 2))]
    )
    eq = {e["tag"]: e for e in report.to_dict()["schedules"]["equipment"]}
    assert eq["VAV-1"]["space_id"] == "L1-101" and eq["VAV-1"]["level_id"] == "L1"
    assert eq["VAV-2"]["space_id"] == "L1-102"
    loc = eq["VAV-1"]["located"][0]
    assert loc["sheet"] == "M-101" and loc["registration"].startswith("frame:")
    assert loc["confidence"] == pytest.approx(0.6)
    # VAV-3 is tagged outside the building: found, but in no room
    assert "space_id" not in eq["VAV-3"]
    rq = [r for r in model.review_queue if r.id == "rq-equip-VAV-3"]
    assert rq and rq[0].kind == "equipment_outside_rooms"
    assert any("2 of 3 scheduled tag(s) placed" in n for n in report.notes)


def test_mech_tag_in_two_rooms_is_ambiguous(tmp_path):
    model, report = _mech_set(tmp_path, [("VAV-1", (1.5, 2)), ("VAV-1", (6.5, 2))])
    eq = {e["tag"]: e for e in report.to_dict()["schedules"]["equipment"]}
    assert "space_id" not in eq["VAV-1"] and len(eq["VAV-1"]["located"]) == 2
    assert [r.kind for r in model.review_queue if r.id == "rq-equip-VAV-1"] == [
        "equipment_ambiguous"
    ]
    assert any("not on any mechanical plan: VAV-2, VAV-3" in n for n in report.notes)


def test_mech_plan_at_another_scale_without_a_grid_is_not_placed(tmp_path):
    model, report = _mech_set(tmp_path, [("VAV-1", (1.5, 2))], scale='SCALE: 1/4" = 1\'-0"')
    eq = {e["tag"]: e for e in report.to_dict()["schedules"]["equipment"]}
    assert "located" not in eq["VAV-1"]
    assert [r.kind for r in model.review_queue if r.id == "rq-mechreg-M-101"] == [
        "mech_plan_unregistered"
    ]


def test_placed_terminal_unit_defines_a_zone_on_its_room(tmp_path):
    model, report = _mech_set(tmp_path, [("VAV-1", (1.5, 2)), ("VAV-3", (14, 2))])
    assert list(model.zones) == ["L1-Z-VAV-1"]
    z = model.zones["L1-Z-VAV-1"]
    assert z.space_ids == ["L1-101"] and z.level_id == "L1"
    assert (z.terminal_unit.tag, z.terminal_unit.type) == ("VAV-1", "vav")
    assert z.provenance.method == "mech_plan_tag" and "duct tracing" in z.provenance.note
    sp = model.spaces["L1-101"]
    assert sp.hvac.zone_ids == ["L1-Z-VAV-1"] and sp.hvac.terminal_units[0].tag == "VAV-1"
    eq = {e["tag"]: e for e in report.to_dict()["schedules"]["equipment"]}
    assert eq["VAV-1"]["zone_id"] == "L1-Z-VAV-1" and "zone_id" not in eq["VAV-3"]
    assert any("1 HVAC zone(s), one per placed terminal unit" in n for n in report.notes)


def test_zone_carries_its_terminal_units_scheduled_airflow(tmp_path):
    model, _ = _mech_set(tmp_path, [("VAV-1", (1.5, 2))])
    z = model.zones["L1-Z-VAV-1"]
    # VAV-1 is scheduled MAX CFM 1,200 / MIN CFM 360
    assert z.design_airflow_max_m3s == pytest.approx(1200 * 0.3048**3 / 60, abs=1e-6)
    assert z.design_airflow_min_m3s == pytest.approx(360 * 0.3048**3 / 60, abs=1e-6)
    assert "design airflow max 1200 CFM, min 360 CFM" in z.provenance.note
    back = type(model).from_json(model.to_json()).zones["L1-Z-VAV-1"]
    assert back.design_airflow_max_m3s == z.design_airflow_max_m3s


@pytest.mark.parametrize(
    "row, expect, note",
    [
        ({"cfm": 400.0, "airflow_unit": "cfm"}, (0.188779, None), "max 400 CFM"),
        ({"cfm_max": 300.0, "cfm_min": 90.0, "airflow_unit": "l/s"}, (0.3, 0.09), "300 L/S"),
        ({"cfm_max": 300.0, "airflow_unit": ""}, (None, None), "states no unit"),
        ({"cfm_max": 100.0, "cfm_min": 300.0, "airflow_unit": "cfm"}, (None, None), "exceeds"),
        ({"neck_size": '8"'}, (None, None), "no airflow"),
        ({"cfm_max": 0.0, "cfm_min": 0.0, "airflow_unit": "cfm"}, (None, None), "not positive"),
    ],
)
def test_zone_airflow_from_a_schedule_row(row, expect, note):
    mx, mn, why = real_set._zone_airflow(row)
    for got, want in ((mx, expect[0]), (mn, expect[1])):
        assert got is None if want is None else got == pytest.approx(want, abs=1e-6)
    assert note in why


def test_mechanical_legend_rows_on_the_report_and_unmapped_to_review(tmp_path):
    from test_hvac_legend import legend
    from test_pdf_schedules import vav_schedule

    pages = [
        _arch("A-101", "FIRST FLOOR PLAN", 1),
        _mech_tagged("M-101", "FIRST FLOOR MECHANICAL PLAN", [("VAV-1", (1.5, 2))]),
        _tb("M-601", "MECHANICAL SCHEDULES") + vav_schedule(100, 1000) + legend(900, 1000),
    ]
    model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    (leg,) = report.to_dict()["hvac_legends"]
    assert leg["sheet"] == "M-601" and leg["title"] == "HVAC SYMBOL LEGEND"
    assert [r["class"] for r in leg["rows"]] == ["diffuser", "grille", "vav", "sensor", None]
    assert all(len(r["symbol_bbox_pt"]) == 4 for r in leg["rows"])
    (rq,) = [r for r in model.review_queue if r.kind == "hvac_legend_unmapped"]
    assert "MANUAL VOLUME DAMPER" in rq.description and rq.provenance.method == "pdf_legend"
    assert any("4 naming an HVAC class" in n for n in report.notes)
    # the legend does not disturb tag placement
    assert list(model.zones) == ["L1-Z-VAV-1"]


def test_a_legend_repeated_on_two_sheets_is_one_review_item(tmp_path):
    from test_hvac_legend import legend
    from test_pdf_schedules import vav_schedule

    pages = [
        _arch("A-101", "FIRST FLOOR PLAN", 1),
        _tb("M-001", "MECHANICAL LEGEND AND NOTES") + legend(900, 1000),
        _tb("M-601", "MECHANICAL SCHEDULES") + vav_schedule(100, 1000) + legend(900, 1000),
    ]
    model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    assert [lg["sheet"] for lg in report.to_dict()["hvac_legends"]] == ["M-001", "M-601"]
    (rq,) = [r for r in model.review_queue if r.kind == "hvac_legend_unmapped"]
    assert "M-001, M-601" in rq.description


def _legend_mech_page(number="M-101", scale=SCALE_NOTE, outside=False, east=True, tags=()):
    """A mechanical plan drawn in a second symbol style with its own legend:
    two diffusers in the west room (x < 4 m), one diffuser and a thermostat in
    the east room (none with ``east=False``). ``tags``: (text, (x, y) m)."""
    from test_hvac_legend_templates import circle, damper, xbox

    x, y = 1100, 1000
    leg = text(x, y, "HVAC SYMBOL LEGEND", 12)
    leg += xbox(x, y - 54) + text(x + 40, y - 50, "SUPPLY AIR DIFFUSER", 8)
    leg += circle(x + 7, y - 72) + text(x + 40, y - 75, "THERMOSTAT", 8)
    leg += damper(x, y - 102) + text(x + 40, y - 100, "MANUAL VOLUME DAMPER", 8)
    at = [(2, 2), (2, 4.5)] + ([(7, 2)] if east else [])
    plan = "".join(xbox(px - 7, py - 7) for px, py in (_pt(*a) for a in at))
    if east:
        plan += circle(*_pt(7, 4.5))
    for tag, a in tags:
        plan += text(*_pt(*a), tag, 8)
    if outside:  # a diffuser drawn beyond the building shell
        px, py = _pt(13, 3)
        plan += xbox(px - 7, py - 7)
    body = _tb(number, "FIRST FLOOR MECHANICAL PLAN") + leg + plan
    return body + (text(100, 48, scale, 8) if scale else "")


def test_legend_symbols_are_counted_on_their_own_sheet(tmp_path):
    """A firm's own symbols (not matchline's glyphs) are found on the sheet by
    cutting the legend row's symbol as a template (#744)."""
    pages = [_arch("A-101", "FIRST FLOOR PLAN", 1), _legend_mech_page(scale=None)]
    _model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    (lg,) = report.to_dict()["hvac_legends"]
    assert lg["symbol_hits"] == {"diffuser": 3, "sensor": 1}
    # no scale on the mechanical plan: it cannot register, so no rooms
    assert lg["symbols_by_room"] == {}
    assert any("diffuser 3, sensor 1; not placed in rooms" in n for n in report.notes)


def test_legend_symbols_land_in_the_rooms_they_are_drawn_in(tmp_path):
    pages = [_arch("A-101", "FIRST FLOOR PLAN", 1), _legend_mech_page()]
    model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    (lg,) = report.to_dict()["hvac_legends"]
    by_room = lg["symbols_by_room"]
    assert "" not in by_room
    west = min(model.spaces.values(), key=lambda sp: min(x for x, _ in sp.polygon_m)).id
    (east,) = [sid for sid in by_room if sid != west]
    assert by_room == {west: {"diffuser": 2}, east: {"diffuser": 1, "sensor": 1}}
    assert any("in rooms: 4 of 4" in n for n in report.notes)


def test_a_legend_symbol_in_no_room_is_counted_apart(tmp_path):
    pages = [_arch("A-101", "FIRST FLOOR PLAN", 1), _legend_mech_page(outside=True)]
    _model, report = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")
    (lg,) = report.to_dict()["hvac_legends"]
    assert lg["symbol_hits"] == {"diffuser": 4, "sensor": 1}
    assert lg["symbols_by_room"][""] == {"diffuser": 1}
    assert any("in rooms: 4 of 5" in n for n in report.notes)


def _legend_zone_set(tmp_path, tags, east=True, second_sheet=False):
    from test_pdf_schedules import vav_schedule

    pages = [_arch("A-101", "FIRST FLOOR PLAN", 1), _legend_mech_page(east=east, tags=tags)]
    if second_sheet:  # the same plan again, as a return-air sheet of the level
        pages.append(_legend_mech_page("M-102", east=east))
    pages.append(_tb("M-601", "MECHANICAL SCHEDULES") + vav_schedule(100, 1000))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")


def test_legend_diffusers_go_on_the_zone_serving_their_room(tmp_path):
    """#744: symbols found from the sheet's own legend reach the HVAC model,
    on the one zone whose terminal unit is tagged in the same room."""
    from shapely.geometry import Point, Polygon

    from validate import _Ctx
    from validate.invariants import _check_hvac_zone_coverage

    model, report = _legend_zone_set(tmp_path, [("VAV-1", (1.5, 3.2))])
    assert list(model.zones) == ["L1-Z-VAV-1"]
    z = model.zones["L1-Z-VAV-1"]
    (sid,) = z.space_ids
    assert [d.type for d in z.diffusers] == ["diffuser", "diffuser"]
    assert [d.id for d in model.spaces[sid].hvac.diffusers] == [d.id for d in z.diffusers]
    assert all(d.provenance.method == "legend_symbol" for d in z.diffusers)
    assert all(d.provenance.confidence <= 0.6 for d in z.diffusers)  # registration caps it
    room = Polygon(model.spaces[sid].polygon_m)
    assert all(room.contains(Point(d.x_m, d.y_m)) for d in z.diffusers)
    # the east room has a diffuser and a thermostat but no zone: not attached
    east = [sp for k, sp in model.spaces.items() if k != sid and sp.level_id == "L1"]
    assert all(not sp.hvac.diffusers and not sp.hvac.sensors for sp in east)
    assert any(
        "legend symbols on zones: 2 supply diffuser(s), 0 grille(s), 0 sensor(s); "
        "2 in rooms no zone serves" in n
        for n in report.notes
    )
    assert not [r for r in model.review_queue if r.kind == "zone_no_diffuser"]
    r = _check_hvac_zone_coverage(_Ctx(model=model))
    assert r.severity == "pass", r.message


def test_a_legend_thermostat_goes_on_its_zone_as_a_sensor(tmp_path):
    model, _r = _legend_zone_set(tmp_path, [("VAV-1", (1.5, 3.2)), ("VAV-2", (6.5, 3.2))])
    z = model.zones["L1-Z-VAV-2"]
    assert [d.type for d in z.diffusers] == ["diffuser"]
    assert [s.type for s in z.sensors] == ["sensor"]
    assert len({d.id for zz in model.zones.values() for d in zz.diffusers + zz.sensors}) == 4


def test_a_zone_with_no_legend_diffuser_in_its_room_goes_to_review(tmp_path):
    model, report = _legend_zone_set(
        tmp_path, [("VAV-1", (1.5, 3.2)), ("VAV-2", (6.5, 3.2))], east=False
    )
    assert not model.zones["L1-Z-VAV-2"].diffusers
    (rq,) = [r for r in model.review_queue if r.kind == "zone_no_diffuser"]
    assert rq.target == {"kind": "zone", "id": "L1-Z-VAV-2"}
    assert "VAV-2 is tagged in" in rq.description and rq.needs_review
    assert any("1 zone(s) with no supply diffuser found" in n for n in report.notes)


def test_legend_diffusers_in_a_room_two_zones_serve_stay_unassigned(tmp_path):
    """Which of two zones feeds a shared room's diffusers needs duct tracing:
    they are counted, not attached, and neither zone goes to review as having
    no diffuser, because the symbols were found."""
    model, report = _legend_zone_set(tmp_path, [("VAV-1", (1.5, 3.2)), ("VAV-2", (2.5, 3.2))])
    assert sorted(model.zones) == ["L1-Z-VAV-1", "L1-Z-VAV-2"]
    assert all(not z.diffusers for z in model.zones.values())
    assert any("2 in rooms several zones serve" in n for n in report.notes)
    assert not [r for r in model.review_queue if r.kind == "zone_no_diffuser"]


def test_a_legend_symbol_on_a_second_sheet_of_the_level_is_not_counted_twice(tmp_path):
    model, report = _legend_zone_set(tmp_path, [("VAV-1", (1.5, 3.2))], second_sheet=True)
    assert len(model.zones["L1-Z-VAV-1"].diffusers) == 2
    assert any("2 repeated on another sheet" in n for n in report.notes)


def test_zones_pass_the_hvac_coverage_check(tmp_path):
    from validate import _Ctx
    from validate.invariants import _check_hvac_zone_coverage

    model, _r = _mech_set(tmp_path, [("VAV-1", (1.5, 2)), ("VAV-2", (6.5, 2))])
    assert sorted(model.zones) == ["L1-Z-VAV-1", "L1-Z-VAV-2"]
    r = _check_hvac_zone_coverage(_Ctx(model=model))
    assert r.severity == "pass", r.message


# ---- #793: storefront drawn as a thin wall band, no glazing line ------------


def _thin_bay_set(tmp_path, rows=None, tag=None):
    from test_plan_walls import _thin_bay

    body = _outline(_mass(_thin_bay(3, 7) + PARTITION, [DOOR])) + _labels(1)
    if tag:
        body += text(*_pt(5, -0.6), tag, 8)
    pages = [_tb("A-101", "FIRST FLOOR PLAN") + text(100, 48, SCALE_NOTE, 8) + body]
    if rows:
        pages.append(_tb("A-601", "WINDOW SCHEDULE") + _window_schedule(rows))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")


def test_thin_band_on_the_envelope_goes_to_review_as_maybe_glazing(tmp_path):
    model, _rep = _thin_bay_set(tmp_path)
    assert all(not s.openings for s in model.spaces.values())  # stays opaque
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "may be storefront glazing" in rq.description and "modelled opaque" in rq.description
    gap = rq.target["gap"]
    assert gap["drawn"] == "thin_band" and gap["candidates"] == []
    assert gap["facade"] == "south" and gap["width_m"] == pytest.approx(3.7, abs=0.05)
    assert rq.target["kind"] == "wall" and rq.target["field"] == "opening"
    assert gap["space_id"] in model.spaces  # a review edit can add the window


def test_scheduled_storefront_tag_on_the_thin_band_is_modelled_not_reviewed(tmp_path):
    rows = [("SF-1", "12'-2\"", "8'-0\"", "FIXED")]
    model, _rep = _thin_bay_set(tmp_path, rows, tag="SF-1")
    (op,) = [o for s in model.spaces.values() for o in s.openings]
    assert op.tag == "SF-1" and op.provenance.method == "plan_wall_tag"
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


# ---- #746: lighting power per room from fixture tags on the plans --------


def _light_set(tmp_path, e101_tags, rcp_tags=None, scale=SCALE_NOTE):
    from test_pdf_schedules import lighting_schedule

    pages = [
        _arch("A-101", "FIRST FLOOR PLAN", 1),
        _mech_tagged("E-101", "FIRST FLOOR LIGHTING PLAN", e101_tags, scale),
    ]
    if rcp_tags is not None:
        pages.append(_mech_tagged("A-121", "FIRST FLOOR REFLECTED CEILING PLAN", rcp_tags))
    pages.append(_tb("E-601", "ELECTRICAL SCHEDULES") + lighting_schedule(100, 1000))
    return real_set.build_set_model(_set(tmp_path, pages), tmp_path / "run")


def test_fixture_tags_give_each_room_its_lighting_power(tmp_path):
    model, report = _light_set(
        tmp_path, [("A", (1.5, 2)), ("A", (2.5, 2)), ("B", (1.5, 1)), ("A", (6.5, 2))]
    )
    office, open_office = model.spaces["L1-101"], model.spaces["L1-102"]
    assert [f.tag for f in office.lighting.fixtures] == ["A", "A", "B"]
    assert office.lighting.total_w == pytest.approx(108.0)
    assert office.lighting.lpd_w_m2 == pytest.approx(108.0 / office.area_m2)
    assert office.lighting.lpd_w_ft2 == pytest.approx(office.lighting.lpd_w_m2 / 10.7639104)
    assert open_office.lighting.total_w == pytest.approx(45.0)
    prov = office.lighting.provenance
    assert prov.sheet_id == "E-101" and prov.method == "plan_fixture_tags"
    assert prov.confidence == pytest.approx(0.5) and "one tag counted as one fixture" in prov.note
    fx = office.lighting.fixtures[0]
    assert fx.watts == 45.0 and fx.fixture_class == "2X4 LED TROFFER"
    assert fx.provenance.bbox is not None
    rq = [r for r in model.review_queue if r.kind == "lighting_from_tags"]
    assert len(rq) == 1 and not rq[0].needs_review  # listed, does not block export
    assert rq[0].target == {"kind": "space", "ids": ["L1-101", "L1-102"]}
    assert "undercounted" in rq[0].description
    assert any("4 fixture tag(s) on E-101 placed in 2 room(s)" in n for n in report.notes)


def test_fixture_type_without_watts_is_counted_and_sent_to_review(tmp_path):
    model, _ = _light_set(tmp_path, [("A", (1.5, 2)), ("C", (2.5, 2)), ("A", (14, 2))])
    office = model.spaces["L1-101"]
    assert [f.tag for f in office.lighting.fixtures] == ["A", "C"]
    assert office.lighting.total_w == pytest.approx(45.0)
    rq = {r.kind: r for r in model.review_queue}
    assert rq["fixture_no_watts"].target == {"kind": "space", "ids": ["L1-101"]}
    assert "1 tag(s) fall in no room" in rq["lighting_from_tags"].description


def test_room_with_only_unwatted_fixtures_gets_no_lpd(tmp_path):
    model, _ = _light_set(tmp_path, [("C", (6.5, 2)), ("A", (1.5, 2))])
    exit_only = model.spaces["L1-102"].lighting
    assert [f.tag for f in exit_only.fixtures] == ["C"]
    assert exit_only.total_w == 0.0 and exit_only.lpd_w_m2 is None


def test_rcp_and_lighting_plan_are_not_both_counted(tmp_path):
    model, report = _light_set(
        tmp_path, [("A", (1.5, 2))], rcp_tags=[("A", (1.5, 2)), ("A", (2.5, 2))]
    )
    office = model.spaces["L1-101"]
    assert len(office.lighting.fixtures) == 2 and office.lighting.provenance.sheet_id == "A-121"
    assert any("E-101: 1 lighting fixture tag(s) not counted" in n for n in report.notes)


def test_lighting_plan_that_does_not_register_counts_nothing(tmp_path):
    model, _ = _light_set(tmp_path, [("A", (1.5, 2))], scale='SCALE: 1/4" = 1\'-0"')
    assert model.spaces["L1-101"].lighting.fixtures == []
    assert [r.kind for r in model.review_queue if r.id == "rq-lightreg-E-101"] == [
        "lighting_plan_unregistered"
    ]


def test_grid_bubble_labels_are_not_fixtures():
    from grid_detect import GridLine, GridSet

    g = GridSet("E-101", [GridLine("A", "v", 100.0, (0, 500), True, [(100.0, 40.0, 9.0)], 0.9)])
    assert real_set._in_grid_bubble([96, 36, 104, 44], g)
    assert not real_set._in_grid_bubble([196, 36, 204, 44], g)
    assert not real_set._in_grid_bubble([96, 36, 104, 44], None)


# ---- #793: a door drawn inside a storefront run --------------------------

# 0.9 m door between jambs at x=4.55 / 5.45 in a 9.7 m storefront (mullions at 2 and 8)
_SF_DOOR_ROWS = [("D1", "3'-0\"", "7'-0\"", "AL")]


def _storefront_door_set(tmp_path, win_rows, door_rows=None, tag=None):
    from test_pdf_schedules import door_schedule
    from test_plan_walls import _door_symbol, _glazing, _mullion_ticks

    a = _pt(5, 3)
    body = _outline(_mass(SHELL)) + text(*a, "LOBBY", 8) + text(a[0], a[1] - 10, "101", 8)
    body += _glazing(0, 10) + _mullion_ticks([2, 4.55, 5.45, 8])
    body += _door_symbol((4.55, 0), (4.55, 0.9), (5.45, 0))
    if tag:
        body += text(*_pt(3, -0.6), tag, 8)
    pages = [
        _tb("A-101", "FIRST FLOOR PLAN") + text(100, 48, SCALE_NOTE, 8) + body,
        _tb("A-601", "WINDOW SCHEDULE") + _window_schedule(win_rows),
    ]
    if door_rows:
        pages.append(_tb("A-602", "DOOR SCHEDULE") + door_schedule(100, 1000, rows=door_rows))
    model, rep = real_set.build_set_model(_set(tmp_path, pages), tmp_path / "out")
    ops = sorted((o for s in model.spaces.values() for o in s.openings), key=lambda o: o.id)
    return model, rep, ops


def test_storefront_row_without_its_door_models_glass_and_door(tmp_path):
    # SF-1 scheduled 28'-10" (8.79 m): the 9.7 m run less the 0.9 m door
    rows = [("SF-1", "28'-10\"", "8'-0\"", "FIXED")]
    model, rep, ops = _storefront_door_set(tmp_path, rows, _SF_DOOR_ROWS)
    assert [(o.category, o.tag) for o in ops] == [("window", "SF-1"), ("door", "D1")]
    glass, door = ops
    assert glass.width_m == pytest.approx(346 * 0.0254)
    assert "glass less the 1 door" in glass.provenance.note
    assert door.provenance.method == "plan_glazing_door" and "SF-1" in door.provenance.note
    assert door.width_m == pytest.approx(36 * 0.0254)
    assert door.height_m == pytest.approx(84 * 0.0254, abs=1e-3)  # schedule height
    assert door.s_center_m == pytest.approx(5.0, abs=0.05) and door.host_facade == "south"
    assert rep.levels[0]["openings"]["modelled"] == 2
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


def test_storefront_row_spanning_the_run_includes_its_door(tmp_path):
    rows = [("SF-1", "31'-10\"", "8'-0\"", "FIXED")]  # 9.70 m: the door is part of it
    _model, _rep, ops = _storefront_door_set(tmp_path, rows, _SF_DOOR_ROWS)
    assert [(o.category, o.tag) for o in ops] == [("window", "SF-1")]
    assert "includes the 1 door" in ops[0].provenance.note


def test_unscheduled_door_in_a_storefront_goes_to_review(tmp_path):
    rows = [("SF-1", "28'-10\"", "8'-0\"", "FIXED")]
    model, rep, ops = _storefront_door_set(tmp_path, rows)
    assert [(o.category, o.tag) for o in ops] == [("window", "SF-1")]
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    assert "door drawn in storefront SF-1" in rq.description
    assert rq.target["gap"]["drawn"] == "door" and rq.target["gap"]["width_m"] == pytest.approx(
        0.9, abs=0.03
    )
    assert rep.levels[0]["openings"]["unsized"] == 1


def test_storefront_tag_names_the_glass_row_less_its_door(tmp_path):
    # by width SF-1 and SF-2 disagree on height; the SF-1 tag on the plan settles it
    rows = [("SF-1", "28'-10\"", "8'-0\"", "FIXED"), ("SF-2", "28'-10\"", "9'-0\"", "FIXED")]
    model, _rep, ops = _storefront_door_set(tmp_path, rows, _SF_DOOR_ROWS, tag="SF-1")
    assert [(o.category, o.tag) for o in ops] == [("window", "SF-1"), ("door", "D1")]
    glass = ops[0]
    assert glass.provenance.method == "plan_gap_tag"
    assert glass.provenance.confidence == pytest.approx(0.80)  # 0.85 less 0.05: doors taken out
    assert "confidence lowered" in glass.provenance.note
    assert not [r for r in model.review_queue if r.kind == "opening_unsized"]


def test_a_provider_that_needs_walls_gets_each_plans_walls(tmp_path):
    """#743: vector_glazing is handed each plan's walls result, its height and
    its rendered px per pt."""
    import detection_provider as dp

    seen = {}

    class Rec(dp.VectorGlazingProvider):
        def detect(self, image_path, sheet_id, plan=None):
            seen[Path(str(image_path)).name] = plan
            return []

    pdf = _set(
        tmp_path, [_arch("A-101", "FIRST FLOOR PLAN", 1), _arch("A-102", "SECOND FLOOR PLAN", 2)]
    )
    _model, rep = real_set.build_set_model(pdf, tmp_path / "out", provider=Rec(), dpi=150)
    assert sorted(seen) == ["sheet_001.png", "sheet_002.png"]
    plan = seen["sheet_001.png"]
    assert plan["px_per_pt"] == pytest.approx(150 / 72)
    assert plan["height_pt"] > 0 and plan["walls"]["m_per_pt"]
    assert rep.detector["provider"] == "vector_glazing" and not rep.detector["eval_only"]


def test_a_provider_that_needs_the_scale_gets_it_per_sheet(tmp_path):
    """#743: a rule provider (door_swing) is handed each plan's rendered px per
    metre from that sheet's own scale, and a sheet with no scale is not run."""
    import detection_provider as dp

    seen = {}

    class Rec(dp.DoorSwingProvider):
        def detect(self, image_path, sheet_id, px_per_m=None, plan=None):
            seen[Path(str(image_path)).name] = px_per_m
            return []

    pdf = _set(
        tmp_path, [_arch("A-101", "FIRST FLOOR PLAN", 1), _arch("A-102", "SECOND FLOOR PLAN", 2)]
    )
    _model, rep = real_set.build_set_model(pdf, tmp_path / "out", provider=Rec(), dpi=150)
    assert sorted(seen) == ["sheet_001.png", "sheet_002.png"]
    m_per_pt = rep.to_dict()["sheets"][0]["stages"]["scale"]["m_per_pt"]
    assert seen["sheet_001.png"] == pytest.approx(150 / 72 / m_per_pt)
    assert rep.detector["provider"] == "door_swing" and not rep.detector["eval_only"]
    by = {s["file"]: s for s in rep.to_dict()["sheets"]}
    assert by["sheet_001.json"]["stages"]["symbols"]["provider"] == "door_swing"


def test_a_combined_provider_gets_the_walls_and_the_scale(tmp_path):
    """#743: door and window providers run together on one set; the set reader
    hands the combined provider each plan's walls and its px per metre."""
    import detection_provider as dp

    seen = {}

    class Doors(dp.DoorSwingProvider):
        def detect(self, image_path, sheet_id, px_per_m=None, plan=None):
            got = seen.setdefault(Path(str(image_path)).name, {})
            got["px_per_m"], got["door_plan"] = px_per_m, plan
            return []

    class Glass(dp.VectorGlazingProvider):
        def detect(self, image_path, sheet_id, plan=None):
            seen.setdefault(Path(str(image_path)).name, {})["plan"] = plan
            return []

    pdf = _set(
        tmp_path, [_arch("A-101", "FIRST FLOOR PLAN", 1), _arch("A-102", "SECOND FLOOR PLAN", 2)]
    )
    prov = dp.CombinedProvider([Doors(), Glass()])
    _model, rep = real_set.build_set_model(pdf, tmp_path / "out", provider=prov, dpi=150)
    assert sorted(seen) == ["sheet_001.png", "sheet_002.png"]
    m_per_pt = rep.to_dict()["sheets"][0]["stages"]["scale"]["m_per_pt"]
    got = seen["sheet_001.png"]
    assert got["px_per_m"] == pytest.approx(150 / 72 / m_per_pt)
    assert got["plan"]["walls"]["m_per_pt"] and got["plan"]["px_per_pt"] == pytest.approx(150 / 72)
    # the door part gets the same walls, for thin-partition gaps (#874)
    assert got["door_plan"] == got["plan"] and "thin_gaps" in got["plan"]["walls"]
    assert rep.detector["provider"] == "combined" and not rep.detector["eval_only"]
