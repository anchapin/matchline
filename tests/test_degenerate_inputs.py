"""Degenerate inputs come out clean, never as tracebacks or NaN (#1124).

Every corner case named in the issue: blank page in a multi-page set,
schedule with no data rows, empty ingested folder, single-room model,
model with no spaces, zero-area space, no envelope walls, space with no
openings, and a sheet with nothing to scale. Nothing here is expected to
crash; the pinned behavior is what the pipeline already does well.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.append(str(Path(__file__).resolve().parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

import ingest.drawing_scale as S  # noqa: E402
import grid_detect as G  # noqa: E402
import ingest.pdf_ingest as P  # noqa: E402
import ingest.pdf_schedules as PS  # noqa: E402
import tests.test_pdf_schedules as TPS  # noqa: E402
from tests.model_factory import make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _ingest(tmp_path, pages, name="set.pdf", dpi=50):
    pdf = write_pdf(tmp_path / name, pages)
    return P.ingest_pdf(pdf, out_dir=tmp_path / "out", dpi=dpi)


def _strict_report_json(report) -> str:
    """The report serializes as strict JSON: no NaN or Infinity tokens."""
    out = json.dumps(report.to_dict(), allow_nan=False)
    assert "NaN" not in out and "Infinity" not in out
    return out


# --- PDF side -------------------------------------------------------------


def test_blank_page_in_a_multi_page_set_keeps_its_neighbours(tmp_path):
    pages = [
        PageSpec(width=600, height=400, content=text(50, 200, "A101 FLOOR PLAN", size=12)),
        PageSpec(width=600, height=400, content=""),
        PageSpec(width=600, height=400, content=line(100, 100, 300, 100, 2)),
    ]
    r = _ingest(tmp_path, pages)
    kinds = [s.kind for s in r.sheets]
    assert kinds == ["vector", "empty", "vector"]
    assert len(r.sheets[0].text) > 0  # page 1 title untouched
    assert len(r.sheets[2].primitives) == 1  # page 3 line untouched


def test_blank_sheet_scale_fails_clean(tmp_path):
    sheet = _ingest(tmp_path, [PageSpec(width=600, height=400, content="")]).sheets[0]
    sc = S.sheet_scale(sheet)
    assert sc.ratio is None
    assert sc.needs_review is True
    assert sc.reason


def test_blank_sheet_grid_detect_returns_empty(tmp_path):
    sheet = _ingest(tmp_path, [PageSpec(width=600, height=400, content="")]).sheets[0]
    gs = G.detect_grids(sheet, "blank")
    assert gs.lines == []


def test_header_only_schedule_is_flagged_not_half_read(tmp_path):
    # A titled door-schedule table with a header row but zero data rows.
    heights = [16, 14, 14]
    cells = {
        (0, 0): "MARK",
        (0, 1): "SIZE",
        (1, 1): "WIDTH",
        (1, 2): "HEIGHT",
        (0, 3): "TYPE",
        (2, 0): "NOTES: VERIFY ALL OPENINGS IN FIELD",
    }
    content = text(60, 746, "DOOR SCHEDULE", 10) + TPS.grid(
        60, 740, [50, 60, 60, 110], heights,
        merges=[(0, 1, 0, 0), (0, 0, 1, 2), (0, 1, 3, 3), (2, 2, 0, 3)],
        cells=cells,
    )  # fmt: skip
    pdf = write_pdf(
        tmp_path / "z.pdf", [PageSpec(width=TPS.PAGE_W, height=TPS.PAGE_H, content=content)]
    )
    P.ingest_pdf(pdf, out_dir=tmp_path / "out")
    r = PS.schedules_for_sheets(tmp_path / "out")
    scheds = [s for res in r.values() for s in res.get("schedules", [])]
    assert len(scheds) == 1
    assert scheds[0]["status"] == "unparsed"
    assert "no data rows" in scheds[0].get("reason", "")
    assert scheds[0].get("rows", []) == []


def test_schedules_on_an_empty_ingested_dir(tmp_path):
    (tmp_path / "out").mkdir()
    assert PS.schedules_for_sheets(tmp_path / "out") == {}


# --- model side ------------------------------------------------------------


def test_single_room_model_validates():
    m = make_clean_model()
    keep = next(iter(m.spaces))
    m.spaces = {k: v for k, v in m.spaces.items() if k == keep}
    r = run_checks(m)
    assert r.results
    _strict_report_json(r)


def test_model_with_no_spaces_validates():
    m = make_clean_model()
    m.spaces = {}
    r = run_checks(m)
    assert r.results
    _strict_report_json(r)


def test_zero_area_space_is_flagged_not_nan():
    m = make_clean_model()
    sid = next(iter(m.spaces))
    m.spaces[sid].polygon_m = [[0, 0], [5, 0], [5, 0]]  # collinear: zero area
    r = run_checks(m)
    area = [c for c in r.results if c.check_id == "space_area_matches_polygon"]
    assert area and area[0].severity == "warn"
    _strict_report_json(r)


def test_no_envelope_walls_validates():
    m = make_clean_model()
    m.envelope = []
    r = run_checks(m)
    assert r.results
    _strict_report_json(r)


def test_space_with_no_openings_validates():
    m = make_clean_model()
    for s in m.spaces.values():
        s.openings = [o for o in s.openings if o.category != "door"]
    r = run_checks(m)
    assert r.results
    _strict_report_json(r)
