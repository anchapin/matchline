"""Correction history: record, undo, revert-to-automatic (#997)."""

from __future__ import annotations

import json

import pytest

import review.corrections as C
import review.review_report as R
from building_model import BuildingModel, Construction, Provenance, Space, SpaceOpening


def _model() -> BuildingModel:
    m = BuildingModel(name="snap997")
    for sid, num in (("L1-101", "101"), ("L1-102", "102")):
        m.spaces[sid] = Space(id=sid, level_id="L1", number=num)
    m.spaces["L1-101"].openings.append(
        SpaceOpening(id="S-W1", tag="", category="window", width_m=1.2, height_m=1.5,
                     host_interval_m=[2.4, 3.6], s_center_m=3.0, area_m2=1.8)
    )  # fmt: skip
    m.constructions["WIN-A"] = Construction(id="WIN-A", name="window A")
    prov = Provenance(sheet_id="A-201", revision=1, method="elevation", confidence=0.6)
    m.flag_for_review("window_room_link", "window W1 -> room 101", 0.6, prov,
                      target={"kind": "opening", "id": "S-W1", "field": "space_id"})  # fmt: skip
    return m


def _op(m):
    return R._find_opening(m, "S-W1")


def _key(m):
    return f"opening:{_op(m)[1].id}:width_m"


def _doc(tmp_path):
    return C.load(tmp_path / "model.json")


# --- recording ---------------------------------------------------------------


def test_edit_records_before_after_source_and_timestamp(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    assert R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp) is True
    cs = _doc(tmp_path)["corrections"]
    assert len(cs) == 1
    c = cs[0]
    assert c["action"] == "edit" and c["source"] == "human"
    assert c["item_id"] == rid and c["seq"] == 1
    assert c["field"] == "width_m"
    assert c["before"] == 1.2 and c["after"] == 0.9
    assert c["target"]["kind"] == "opening" and c["target"]["id"] == "S-W1"
    assert c["state_before"]["status"] == "open"
    assert c["undone"] is False
    # a UTC timestamp, recorded in the project dir beside the model
    assert c["ts"].endswith("Z") and len(c["ts"]) == 20
    assert (tmp_path / "corrections.json").exists()
    assert json.loads((tmp_path / "corrections.json").read_text())["schema"] == C.SCHEMA
    # the field changed on the model
    assert _op(m)[1].width_m == 0.9


