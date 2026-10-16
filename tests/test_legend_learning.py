"""Per-project legend tag learning (#995): sidecar, classify override, eval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli import main as cli_main  # noqa: E402
from hvac_legend import classify, read_legend  # noqa: E402
from legend_learning import (  # noqa: E402
    LegendTagsError,
    add_tags,
    eval_before_after,
    learn_from_set_report,
    load_tags,
    normalize,
    save_tags,
)


def _sheet(rows):
    """A vector sheet dict with one legend: title, column headers, then one
    drawn symbol (a box) left of each description."""
    texts = [
        {"text": "HVAC SYMBOL LEGEND", "bbox": [800.0, 700.0, 890.0, 712.0]},
        {"text": "SYMBOL", "bbox": [800.0, 716.0, 832.0, 724.0]},
        {"text": "DESCRIPTION", "bbox": [840.0, 716.0, 898.0, 724.0]},
    ]
    prims = []
    y = 730.0
    for desc in rows:
        texts.append({"text": desc, "bbox": [840.0, y, 900.0, y + 10.0]})
        prims.append({"bbox": [800.0, y - 2.0, 820.0, y + 10.0]})
        y += 15.0
    return {"text": texts, "primitives": prims}


# --- normalize -------------------------------------------------------------


def test_normalize_collapses_space_and_case():
    assert normalize("  supply   grille ") == "SUPPLY GRILLE"


# --- sidecar load/save -----------------------------------------------------


def test_load_tags_absent_is_none(tmp_path):
    assert load_tags(tmp_path / "legend_tags.json") is None


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "legend_tags.json"
    save_tags(p, {"supply grille": "grille", "CEILING OUTLET": "diffuser"})
    assert load_tags(p) == {"CEILING OUTLET": "diffuser", "SUPPLY GRILLE": "grille"}
    doc = json.loads(p.read_text())
    assert doc["schema"] == "matchline.legend_tags/1"
    assert doc["tags"]["SUPPLY GRILLE"] == "grille"


def test_load_rejects_wrong_schema(tmp_path):
    p = tmp_path / "legend_tags.json"
    p.write_text(json.dumps({"schema": "matchline.legend_tags/9", "tags": {}}))
    with pytest.raises(LegendTagsError, match="expected schema"):
        load_tags(p)


def test_load_rejects_unknown_class(tmp_path):
    p = tmp_path / "legend_tags.json"
    p.write_text(json.dumps({"schema": "matchline.legend_tags/1", "tags": {"VAV BOX": "box"}}))
    with pytest.raises(LegendTagsError, match="unknown class"):
        load_tags(p)


def test_load_rejects_non_string_tags(tmp_path):
    p = tmp_path / "legend_tags.json"
    p.write_text(json.dumps({"schema": "matchline.legend_tags/1", "tags": {"VAV": 1}}))
    with pytest.raises(LegendTagsError, match="strings to strings"):
        load_tags(p)


# --- learning from a set report --------------------------------------------


def _report(rows_by_sheet):
    return {
        "hvac_legends": [
            {
                "sheet": sheet,
                "title": "HVAC SYMBOL LEGEND",
                "bbox_pt": [800, 700, 890, 712],
                "rows": [
                    {"description": d, "class": c, "symbol_bbox_pt": [800, y, 820, y + 12]}
                    for (d, c), y in zip(rows, [730, 745, 760])
                ],
                "symbol_hits": {},
                "symbols_by_room": {},
            }
            for sheet, rows in rows_by_sheet.items()
        ]
    }


def test_learn_keeps_mapped_and_lists_unmapped():
    tags, conflicts, unmapped = learn_from_set_report(
        _report({"M-101": [("SUPPLY AIR DIFFUSER", "diffuser"), ("SUPPLY GRILLE", None)]})
    )
    assert tags == {"SUPPLY AIR DIFFUSER": "diffuser"}
    assert unmapped == ["SUPPLY GRILLE"]
    assert conflicts == []


def test_learn_skips_conflicting_description():
    tags, conflicts, _ = learn_from_set_report(
        _report({"M-101": [("SIDE OUTLET", "diffuser")], "M-201": [("SIDE OUTLET", "grille")]})
    )
    assert "SIDE OUTLET" not in tags
    assert conflicts == ["SIDE OUTLET: diffuser vs grille"]


def test_learn_same_description_twice_agrees():
    tags, conflicts, _ = learn_from_set_report(
        _report({"M-101": [("SUPPLY GRILLE", "grille")], "M-201": [("SUPPLY GRILLE", "grille")]})
    )
    assert tags == {"SUPPLY GRILLE": "grille"}
    assert conflicts == []


# --- manual tags -----------------------------------------------------------


def test_add_tags_maps_and_overrides():
    tags = add_tags(
        {"SUPPLY GRILLE": "grille"}, ["CEILING OUTLET=diffuser", "supply grille=diffuser"]
    )
    assert tags == {"SUPPLY GRILLE": "diffuser", "CEILING OUTLET": "diffuser"}


def test_add_tags_rejects_bad_pair():
    with pytest.raises(LegendTagsError, match="DESC="):
        add_tags({}, ["SUPPLY GRILLE=box"])


# --- classify / read_legend with learned tags ------------------------------


def test_classify_learned_wins_over_builtin():
    # the built-in rules leave a bare grille unmapped and read a return
    # diffuser as a grille; a learned tag says otherwise on both counts
    assert classify("SUPPLY GRILLE", {"SUPPLY GRILLE": "grille"}) == "grille"
    assert classify("SUPPLY GRILLE", {}) is None
    assert classify("RETURN AIR DIFFUSER", {"RETURN AIR DIFFUSER": "diffuser"}) == "diffuser"


def test_classify_learned_miss_falls_back_to_rules():
    assert classify("VAV BOX", {"THERMOSTAT": "sensor"}) == "vav"


def test_read_legend_applies_learned_tags():
    sheet = _sheet(["SUPPLY GRILLE", "CEILING OUTLET", "VAV BOX"])
    bare = read_legend(sheet)
    assert [r.cls for r in bare[0].rows] == [None, None, "vav"]
    tagged = read_legend(sheet, learned={"SUPPLY GRILLE": "grille", "CEILING OUTLET": "diffuser"})
    assert [r.cls for r in tagged[0].rows] == ["grille", "diffuser", "vav"]
    # the sidecar never leaks into a run that does not pass it
    assert [r.cls for r in read_legend(sheet)[0].rows] == [None, None, "vav"]


# --- before/after eval -----------------------------------------------------


def test_eval_counts_newly_mapped_rows():
    before = _report({"M-101": [("SUPPLY GRILLE", None), ("VAV BOX", "vav")]})
    after = _report({"M-101": [("SUPPLY GRILLE", "grille"), ("VAV BOX", "vav")]})
    r = eval_before_after(before, after)
    assert r["regressions"] == 0
    assert r["newly_mapped"] == 1
    assert r["mapped_before"] == 1 and r["mapped_after"] == 2


def test_eval_flags_lost_and_flipped_as_regressions():
    before = _report({"M-101": [("RETURN AIR DIFFUSER", "grille"), ("VAV BOX", "vav")]})
    after = _report(
        {
            "M-101": [
                ("RETURN AIR DIFFUSER", None),
                ("VAV BOX", "ahu"),
            ]
        }
    )
    r = eval_before_after(before, after)
    assert r["regressions"] == 2
    assert r["lost"] == [{"sheet": "M-101", "description": "RETURN AIR DIFFUSER", "was": "grille"}]
    assert r["flipped"] == [
        {"sheet": "M-101", "description": "VAV BOX", "was": "vav", "now": "ahu"}
    ]


# --- CLI: learn-legend and legend-eval -------------------------------------


def test_cli_learn_legend_writes_sidecar(tmp_path, capsys):
    report = tmp_path / "stage_00_set_report.json"
    report.write_text(
        json.dumps(_report({"M-101": [("SUPPLY AIR DIFFUSER", "diffuser"), ("SIDE OUTLET", None)]}))
    )
    out = tmp_path / "legend_tags.json"
    cli_main(["learn-legend", str(report), "--tag", "SIDE OUTLET=grille", "--out", str(out)])
    text = capsys.readouterr().out
    assert "learned 2 legend tag(s)" in text
    assert load_tags(out) == {"SUPPLY AIR DIFFUSER": "diffuser", "SIDE OUTLET": "grille"}


def test_cli_learn_legend_reports_unmapped(tmp_path, capsys):
    report = tmp_path / "stage_00_set_report.json"
    report.write_text(json.dumps(_report({"M-101": [("DAMPER", None)]})))
    cli_main(["learn-legend", str(tmp_path)])  # out_dir form, default --out
    assert "DAMPER" in capsys.readouterr().out
    assert load_tags(tmp_path / "legend_tags.json") == {}


def test_cli_learn_legend_rejects_bad_tag(tmp_path):
    report = tmp_path / "stage_00_set_report.json"
    report.write_text(json.dumps(_report({})))
    with pytest.raises(SystemExit, match="DESC="):
        cli_main(
            ["learn-legend", str(report), "--tag", "DAMPER=box", "--out", str(tmp_path / "x.json")]
        )


def test_cli_legend_eval_clean_run_passes(tmp_path, capsys):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(_report({"M-101": [("SUPPLY GRILLE", None)]})))
    after.write_text(json.dumps(_report({"M-101": [("SUPPLY GRILLE", "grille")]})))
    cli_main(["legend-eval", str(before), str(after)])
    assert "newly mapped 1" in capsys.readouterr().out


def test_cli_legend_eval_exits_on_regression(tmp_path, capsys):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(_report({"M-101": [("VAV BOX", "vav")]})))
    after.write_text(json.dumps(_report({"M-101": [("VAV BOX", None)]})))
    with pytest.raises(SystemExit, match="regression"):
        cli_main(["legend-eval", str(before), str(after)])
    assert "LOST" in capsys.readouterr().out
