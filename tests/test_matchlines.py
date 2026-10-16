"""Match lines, split sheets, and enlarged-plan deduplication (#994)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, text, write_pdf  # noqa: E402
from test_plan_walls import SHELL, _mass, _outline, _pt  # noqa: E402

import matchlines  # noqa: E402
import real_set  # noqa: E402

W, H = 1728, 1152
SCALE_NOTE = 'SCALE: 1/8" = 1\'-0"'


def _entry(number, title, type_="floor_plan", level="L1", disc="A"):
    """One sheet_index entry in the provenance-dict shape build_index emits."""
    return {
        "file": f"sheet_{number[-3:]}.json",
        "number": {"value": number, "source": "test"},
        "title": {"value": title, "source": "test"},
        "type": {"value": type_, "source": "test"},
        "discipline": {"value": disc, "source": "test"},
        "level": {"value": level, "source": "test"},
    }


def _spans_sheet(spans):
    return {"text": [{"text": t, "bbox": b} for t, b in spans]}


def _hit(file, key):
    return matchlines.MatchLine(file=file, key=key, text=f"MATCH LINE {key}", bbox=(0, 0, 10, 10))


# ---------------------------------------------------------------- detection


def test_match_line_label_variants():
    sheet = _spans_sheet(
        [
            ("MATCH LINE A", (0, 0, 100, 10)),
            ("MATCHLINE 3", (0, 20, 100, 30)),
            ("MATCH LINE - AREA B", (0, 40, 100, 50)),
            ("MATCH LINE", (0, 60, 100, 70)),
            ("MATCHING LINEWORK", (0, 80, 100, 90)),
            ("BEYOND MATCH LINE", (0, 100, 100, 110)),
            ("MATCHLINE TEST CLINIC", (0, 120, 100, 130)),
        ]
    )
    got = matchlines.find_match_lines(sheet, "sheet_001.json")
    # Only whole-span match-line labels count: a project name that merely
    # contains the words, "MATCHING LINEWORK", and "BEYOND MATCH LINE" do not.
    assert [(g.key, g.file) for g in got] == [
        ("A", "sheet_001.json"),
        ("3", "sheet_001.json"),
        ("B", "sheet_001.json"),
        ("", "sheet_001.json"),
    ]


# ---------------------------------------------------------------- pairing


def test_pair_two_sheets_of_one_level():
    rep = matchlines.pair_match_lines(
        {
            "sheet_001.json": [_hit("sheet_001.json", "A")],
            "sheet_002.json": [_hit("sheet_002.json", "A")],
        },
        {"sheet_001.json": "L1", "sheet_002.json": "L1"},
    )
    assert rep.pairs == [{"key": "A", "level": "L1", "files": ["sheet_001.json", "sheet_002.json"]}]
    assert rep.conflicts == [] and rep.unpaired == []


def test_three_sheets_with_one_key_is_a_conflict():
    hits = {f"sheet_00{i}.json": [_hit(f"sheet_00{i}.json", "A")] for i in (1, 2, 3)}
    rep = matchlines.pair_match_lines(hits, {f"sheet_00{i}.json": "L1" for i in (1, 2, 3)})
    assert rep.pairs == []
    assert rep.conflicts[0]["key"] == "A" and len(rep.conflicts[0]["files"]) == 3


def test_unpaired_key_records_its_missing_neighbour():
    rep = matchlines.pair_match_lines(
        {"sheet_001.json": [_hit("sheet_001.json", "B")]}, {"sheet_001.json": "L1"}
    )
    assert rep.unpaired == [
        {
            "file": "sheet_001.json",
            "key": "B",
            "level": "L1",
            "reason": "match line B has no counterpart sheet in the set",
        }
    ]


def test_keyless_label_is_recorded_but_never_paired():
    rep = matchlines.pair_match_lines(
        {
            f: [matchlines.MatchLine(f, "", "MATCH LINE", (0, 0, 1, 1))]
            for f in ("sheet_001.json", "sheet_002.json")
        },
        {"sheet_001.json": "L1", "sheet_002.json": "L1"},
    )
    assert rep.pairs == []
    assert len(rep.unpaired) == 2 and all(u["key"] == "" for u in rep.unpaired)


def test_middle_sheet_repeated_key_pairs_each_edge():
    """A three-sheet strip whose middle sheet repeats the key: two pairs (#1042)."""
    rep = matchlines.pair_match_lines(
        {
            "sheet_001.json": [_hit("sheet_001.json", "A")],
            "sheet_002.json": [_hit("sheet_002.json", "A"), _hit("sheet_002.json", "A")],
            "sheet_003.json": [_hit("sheet_003.json", "A")],
        },
        {f"sheet_00{i}.json": "L1" for i in (1, 2, 3)},
    )
    assert len(rep.pairs) == 2
    assert {tuple(p["files"]) for p in rep.pairs} == {
        ("sheet_001.json", "sheet_002.json"),
        ("sheet_002.json", "sheet_003.json"),
    }
    assert rep.conflicts == [] and rep.unpaired == []


def test_extra_edge_without_counterpart_is_unpaired():
    """A middle sheet's second edge with no counterpart no longer vanishes (#1042)."""
    rep = matchlines.pair_match_lines(
        {
            "sheet_001.json": [_hit("sheet_001.json", "A")],
            "sheet_002.json": [_hit("sheet_002.json", "A"), _hit("sheet_002.json", "A")],
        },
        {"sheet_001.json": "L1", "sheet_002.json": "L1"},
    )
    assert len(rep.pairs) == 1
    assert len(rep.unpaired) == 1
    assert rep.unpaired[0]["file"] == "sheet_002.json"
    assert "counterpart" in rep.unpaired[0]["reason"]


def test_repeated_key_on_one_sheet_alone_stays_unpaired():
    rep = matchlines.pair_match_lines(
        {"sheet_002.json": [_hit("sheet_002.json", "A"), _hit("sheet_002.json", "A")]},
        {"sheet_002.json": "L1"},
    )
    assert rep.pairs == [] and len(rep.unpaired) == 1
    assert "no counterpart sheet" in rep.unpaired[0]["reason"]


def test_same_sheets_on_two_edges_needs_review():
    """A key pairing the same two sheets twice is ambiguous without label positions."""
    rep = matchlines.pair_match_lines(
        {
            "sheet_001.json": [_hit("sheet_001.json", "A"), _hit("sheet_001.json", "A")],
            "sheet_002.json": [_hit("sheet_002.json", "A"), _hit("sheet_002.json", "A")],
        },
        {"sheet_001.json": "L1", "sheet_002.json": "L1"},
    )
    assert len(rep.pairs) == 1
    assert len(rep.conflicts) == 1
    assert "more than one edge" in rep.conflicts[0]["reason"]


def test_lowercase_title_reference_links():
    entries = {
        "sheet_101.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A (a-101)", type_="enlarged_plan"),
    }
    walls = {"sheet_101.json": _walls_res([[0, 0], [1, 1]])}
    rep = matchlines.link_enlarged_plans(entries, walls, {}, ".")
    assert rep.links[0]["basis"] == "title_reference"
    assert rep.links[0]["base"] == "sheet_101.json"


