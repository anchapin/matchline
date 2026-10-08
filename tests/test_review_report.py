"""HTML review report and replayable decisions (#749), on a synthetic snapshot."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import review_report as R
import run_review
from building_model import BuildingModel, Provenance, ReviewItem


def _model() -> BuildingModel:
    m = BuildingModel(name="snap")
    prov = Provenance(
        sheet_id="A-101", revision=2, method="plan_gap_schedule_width", confidence=0.7
    )
    rows = [
        ("R1", "window_room_link", 0.70, 1, "Window W1 in Room 101"),
        ("R2", "opening_attachment", 0.40, 1, "Door D7 </script><b>x</b> width unmatched"),
        ("R3", "space_merge", 0.55, 3, "Closet 104 merged into 103"),
        ("R4", "facade_unclear", 0.90, 0, "East facade glazing ratio"),
    ]
    for iid, kind, conf, urg, desc in rows:
        m.review_queue.append(
            ReviewItem(id=iid, kind=kind, description=desc, confidence=conf, provenance=prov,
                       urgency=urg)
        )  # fmt: skip
    m.review_queue[3].status = "confirmed"  # auto-resolved by triage
    m.review_queue[3].resolution = "accept"
    m.review_queue[3].auto_resolved = True
    return m


def _doc(m, *decisions):
    d = R.template(m, R.sha256_text(m.to_json()))
    d["decisions"] = [dict(x, at="2026-10-08T12:00:00+00:00") for x in decisions]
    return d


def test_queue_is_worst_first():
    order = [i.id for i in R.worst_first(_model().review_queue)]
    # open before resolved, then urgency high to low, then confidence low to high
    assert order == ["R3", "R2", "R1", "R4"]


def test_apply_confirm_reject_edit_and_log_history():
    m = _model()
    doc = _doc(
        m,
        {"id": "R1", "action": "confirm"},
        {"id": "R2", "action": "reject"},
        {"id": "R3", "action": "edit", "value": "merge into 102, not 103"},
    )
    s = R.apply_decisions(m, doc, R.sha256_text(m.to_json()))
    q = {i.id: i for i in m.review_queue}
    assert (q["R1"].status, q["R1"].needs_review, q["R1"].acknowledged) == (
        "confirmed",
        False,
        True,
    )
    assert (q["R2"].status, q["R2"].resolution) == ("rejected", "drop")
    assert (q["R3"].status, q["R3"].resolution) == ("confirmed", "reassign")
    assert (s["confirm"], s["reject"], s["edit"], s["warnings"]) == (1, 1, 1, [])
    notes = [e.note for e in m.revision_log if e.action == "review"]
    assert "R3 edit: merge into 102, not 103" in notes and len(notes) == 3
    # replaying the same file again changes and logs nothing
    s2 = R.apply_decisions(m, doc)
    assert s2["unchanged"] == 3 and len([e for e in m.revision_log if e.action == "review"]) == 3


def test_revert_restores_the_automatic_output_and_keeps_history():
    m = _model()
    before = {i.id: R.auto_state(i) for i in m.review_queue}
    doc = _doc(
        m,
        {"id": "R4", "action": "reject"},
        {"id": "R1", "action": "confirm"},
        {"id": "R4", "action": "revert"},
        {"id": "R1", "action": "revert"},
    )
    R.apply_decisions(m, doc)
    assert {i.id: R.auto_state(i) for i in m.review_queue} == before
    assert len(doc["decisions"]) == 4  # the file keeps every correction


def test_bad_decisions_are_refused_before_anything_changes():
    m = _model()
    for bad, msg in [
        ({"id": "R9", "action": "confirm"}, "no review item"),
        ({"id": "R1", "action": "approve"}, "unknown action"),
        ({"id": "R1", "action": "edit", "value": " "}, "has no value"),
    ]:
        with pytest.raises(ValueError, match=msg):
            R.apply_decisions(m, _doc(m, {"id": "R2", "action": "reject"}, bad))
        assert m.review_queue[1].status == "open"
    with pytest.raises(ValueError, match="not a decisions file"):
        R.apply_decisions(m, {"schema": "x", "decisions": []})


def test_page_is_offline_and_carries_the_queue(tmp_path):
    m = _model()
    d = R.write_review(tmp_path, m)
    page = (d / "review.html").read_text()
    assert not re.search(r'(src|href)\s*=\s*["\']?(https?:)?//', page)  # no external assets
    assert "</script><b>" not in page  # item text cannot break out of the data block
    data = json.loads(re.search(r'id="data">(.*?)</script>', page, re.S).group(1))
    assert [i["id"] for i in data["items"]] == ["R3", "R2", "R1", "R4"]
    doc = json.loads((d / "decisions.json").read_text())
    assert doc["model_sha256"] == R.sha256_text((d / "model.json").read_text())
    assert set(doc["auto"]) == {"R1", "R2", "R3", "R4"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_replay_matches_the_cli(tmp_path):
    m = _model()
    page = R.render_html(m, R.template(m, "x"))
    js = re.search(r"<script id=replay>(.*?)</script>", page, re.S).group(1)
    (tmp_path / "replay.js").write_text(js)
    doc = _doc(
        m,
        {"id": "R1", "action": "confirm"},
        {"id": "R2", "action": "edit", "value": "0.9 m"},
        {"id": "R1", "action": "revert"},
        {"id": "R3", "action": "reject"},
    )
    out = subprocess.run(
        ["node", "-e", "const r=require(process.argv[1]);"
         "process.stdout.write(JSON.stringify(r.finalStates(JSON.parse(process.argv[2]))))",
         str(tmp_path / "replay.js"), json.dumps(doc)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert json.loads(out.stdout) == R.final_states(doc)


def test_cli_report_then_apply_round_trip(tmp_path, capsys):
    path = tmp_path / "model.json"
    path.write_text(_model().to_json())
    base = dict(confirm=None, reject=None, show_all=False, auto_triage=False, list=False,
                format="text", apply=None, out=None, report=None)  # fmt: skip
    run_review.main(SimpleNamespace(**dict(base, model=str(path), report=str(tmp_path))))
    d = tmp_path / "review"
    doc = json.loads((d / "decisions.json").read_text())
    doc["decisions"] = [{"id": "R2", "action": "reject"}, {"id": "R3", "action": "confirm"}]
    (tmp_path / "decisions.json").write_text(json.dumps(doc))
    out = tmp_path / "reviewed.json"
    run_review.main(
        SimpleNamespace(
            **dict(base, model=str(d / "model.json"), apply=str(tmp_path / "decisions.json"),
                   out=str(out))
        )
    )  # fmt: skip
    printed = capsys.readouterr()
    assert "2 rejected" not in printed.out and "1 rejected" in printed.out
    assert "Warning" not in printed.err  # same model the template was made for
    q = {i.id: i.status for i in BuildingModel.from_json(out.read_text()).review_queue}
    assert q == {"R1": "open", "R2": "rejected", "R3": "confirmed", "R4": "confirmed"}
    assert (d / "model.json").read_text() == _model().to_json()  # --out leaves the input alone


def test_cli_flags_match_the_report(tmp_path):
    # --confirm/--reject leave an item exactly as the report's decision would
    flag, page = _model(), _model()
    run_review._confirm_item(flag, "R1")
    run_review._reject_item(flag, "R2")
    R.apply_decisions(page, _doc(page, {"id": "R1", "action": "confirm"},
                                 {"id": "R2", "action": "reject"}))  # fmt: skip
    for a, b in zip(flag.review_queue, page.review_queue):
        assert R.auto_state(a) == R.auto_state(b)
    assert [e.note for e in flag.revision_log] == [e.note for e in page.revision_log]


def test_cli_flags_can_change_a_decision_like_the_report():
    m = _model()
    run_review._confirm_item(m, "R1")
    _, msg = run_review._confirm_item(m, "R1")
    assert "nothing changed" in msg and len(m.revision_log) == 1
    run_review._reject_item(m, "R1")
    assert (m.review_queue[0].status, m.review_queue[0].resolution) == ("rejected", "drop")
    with pytest.raises(ValueError, match="not found"):
        run_review._confirm_item(m, "R9")


# -- #796: edits change the model they correct -------------------------------


def _with_openings():
    from building_model import Construction, Space, SpaceOpening

    m = BuildingModel(name="snap796")
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
    m.flag_for_review("space_merge", "closet kept", 0.6, prov,
                      target={"kind": "space", "id": "L1-102"})  # fmt: skip
    return m


def _op(m):
    return R._find_opening(m, "S-W1")


def _doc0(tpl, *decisions):
    # decisions against the automatic snapshot taken when the run wrote the page
    return dict(tpl, decisions=[dict(x, at="2026-10-08T12:00:00+00:00") for x in decisions])


def test_edit_room_link_and_width_change_the_model_and_revert_restores():
    m = _with_openings()
    tpl = R.template(m, "x")
    rid = m.review_queue[0].id
    room = {"id": rid, "action": "edit", "value": "102"}
    s = R.apply_decisions(m, _doc0(tpl, room))
    sp, op = _op(m)
    assert s["edit"] == 1 and sp.id == "L1-102" and not m.spaces["L1-101"].openings
    R.apply_decisions(m, _doc0(tpl, room, {"id": rid, "action": "edit", "value": "width_m=0.9"}))
    sp, op = _op(m)
    assert op.width_m == 0.9 and op.area_m2 == pytest.approx(1.35)
    assert op.host_interval_m == [2.55, 3.45]
    notes = [e.note for e in m.revision_log]
    assert any("space_id 'L1-101' -> 'L1-102'" in n for n in notes)
    assert any("width_m 1.2 -> 0.9" in n for n in notes)
    # revert restores the automatic output of the field the item edits
    R.apply_decisions(m, _doc0(tpl, room, {"id": rid, "action": "revert"}))
    assert _op(m)[0].id == "L1-101"
    assert m.review_queue[0].status == "open"


def test_bad_edit_value_changes_nothing():
    m = _with_openings()
    rid = m.review_queue[0].id
    before = m.to_json()
    with pytest.raises(ValueError, match="not a space id or room number"):
        R.apply_decisions(m, _doc(m, {"id": rid, "action": "edit", "value": "999"}))
    with pytest.raises(ValueError, match="outside"):
        R.apply_decisions(m, _doc(m, {"id": rid, "action": "edit", "value": "width_m=-1"}))
    assert m.to_json() == before


def test_edit_without_a_model_field_records_only_and_page_says_so():
    m = _with_openings()
    rid = m.review_queue[1].id
    before = [sp.openings[:] for sp in m.spaces.values()]
    R.apply_decisions(m, _doc(m, {"id": rid, "action": "edit", "value": "merge into 101"}))
    assert [sp.openings for sp in m.spaces.values()] == before
    assert m.review_queue[1].status == "confirmed"
    page = R.render_html(m, R.template(m, "x"))
    assert '"field": "space_id"' in page and '"field": ""' in page


def test_cli_confirm_after_an_edit_restores_the_automatic_value_like_the_report():
    m = _with_openings()
    rid = m.review_queue[0].id
    R.apply_decisions(m, _doc(m, {"id": rid, "action": "edit", "value": "L1-102"}))
    assert _op(m)[0].id == "L1-102"
    run_review._confirm_item(m, rid)
    assert _op(m)[0].id == "L1-101" and m.review_queue[0].status == "confirmed"


def test_target_survives_json_and_old_models_load():
    m = _with_openings()
    back = BuildingModel.from_json(m.to_json())
    assert back.review_queue[0].target == {"kind": "opening", "id": "S-W1", "field": "space_id"}
    d = json.loads(m.to_json())
    for it in d["model"]["review_queue"]:
        it.pop("target")
    assert BuildingModel.from_dict(d).review_queue[0].target == {}
