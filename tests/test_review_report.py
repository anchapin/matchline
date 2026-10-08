"""HTML review report and replayable decisions (#749), on a synthetic snapshot."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
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


# -- #749 sheet overlays ----------------------------------------------------


def _set_run(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_t_real_set", Path(__file__).with_name("test_real_set.py")
    )
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)

    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    model, _rep = T._one_floor(tmp_path, rows)
    return model, tmp_path / "out"


def test_overlays_draw_rooms_walls_and_openings_with_model_ids(tmp_path):
    model, out = _set_run(tmp_path)
    (sh,) = R.sheet_overlays(out, model)
    assert sh["file"] == "sheet_001.json" and sh["number"] == "A-101"
    assert sh["img"].startswith("data:image/jpeg;base64,")
    assert {r["id"] for r in sh["rooms"]} == set(model.spaces)
    assert all(r["space"] for r in sh["rooms"])
    assert sh["walls"] and all(0 <= w["a"][0] <= sh["w"] for w in sh["walls"])
    modelled = {op.id for s in model.spaces.values() for op in s.openings}
    states = {o["id"]: o["state"] for o in sh["openings"]}
    assert modelled and all(states[i] == "modelled" for i in modelled)


def test_overlays_read_detections_in_sheet_points(tmp_path):
    model, out = _set_run(tmp_path)
    d = out / "sheets"
    px = json.loads((d / "sheet_001.json").read_text())["px_per_pt"]
    det = [
        {"label": "window", "tag": "W1", "score": 0.8, "bbox": [10 * px, 20 * px, 30 * px, 40 * px]}
    ]
    (d / "detections_001.json").write_text(json.dumps(det))
    (sh,) = R.sheet_overlays(out, model)
    assert sh["dets"] == [
        {"id": "sheet_001.json#0", "label": "window", "tag": "W1", "score": 0.8,
         "box": [10.0, 20.0, 30.0, 40.0]}
    ]  # fmt: skip


def test_items_link_to_their_room_and_to_their_detection_box(tmp_path):
    model, out = _set_run(tmp_path)
    sid = sorted(model.spaces)[0]
    nowhere = Provenance(sheet_id="", revision=0, method="m", confidence=0.5)
    model.flag_for_review(
        "space_merge", f"check {sid}", 0.5, nowhere, target={"kind": "space", "id": sid}
    )
    px = json.loads((out / "sheets" / "sheet_001.json").read_text())["px_per_pt"]
    boxed = Provenance(sheet_id="A-101", revision=0, method="m", confidence=0.4,
                       bbox=[0, 0, 50 * px, 25 * px])  # fmt: skip
    model.flag_for_review("fixture_assignment", "a fixture", 0.4, boxed)
    links = R.item_links(model, R.sheet_overlays(out, model))
    by_kind = {i.kind: links.get(i.id) for i in model.review_queue}
    assert by_kind["space_merge"] == {"sheet": "sheet_001.json", "el": f"room:{sid}"}
    assert by_kind["fixture_assignment"] == {
        "sheet": "sheet_001.json",
        "box": [0.0, 0.0, 50.0, 25.0],
    }


def test_review_page_embeds_sheets_and_says_when_there_are_none(tmp_path):
    model, out = _set_run(tmp_path)
    page = (R.write_review(out, model) / "review.html").read_text()
    assert "<figure id='sheet-sheet_001.json'>" in page and "drawSheet" in page
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    bare = R.render_html(_with_openings(), R.template(_with_openings(), "x"))
    assert "No plan sheets in this run" in bare


# -- #801 elevation sheets and two-sheet items -------------------------------


def _elev_mod():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_t_elevation_sheets", Path(__file__).with_name("test_elevation_sheets.py")
    )
    TE = importlib.util.module_from_spec(spec)
    saved = list(sys.path)  # it puts tests/ first, where tests/cli would shadow cli.py
    try:
        spec.loader.exec_module(TE)
    finally:
        sys.path[:] = saved
    return TE


def _elev_run(tmp_path, **kw):
    TE = _elev_mod()
    model, _rep, _s = TE._run(tmp_path, TE._elev("SOUTH ELEVATION", **kw))
    return model, tmp_path / "out", TE


def test_overlays_draw_the_elevation_and_its_joined_openings(tmp_path):
    model, out, _TE = _elev_run(tmp_path)
    plan, elev = R.sheet_overlays(out, model)
    assert plan["kind"] == "plan" and elev["kind"] == "elevation"
    assert (elev["file"], elev["number"], elev["facade"], elev["plan"]) == (
        "sheet_002.json", "A-201", "south", "A-101",
    )  # fmt: skip
    assert elev["img"].startswith("data:image/jpeg;base64,")
    x0, y0, x1, y1 = elev["outline"]
    assert 0 <= x0 < x1 <= elev["w"] and 0 <= y0 < y1 <= elev["h"]
    (w,) = elev["elev"]
    (op,) = [o for sp in model.spaces.values() for o in sp.openings]
    assert (w["id"], w["kind"], w["plan_opening"]) == ("A-201-W1", "window", op.id)
    assert x0 <= w["box"][0] < w["box"][2] <= x1


def test_a_two_sheet_item_links_both_ends(tmp_path):
    TE = _elev_mod()
    model, out, _ = _elev_run(tmp_path, wins=((TE.WIN_X + TE.T_EXT / 2, 0.9, 1.2, 2.1),))
    (rq,) = [r for r in model.review_queue if r.id.startswith("rq-elev-")]
    (op,) = [o for sp in model.spaces.values() for o in sp.openings]
    ln = R.item_links(model, R.sheet_overlays(out, model))[rq.id]
    assert (ln["sheet"], ln["el"]) == ("sheet_002.json", "elev:A-201-W1")
    ev, pl = ln["ends"]
    assert (ev["side"], ev["number"], ev["el"]) == ("elevation", "A-201", "elev:A-201-W1")
    assert len(ev["box"]) == 4
    assert (pl["side"], pl["sheet"], pl["number"], pl["el"]) == (
        "plan", "sheet_001.json", "A-101", f"op:{op.id}",
    )  # fmt: skip
    assert len(pl["point"]) == 2


def test_one_ended_items_link_to_the_sheet_they_have(tmp_path):
    model, out, _ = _elev_run(tmp_path, wins=((2.0, 0.9, 1.2, 1.5),))
    (op,) = [o for sp in model.spaces.values() for o in sp.openings]
    nowhere = Provenance(sheet_id="", revision=0, method="m", confidence=0.5)
    model.flag_for_review(
        "elevation_extraction", "whole sheet", 0.5, nowhere, target={"kind": "sheet", "id": "A-201"}
    )
    links = R.item_links(model, R.sheet_overlays(out, model))
    ev_only = links["rq-elev-A-201-A-201-W1"]
    assert (ev_only["sheet"], ev_only["el"]) == ("sheet_002.json", "elev:A-201-W1")
    assert "ends" not in ev_only
    plan_only = links[f"rq-elev-A-201-{op.id}"]
    assert (plan_only["sheet"], plan_only["el"]) == ("sheet_001.json", f"op:{op.id}")
    sheet_item = next(i.id for i in model.review_queue if i.description == "whole sheet")
    assert links[sheet_item]["sheet"] == "sheet_002.json" and "el" not in links[sheet_item]


def test_review_page_draws_the_elevation_offline(tmp_path):
    model, out, _ = _elev_run(tmp_path)
    page = (R.write_review(out, model) / "review.html").read_text()
    assert "<figure id='sheet-sheet_002.json'>" in page
    assert "A-201 sheet_002.json south elevation joined to plan A-101" in page
    assert "data-layer=outline" in page and "id=pair" in page and "drawElevation" in page
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["_SHEET_JS", "_UI_JS"])
def test_sheet_script_parses(name):
    r = subprocess.run(["node", "-e", "new Function(process.argv[1])", getattr(R, name)],
                       capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0, r.stderr


# -- #798: a review edit adds the opening an unsized gap left out -------------


def _gap_run(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_t_real_set_798", Path(__file__).with_name("test_real_set.py")
    )
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    # two 4'-0" rows that disagree on height: the gap stays unsized
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W3", "4'-0\"", "7'-0\"", "FIXED")]
    model, _rep = T._one_floor(tmp_path, rows)
    (rq,) = [r for r in model.review_queue if r.kind == "opening_unsized"]
    return model, rq


def _added(m, rq):
    return R._find_opening(m, rq.target["gap"]["opening_id"])


def test_gap_edit_with_a_schedule_tag_adds_the_opening_and_revert_removes_it(tmp_path):
    from validate import _Ctx
    from validate.invariants import _check_takeoff_counts_reconcile

    m, rq = _gap_run(tmp_path)
    gap = rq.target["gap"]
    assert R.edit_field(m, rq) == "opening" and gap["candidates"] == ["W1", "W3"]
    assert _added(m, rq)[1] is None
    tpl = R.template(m, "x")
    edit = {"id": rq.id, "action": "edit", "value": "W1"}
    assert R.apply_decisions(m, _doc0(tpl, edit))["edit"] == 1
    sp, op = _added(m, rq)
    assert sp.id == gap["space_id"] and (op.tag, op.category) == ("W1", "window")
    assert op.width_m == pytest.approx(48 * 0.0254) and op.height_m == pytest.approx(1.524)
    assert op.area_m2 == pytest.approx(op.width_m * op.height_m)
    assert op.s_center_m == gap["s_center_m"]
    assert sum(op.host_interval_m) / 2 == pytest.approx(gap["s_center_m"], abs=1e-4)
    assert op.provenance.method == "review_edit" and not op.needs_review
    assert any(f"{rq.id} edit: W1 (opening None ->" in e.note for e in m.revision_log)
    r = _check_takeoff_counts_reconcile(_Ctx(model=m))
    assert r.severity == "pass" and "1 window(s) added in review" in r.message
    # replaying the same file again changes nothing
    assert R.apply_decisions(m, _doc0(tpl, edit))["unchanged"] == 1
    # revert, confirm and reject all mean "the pipeline was right to leave it out"
    for act in ("revert", "confirm", "reject"):
        R.apply_decisions(m, _doc0(tpl, edit))
        assert _added(m, rq)[1] is not None
        R.apply_decisions(m, _doc0(tpl, edit, {"id": rq.id, "action": act}))
        assert _added(m, rq)[1] is None, act


def test_gap_edit_with_typed_sizes_adds_an_untagged_opening(tmp_path):
    m, rq = _gap_run(tmp_path)
    v = "category=door width_m=1.2 height_m=2.1"
    R.apply_decisions(m, _doc(m, {"id": rq.id, "action": "edit", "value": v}))
    _sp, op = _added(m, rq)
    assert (op.tag, op.category, op.width_m, op.height_m) == ("", "door", 1.2, 2.1)
    assert op.sill_m is None and op.head_m is None
    (tmp_path / "b").mkdir()
    m2, rq2 = _gap_run(tmp_path / "b")
    v = "category=window width_m=1.0 height_m=1.5 sill_m=0.9"
    R.apply_decisions(m2, _doc(m2, {"id": rq2.id, "action": "edit", "value": v}))
    _sp, op = _added(m2, rq2)
    assert (op.sill_m, op.head_m) == (0.9, 2.4)


@pytest.mark.parametrize(
    "value, why",
    [
        ("W9", "not in the model's schedules"),
        ("category=window width_m=2.0 height_m=1.5", "wider than"),
        ("category=window width_m=1.0", "missing height_m"),
        ("category=skylight width_m=1.0 height_m=1.0", "not one of"),
        ("category=window width_m=1.0 height_m=1.5 depth=2", "unknown key"),
        ("category=window width_m=1.0 height_m=99", "outside"),
    ],
)
def test_bad_gap_edits_refuse_the_file_and_change_nothing(tmp_path, value, why):
    m, rq = _gap_run(tmp_path)
    before = m.to_json()
    with pytest.raises(ValueError, match=why):
        R.apply_decisions(m, _doc(m, {"id": rq.id, "action": "edit", "value": value}))
    assert m.to_json() == before


def test_gap_items_are_editable_on_the_page_and_tolerance_matches_the_pipeline(tmp_path):
    import real_set

    m, rq = _gap_run(tmp_path)
    page = R.render_html(m, R.template(m, "x"))
    assert '"field": "opening"' in page and "Schedule tag (W1, W3) or category=window" in page
    assert R.GAP_WIDTH_TOL_M == real_set.WIDTH_TOL_M
    # an old model whose gap item has no "gap" record still records only
    rq.target = {"kind": "wall", "id": rq.target["id"]}
    assert R.edit_field(m, rq) == ""
