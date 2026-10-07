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

sys.path.insert(0, str(Path(__file__).parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402
from test_plan_walls import DOOR, PARTITION, SHELL, _mass, _outline, _pt  # noqa: E402

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
    # plan doors are counted, not modelled, until schedules give tags and heights
    assert all(not s.openings for s in model.spaces.values())
    assert [lv["plan_openings"] for lv in rep.levels] == [1, 1]
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