def test_confirm_without_a_field_records_the_state_change(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    # a second item with no editable field: a merge item edits only via its
    # own path, so use an item whose edit_field resolves empty
    prov = Provenance(sheet_id="A-999", revision=0, method="note", confidence=0.5)
    m.flag_for_review("facade_unclear", "east facade ratio", 0.9, prov,
                      target={"kind": "sheet", "id": "A-999"})  # fmt: skip
    rid = m.review_queue[-1].id
    assert R.decide(m, rid, "confirm", corrections_path=mp) is True
    cs = _doc(tmp_path)["corrections"]
    assert len(cs) == 1
    c = cs[0]
    assert c["action"] == "confirm" and c["field"] == ""
    assert c["before"] is None and c["after"] is None
    assert m.review_queue[-1].status == "confirmed"


def test_idempotent_replay_records_nothing(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    n1 = len(_doc(tmp_path)["corrections"])
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    assert len(_doc(tmp_path)["corrections"]) == n1
    assert R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp) is False


def test_undo_marks_undone_and_history_grows(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    C.undo(m, mp, 1)
    cs = _doc(tmp_path)["corrections"]
    assert len(cs) == 1 and cs[0]["undone"] is True and cs[0]["undone_ts"].endswith("Z")
    # the model took the value back and reopened the item
    assert _op(m)[1].width_m == 1.2
    assert m.review_queue[0].status == "open"
    # nothing left to undo
    assert C.undo(m, mp, 1) == []


# --- undo of the last N ------------------------------------------------------


def test_undo_of_the_last_n_in_reverse_order(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    R.decide(m, rid, "edit", "width_m=1.1", corrections_path=mp)
    assert _op(m)[1].width_m == 1.1
    undone = C.undo(m, mp, 2)
    assert [c["seq"] for c in undone] == [2, 1]
    assert _op(m)[1].width_m == 1.2  # both edits back to the automatic value
    assert _doc(tmp_path)["corrections"][0]["undone"] is True


def test_undo_restores_the_previous_value_not_the_automatic_one(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    R.decide(m, rid, "edit", "width_m=1.1", corrections_path=mp)
    C.undo(m, mp, 1)
    # the second correction is undone: the field shows the first edit again
    assert _op(m)[1].width_m == 0.9


# --- revert-to-automatic per field ------------------------------------------


def test_revert_to_automatic_restores_the_pipeline_value(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    R.decide(m, rid, "edit", "width_m=1.4", corrections_path=mp)
    r = C.revert_to_automatic(m, mp, _key(m))
    assert r["from"] == 1.4 and r["to"] == 1.2 and r["reverted"] == 2
    assert _op(m)[1].width_m == 1.2
    cs = _doc(tmp_path)["corrections"]
    assert all(c["undone_reason"] == "revert-to-automatic" for c in cs)
    assert any("revert-to-automatic" in e.note for e in m.revision_log)


def test_revert_to_automatic_unknown_key_raises(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    with pytest.raises(ValueError, match="no accepted correction"):
        C.revert_to_automatic(m, mp, "opening:S-W1:width_m")


def test_revert_to_automatic_after_an_undo_uses_the_earliest_active(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    R.decide(m, rid, "edit", "width_m=1.1", corrections_path=mp)
    C.undo(m, mp, 1)  # the second edit is gone; the field shows 0.9
    r = C.revert_to_automatic(m, mp, _key(m))
    assert r["to"] == 1.2 and _op(m)[1].width_m == 1.2
    assert _doc(tmp_path)["corrections"][1]["undone"] is True  # the already-undone one stays


# --- history survives later stages; the export reads it ----------------------


def test_history_survives_and_summary_counts(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    doc = C.load(mp)
    # a later stage re-saving the model leaves the history alone
    mp.write_text(m.to_json())
    assert C.load(mp) == doc
    assert C.summary(doc) == {"total": 1, "undone": 0, "human": 1, "auto": 0}


def test_export_package_copies_the_history_and_reports_it(tmp_path):
    import export_package as EP
    from building_model import Level
    from validate.types import ValidationReport

    m = _model()
    m.levels.append(Level(id="L1", wall_height_m=3.0))
    mp = tmp_path / "model.json"
    R.decide(m, m.review_queue[0].id, "edit", "width_m=0.9", corrections_path=mp)
    report = ValidationReport(building_name="snap997")
    pkg = EP.build_package(tmp_path, m, report, {}, [], pdf=False, review_dir=None)
    assert (pkg / "corrections.json").exists()
    tr = json.loads((pkg / "trust_report.json").read_text())
    assert tr["corrections"]["recorded"] is True
    assert tr["corrections"]["summary"]["total"] == 1
    html = (pkg / "trust_report.html").read_text()
    assert "corrections.json" in html and "1 accepted" in html


def test_export_package_without_a_history_says_so(tmp_path):
    import export_package as EP
    from building_model import Level
    from validate.types import ValidationReport

    m = _model()
    m.levels.append(Level(id="L1", wall_height_m=3.0))
    pkg = EP.build_package(
        tmp_path, m, ValidationReport(building_name="snap997"), {}, [], pdf=False, review_dir=None
    )
    tr = json.loads((pkg / "trust_report.json").read_text())
    assert tr["corrections"]["recorded"] is False
    assert (pkg / "corrections.json").exists() is False


# --- CLI ---------------------------------------------------------------------


def _args(**kw):
    import argparse

    return argparse.Namespace(**kw)


def test_cli_undo_and_listing(tmp_path, capsys):
    import run_review

    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    mp.write_text(m.to_json())
    run_review.main(
        _args(
            model=str(mp),
            confirm=None,
            reject=None,
            show_all=False,
            auto_triage=False,
            list=False,
            format="text",
            apply=None,
            out=None,
            report=None,
            undo=1,
            revert_to_auto=None,
            corrections=False,
        )
    )
    out = capsys.readouterr().out
    assert "Undid [1] edit" in out and "width_m back to 1.2" in out
    assert _op(_model_read(mp))[1].width_m == 1.2
    run_review.main(
        _args(
            model=str(mp),
            confirm=None,
            reject=None,
            show_all=False,
            auto_triage=False,
            list=False,
            format="text",
            apply=None,
            out=None,
            report=None,
            undo=None,
            revert_to_auto=None,
            corrections=True,
        )
    )
    out = capsys.readouterr().out
    assert "Correction history: 1 accepted, 1 undone" in out
    assert "edit RVW-001 (human) width_m 1.2 -> 0.9 [undone]" in out


def _model_read(mp):
    return BuildingModel.from_json(mp.read_text())


def test_cli_revert_to_auto(tmp_path, capsys):
    import run_review

    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    mp.write_text(m.to_json())
    run_review.main(
        _args(
            model=str(mp),
            confirm=None,
            reject=None,
            show_all=False,
            auto_triage=False,
            list=False,
            format="text",
            apply=None,
            out=None,
            report=None,
            undo=None,
            revert_to_auto=_key(m),
            corrections=False,
        )
    )
    out = capsys.readouterr().out
    assert "Reverted opening:S-W1:width_m to automatic output" in out
    assert _op(_model_read(mp))[1].width_m == 1.2


def test_cli_rejects_combined_modes(tmp_path, capsys):
    import run_review

    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(SystemExit):
        run_review.main(
            _args(
                model=str(mp),
                confirm="RVW-001",
                reject=None,
                show_all=False,
                auto_triage=False,
                list=False,
                format="text",
                apply=None,
                out=None,
                report=None,
                undo=1,
                revert_to_auto=None,
                corrections=False,
            )
        )


def test_undo_with_a_stale_target_aborts_before_any_change(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    rid = m.review_queue[0].id
    R.decide(m, rid, "edit", "width_m=0.9", corrections_path=mp)
    # a later correction on a second opening that has since been deleted
    m.spaces["L1-101"].openings.append(
        SpaceOpening(id="S-W2", tag="", category="window", width_m=1.0, height_m=1.5, area_m2=1.5)
    )  # fmt: skip
    prov = Provenance(sheet_id="A-202", revision=1, method="elevation", confidence=0.6)
    m.flag_for_review("window_room_link", "window W2 -> room 101", 0.6, prov,
                      target={"kind": "opening", "id": "S-W2", "field": "width_m"})  # fmt: skip
    rid2 = m.review_queue[1].id
    R.decide(m, rid2, "edit", "width_m=0.8", corrections_path=mp)
    m.spaces["L1-101"].openings = [o for o in m.spaces["L1-101"].openings if o.id != "S-W2"]
    before_json = m.to_json()
    with pytest.raises(ValueError, match="no longer resolves"):
        C.undo(m, mp, 2)
    assert m.to_json() == before_json  # nothing changed
    # the stale entry blocks the stack until a revert-to-automatic clears it
    r = C.revert_to_automatic(m, mp, "opening:S-W2:width_m")
    assert r["entity_gone"] is True and r["to"] is None and r["reverted"] == 1
    assert len(C.undo(m, mp, 1)) == 1
    assert _op(m)[1].width_m == 1.2