def test_lowercase_base_number_still_links():
    entries = {
        "sheet_101.json": _entry("a-101", "FIRST FLOOR PLAN"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A (A-101)", type_="enlarged_plan"),
    }
    walls = {"sheet_101.json": _walls_res([[0, 0], [1, 1]])}
    rep = matchlines.link_enlarged_plans(entries, walls, {}, ".")
    assert rep.links[0]["basis"] == "title_reference"
    assert rep.links[0]["base"] == "sheet_101.json"


def test_same_key_on_two_levels_never_pairs():
    rep = matchlines.pair_match_lines(
        {
            "sheet_001.json": [_hit("sheet_001.json", "A")],
            "sheet_002.json": [_hit("sheet_002.json", "A")],
        },
        {"sheet_001.json": "L1", "sheet_002.json": "L2"},
    )
    assert rep.pairs == [] and len(rep.unpaired) == 2


# ---------------------------------------------------------------- links


def _walls_res(*polys):
    return {"rooms": [{"polygon_m": poly} for poly in polys], "walls": []}


def test_enlarged_plan_links_by_title_reference():
    entries = {
        "sheet_001.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A (A-101)", type_="enlarged_plan"),
    }
    walls = {"sheet_001.json": _walls_res([[0, 0], [1, 1]])}
    rep = matchlines.link_enlarged_plans(entries, walls, {}, ".")
    assert rep.links == [
        {"file": "sheet_501.json", "base": "sheet_001.json", "basis": "title_reference"}
    ]
    assert rep.exclusions == [
        {
            "file": "sheet_501.json",
            "kind": "enlarged_plan",
            "reason": "enlarged plan: its area is already on the base plan",
            "linked_to": "sheet_001.json",
            "basis": "title_reference",
        }
    ]


def test_enlarged_plan_links_by_content_overlap(tmp_path, monkeypatch):
    entries = {
        "sheet_001.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A", type_="enlarged_plan"),
    }
    walls = {"sheet_001.json": _walls_res([[0, 0], [20, 0], [20, 12], [0, 12]])}
    scales = {"sheet_501.json": {"m_per_pt": 0.02}}

    def fake_extract(sheet, m_per_pt):
        # a small crop, entirely inside the base plan's extent
        return SimpleNamespace(to_dict=lambda: _walls_res([[5, 3], [8, 3], [8, 6], [5, 6]]))

    monkeypatch.setattr("plan_walls.extract_walls", fake_extract)
    (tmp_path / "sheet_501.json").write_text("{}")
    rep = matchlines.link_enlarged_plans(entries, walls, scales, tmp_path)
    assert rep.links[0]["basis"] == "content_overlap"
    assert rep.links[0]["base"] == "sheet_001.json"
    assert rep.links[0]["area_ratio"] < 0.6
    assert rep.exclusions and rep.exclusions[0]["linked_to"] == "sheet_001.json"


def test_enlarged_plan_between_two_bases_without_a_scale_is_reviewed(tmp_path):
    entries = {
        "sheet_001.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_002.json": _entry("A-102", "FIRST FLOOR PLAN EAST"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A", type_="enlarged_plan"),
    }
    walls = {"sheet_001.json": _walls_res([[0, 0], [10, 0], [10, 8], [0, 8]])}
    scales = {"sheet_501.json": {}}  # no scale: content cannot be compared
    rep = matchlines.link_enlarged_plans(entries, walls, scales, tmp_path)
    assert rep.links == [
        {
            "file": "sheet_501.json",
            "base": None,
            "basis": "none",
            "reason": "no title reference and no scale to compare content with",
        }
    ]
    assert rep.exclusions == []


def test_ambiguous_overlap_picks_the_smallest_base(tmp_path, monkeypatch):
    entries = {
        "sheet_001.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_002.json": _entry("A-102", "FIRST FLOOR PLAN EAST"),
        "sheet_501.json": _entry("A-501", "ENLARGED PLAN - AREA A", type_="enlarged_plan"),
    }
    walls = {
        "sheet_001.json": _walls_res([[0, 0], [20, 0], [20, 12], [0, 12]]),
        "sheet_002.json": _walls_res([[0, 0], [10, 0], [10, 8], [0, 8]]),
    }
    scales = {"sheet_501.json": {"m_per_pt": 0.02}}

    def fake_extract(sheet, m_per_pt):
        return SimpleNamespace(to_dict=lambda: _walls_res([[2, 2], [6, 2], [6, 5], [2, 5]]))

    monkeypatch.setattr("plan_walls.extract_walls", fake_extract)
    (tmp_path / "sheet_501.json").write_text("{}")
    rep = matchlines.link_enlarged_plans(entries, walls, scales, tmp_path)
    # both bases contain the crop; the smaller base wins
    assert rep.links[0]["base"] == "sheet_002.json"
    assert rep.links[0]["basis"] == "content_overlap"


# ---------------------------------------------------------------- exclusions


def test_partial_plan_a_full_plan_covers_is_excluded():
    entries = {
        "sheet_001.json": _entry("A-101", "FIRST FLOOR PLAN"),
        "sheet_002.json": _entry("A-201", "FIRST FLOOR PLAN - AREA A", type_="partial_plan"),
        "sheet_003.json": _entry("A-301", "SECOND FLOOR PLAN", level="L2"),
        "sheet_004.json": _entry(
            "A-401", "PARTIAL PLAN - ROOF", type_="partial_plan", level="ROOF"
        ),
    }
    got = matchlines.takeoff_exclusions(entries)
    assert [x["file"] for x in got] == ["sheet_002.json"]
    assert got[0]["kind"] == "partial_plan"


# ---------------------------------------------------------------- end to end


def _half(number, title, half):
    """A split floor plan: the east or west half of the two-room shell."""
    if half == "east":
        mass = [((5, 0), (10, 0), 0.3), ((10, 0), (10, 6), 0.3), ((10, 6), (5, 6), 0.3),
                ((5, 6), (5, 0), 0.15)]  # fmt: skip
        labels = _pt(6.5, 3)
        label = "OPEN OFFICE"
    else:
        mass = [((0, 0), (5, 0), 0.3), ((5, 0), (5, 6), 0.15), ((5, 6), (0, 6), 0.3),
                ((0, 6), (0, 0), 0.3)]  # fmt: skip
        labels = _pt(1.5, 3)
        label = "OFFICE"
    body = (
        _outline(_mass(mass)) + text(*labels, label, 8) + text(labels[0], labels[1] - 10, "101", 8)
    )
    return (
        text(1500, 220, title, 12)
        + text(1500, 250, "MATCH LINE A", 10)
        + text(1560, 40, number, 24)
        + text(100, 48, SCALE_NOTE, 8)
        + body
    )


def test_split_pair_enlarged_exclusion_and_review(tmp_path):
    pages = [
        _half("A-101", "FIRST FLOOR PLAN EAST", "east"),
        _half("A-102", "FIRST FLOOR PLAN WEST", "west"),
        _tb_enlarged("A-501", "ENLARGED PLAN - AREA A (A-101)"),
    ]
    pdf = write_pdf(tmp_path / "set.pdf", [PageSpec(width=W, height=H, content=c) for c in pages])
    model, report = real_set.build_set_model(pdf, tmp_path / "out")
    dedup = report.dedup
    assert {"key": "A", "level": "L1", "files": ["sheet_001.json", "sheet_002.json"]} in dedup[
        "pairs"
    ]
    enlarged = [x for x in dedup["exclusions"] if x["kind"] == "enlarged_plan"]
    assert enlarged and enlarged[0]["linked_to"] == "sheet_001.json"
    pairs = [r for r in model.review_queue if r.kind == "match_line_pair"]
    assert len(pairs) == 1 and "A-101" in pairs[0].description and "A-102" in pairs[0].description
    by_file = {s.file: s.stages.get("takeoff_dedup") for s in report.sheets}
    assert by_file["sheet_001.json"]["status"] == "ok"
    assert by_file["sheet_003.json"]["status"] == "excluded"


def _tb_enlarged(number, title):
    return (
        text(1500, 220, title, 12)
        + text(1560, 40, number, 24)
        + text(100, 48, SCALE_NOTE, 8)
        + _outline(_mass(SHELL))
    )
