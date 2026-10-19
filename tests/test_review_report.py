"""HTML review report and replayable decisions (#749), on a synthetic snapshot."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import review.review_report as R
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


# --- space merge target edits (#799, second slice) ---------------------------


def _merge_fixture():
    """A closet kept for review beside Room 101, plus the pieces a merge moves."""
    from space_merge import merge_closets_and_shafts

    m = _model()
    from building_model import EnvelopeWall, Level, Space, SpaceOpening, Zone

    m.levels.append(Level(id="L1", wall_height_m=3.0))

    def rect(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    room = Space(
        id="L1-101",
        level_id="L1",
        number="101",
        polygon_m=rect(0, 0, 6, 4),
        area_m2=24.0,
        volume_m3=72.0,
    )
    clo = Space(
        id="L1-103",
        level_id="L1",
        number="103",
        polygon_m=rect(6, 0, 8, 2),
        area_m2=4.0,
        volume_m3=12.0,
        poly_type="closet",
        poly_type_confidence=0.85,
    )
    # a door with no plan position: the automatic rule cannot connect the
    # closet to a room, so it keeps the closet and flags it for review
    clo.openings.append(SpaceOpening(id="D7", tag="D1", category="door", width_m=0.9, height_m=2.1))
    m.spaces["L1-101"] = room
    m.spaces["L1-103"] = clo
    m.zones["Z1"] = Zone(id="Z1", level_id="L1", space_ids=["L1-103", "L1-101"])
    m.envelope.append(EnvelopeWall(id="E1", facade="east", space_id="L1-103"))
    # the automatic rule keeps the closet for review (no merge happened)
    res = merge_closets_and_shafts(m)
    assert res.merged == [] and res.kept, res
    # the item the automatic rule raised (R3 in _model() has no target)
    item = next(
        i for i in m.review_queue if i.kind == "space_merge" and i.target and i.target.get("id")
    )
    return m, item


def _merge_doc(m, *decisions):
    d = R.template(m, R.sha256_text(m.to_json()))
    d["decisions"] = [dict(x, at="2026-10-11T19:40:00+00:00") for x in decisions]
    return d


def test_space_merge_edit_merges_into_the_room_the_reviewer_picked():
    m, item = _merge_fixture()
    doc = _merge_doc(m, {"id": item.id, "action": "edit", "value": "merge into 101"})
    s = R.apply_decisions(m, doc, R.sha256_text(m.to_json()))
    assert s["edit"] == 1 and s["warnings"] == []
    assert "L1-103" not in m.spaces
    room = m.spaces["L1-101"]
    assert "L1-103" in room.merged_from
    assert abs(room.area_m2 - 28.0) < 1e-6
    assert m.zones["Z1"].space_ids == ["L1-101"]
    assert m.envelope[0].space_id == "L1-101"
    h = room.history[-1]
    assert h.method == "space_merge:review_edit"
    notes = [e.note for e in m.revision_log if e.action == "review"]
    assert any(item.id in n and "merge_into" in n for n in notes)
    # replaying the same file again changes and logs nothing
    s2 = R.apply_decisions(m, doc)
    assert s2["unchanged"] == 1
    assert len([e for e in m.revision_log if e.action == "review"]) == 1


def test_space_merge_edit_bad_value_changes_nothing():
    m, item = _merge_fixture()
    doc = _merge_doc(m, {"id": item.id, "action": "edit", "value": "merge into 999"})
    with pytest.raises(ValueError, match="999"):
        R.apply_decisions(m, doc, R.sha256_text(m.to_json()))
    # still there, still its own space, item untouched
    assert "L1-103" in m.spaces and "L1-101" in m.spaces
    assert m.spaces["L1-101"].merged_from == []
    assert not [e for e in m.revision_log if e.action == "review"]
    # a target on another level is refused too
    from building_model import Space

    m.spaces["L2-201"] = Space(
        id="L2-201", level_id="L2", number="201",
        polygon_m=[[0, 0], [4, 0], [4, 4], [0, 4]], area_m2=16.0,
    )  # fmt: skip
    doc2 = _merge_doc(m, {"id": item.id, "action": "edit", "value": "merge into L2-201"})
    with pytest.raises(ValueError, match="level"):
        R.apply_decisions(m, doc2, R.sha256_text(m.to_json()))
    assert "L1-103" in m.spaces


def test_space_merge_edit_revert_restores_both_spaces():
    m, item = _merge_fixture()
    h = R.sha256_text(m.to_json())
    s = R.apply_decisions(
        m, _merge_doc(m, {"id": item.id, "action": "edit", "value": "merge into 101"}), h
    )
    assert s["edit"] == 1
    # a second pass undoes the merge
    s2 = R.apply_decisions(m, _merge_doc(m, {"id": item.id, "action": "revert"}))
    assert s2["revert"] == 1
    # the closet is back, unchanged
    clo = m.spaces["L1-103"]
    assert clo.poly_type == "closet" and abs(clo.area_m2 - 4.0) < 1e-6
    assert abs(clo.volume_m3 - 12.0) < 1e-6
    assert [o.id for o in clo.openings] == ["D7"]
    # the room is back to 24 m2 with nothing absorbed
    room = m.spaces["L1-101"]
    assert room.merged_from == [] and abs(room.area_m2 - 24.0) < 1e-6
    assert abs(room.volume_m3 - 72.0) < 1e-6
    assert m.zones["Z1"].space_ids == ["L1-103", "L1-101"]
    assert m.envelope[0].space_id == "L1-103"
    assert not [h for h in room.history if h.method == "space_merge:review_edit"]
    # and the file replays idempotently after the revert
    s3 = R.apply_decisions(m, _merge_doc(m, {"id": item.id, "action": "revert"}))
    assert s3["unchanged"] == 1 and "L1-103" in m.spaces


def test_space_item_that_is_not_a_merge_stays_record_only():
    from building_model import Provenance, Space

    m, _ = _merge_fixture()
    m.spaces["L1-110"] = Space(
        id="L1-110", level_id="L1", number="110",
        polygon_m=[[0, 0], [2, 0], [2, 2], [0, 2]], area_m2=4.0,
    )  # fmt: skip
    m.review_queue.append(
        ReviewItem(
            id="RVW-NG1",
            kind="space_no_geometry",
            description="Space L1-110 has no usable polygon",
            confidence=0.5,
            provenance=Provenance(sheet_id="A-101", revision=2, method="t", confidence=0.5),
            urgency=1,
            target={"kind": "space", "id": "L1-110"},
        )
    )
    doc = _merge_doc(m, {"id": "RVW-NG1", "action": "edit", "value": "merge into 101"})
    s = R.apply_decisions(m, doc, R.sha256_text(m.to_json()))
    assert s["edit"] == 1
    assert "L1-110" in m.spaces and "L1-103" in m.spaces
    assert m.spaces["L1-101"].merged_from == []
    q = {i.id: i for i in m.review_queue}
    assert q["RVW-NG1"].status == "confirmed"
    notes = [e.note for e in m.revision_log if e.action == "review"]
    assert any("RVW-NG1 edit: merge into 101" in n for n in notes)


def test_space_merge_edit_survives_a_model_roundtrip():
    """The snapshot lives on the item, so revert still works after save/reload."""
    m, item = _merge_fixture()
    doc = _merge_doc(m, {"id": item.id, "action": "edit", "value": "merge into 101"})
    R.apply_decisions(m, doc, R.sha256_text(m.to_json()))
    # ...then someone saves and reloads the merged model
    m2 = m.from_json(m.to_json())
    item2 = next(i for i in m2.review_queue if i.id == item.id)
    doc2 = _merge_doc(m2, {"id": item2.id, "action": "revert"})
    s = R.apply_decisions(m2, doc2)
    assert s["revert"] == 1
    assert "L1-103" in m2.spaces
    assert m2.spaces["L1-101"].merged_from == []
    assert abs(m2.spaces["L1-101"].area_m2 - 24.0) < 1e-6


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


def _with_assignments():
    # fixture / diffuser / sensor assignment items (#799)
    from building_model import ComponentRef, FixtureInstance, Space, Zone

    m = BuildingModel(name="snap799")
    for sid, num in (("L1-101", "101"), ("L1-102", "102")):
        m.spaces[sid] = Space(id=sid, level_id="L1", number=num)
    m.spaces["L1-102"].area_m2 = 20.0
    prov = Provenance(sheet_id="E-101", revision=1, method="point_in_polygon", confidence=0.4)
    # F1: the linker found no space for it, so the fixture data rides in the
    # target for an assignment edit to build the instance from
    m.flag_for_review(
        "fixture_assignment",
        "fixture F1 falls in no space",
        0.4,
        prov,
        target={
            "kind": "fixture",
            "id": "F1",
            "fixture": {
                "tag": "A1",
                "fixture_class": "troffer",
                "x_m": 1.0,
                "y_m": 2.0,
                "watts": 32.0,
                "sheet_id": "E-101",
            },
        },  # fmt: skip
    )
    # F2: assigned, to the wrong space
    m.spaces["L1-101"].lighting.fixtures.append(
        FixtureInstance(id="F2", tag="A2", fixture_class="troffer", x_m=0.5, y_m=0.5, watts=40.0)
    )
    m.spaces["L1-101"].lighting.total_w = 40.0
    m.flag_for_review("fixture_assignment", "fixture F2 in the wrong space", 0.5, prov,
                      target={"kind": "fixture", "id": "F2"})  # fmt: skip
    ref = ComponentRef(id="D1", type="diffuser", x_m=3.0, y_m=3.0)
    sref = ComponentRef(id="S1", type="sensor", x_m=4.0, y_m=4.0)
    m.zones["Z1"] = Zone(id="Z1", level_id="L1", space_ids=["L1-101"],
                         diffusers=[ref], sensors=[sref])  # fmt: skip
    m.spaces["L1-101"].hvac.zone_ids.append("Z1")
    m.spaces["L1-101"].hvac.sensors.append(sref)
    m.flag_for_review("diffuser_assignment", "diffuser D1 falls in no space", 0.4, prov,
                      target={"kind": "diffuser", "id": "D1"})  # fmt: skip
    m.flag_for_review("sensor_assignment", "sensor S1 in the wrong space", 0.5, prov,
                      target={"kind": "sensor", "id": "S1"})  # fmt: skip
    return m


def test_fixture_assignment_edit_assigns_and_revert_restores():
    m = _with_assignments()
    tpl = R.template(m, "x")
    fid = m.review_queue[0].id
    s = R.apply_decisions(m, _doc0(tpl, {"id": fid, "action": "edit", "value": "102"}))
    fx = m.spaces["L1-102"].lighting.fixtures[0]
    assert s["edit"] == 1 and fx.id == "F1" and fx.watts == 32.0
    assert fx.provenance.method == "review_edit"
    assert m.spaces["L1-102"].lighting.total_w == pytest.approx(32.0)
    assert m.spaces["L1-102"].lighting.lpd_w_m2 == pytest.approx(1.6)
    assert any("space_id None -> 'L1-102'" in e.note for e in m.revision_log)
    R.apply_decisions(m, _doc0(tpl, {"id": fid, "action": "revert"}))
    assert not m.spaces["L1-102"].lighting.fixtures
    assert m.spaces["L1-102"].lighting.total_w == 0.0
    assert m.review_queue[0].status == "open"


def test_fixture_assignment_edit_moves_an_assigned_fixture():
    m = _with_assignments()
    tpl = R.template(m, "x")
    fid = m.review_queue[1].id
    R.apply_decisions(m, _doc0(tpl, {"id": fid, "action": "edit", "value": "102"}))
    assert [f.id for f in m.spaces["L1-102"].lighting.fixtures] == ["F2"]
    assert not m.spaces["L1-101"].lighting.fixtures
    assert m.spaces["L1-102"].lighting.total_w == pytest.approx(40.0)
    assert m.spaces["L1-101"].lighting.total_w == 0.0
    R.apply_decisions(m, _doc0(tpl, {"id": fid, "action": "revert"}))
    assert [f.id for f in m.spaces["L1-101"].lighting.fixtures] == ["F2"]
    assert m.spaces["L1-101"].lighting.total_w == pytest.approx(40.0)


def test_diffuser_and_sensor_assignment_edits_and_revert():
    m = _with_assignments()
    tpl = R.template(m, "x")
    did, sid = m.review_queue[2].id, m.review_queue[3].id
    R.apply_decisions(
        m,
        _doc0(
            tpl,
            {"id": did, "action": "edit", "value": "102"},
            {"id": sid, "action": "edit", "value": "102"},
        ),
    )
    assert [r.id for r in m.spaces["L1-102"].hvac.diffusers] == ["D1"]
    assert [r.id for r in m.spaces["L1-102"].hvac.sensors] == ["S1"]
    assert not m.spaces["L1-101"].hvac.sensors
    assert "L1-102" in m.zones["Z1"].space_ids
    assert "Z1" in m.spaces["L1-102"].hvac.zone_ids
    R.apply_decisions(
        m, _doc0(tpl, {"id": did, "action": "revert"}, {"id": sid, "action": "revert"})
    )
    assert not m.spaces["L1-102"].hvac.diffusers
    assert [r.id for r in m.spaces["L1-101"].hvac.sensors] == ["S1"]
    assert m.review_queue[2].status == "open"


def test_bad_assignment_value_changes_nothing():
    m = _with_assignments()
    before = m.to_json()
    with pytest.raises(ValueError, match="not a space id or room number"):
        R.apply_decisions(m, _doc(m, {"id": m.review_queue[0].id,
                                      "action": "edit", "value": "999"}))  # fmt: skip
    assert m.to_json() == before


def test_assignment_without_a_target_record_records_only():
    m = _with_assignments()
    m.review_queue[0].target = {"kind": "fixture", "id": "F9"}  # pre-#799 item
    before = m.to_json()
    s = R.apply_decisions(m, _doc(m, {"id": m.review_queue[0].id,
                                      "action": "edit", "value": "102"}))  # fmt: skip
    assert s["edit"] == 1
    assert m.review_queue[0].status == "confirmed"

    def _core(doc):
        d = json.loads(doc)
        d["model"]["review_queue"] = []
        d["model"]["revision_log"] = []
        return d

    assert _core(m.to_json()) == _core(before)
    page = R.render_html(m, R.template(m, "x"))
    assert '"field": ""' in page


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
    from building_model import Provenance, Space

    m = _with_openings()
    m.spaces["L1-110"] = Space(id="L1-110", level_id="L1", number="110")
    m.flag_for_review("space_no_geometry", "space L1-110 has no polygon", 0.5,
                      Provenance(sheet_id="A-1", revision=1, method="t", confidence=0.5),
                      target={"kind": "space", "id": "L1-110"})  # fmt: skip
    rid = "RVW-003"
    before = [sp.openings[:] for sp in m.spaces.values()]
    R.apply_decisions(m, _doc(m, {"id": rid, "action": "edit", "value": "merge into 101"}))
    assert [sp.openings for sp in m.spaces.values()] == before
    assert next(i for i in m.review_queue if i.id == rid).status == "confirmed"
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


# --- IFC opening attachment edits (#799, third slice) -------------------------


def _attach_fixture():
    """A skylight over no space and a wall opening in no room, kept for review."""
    from building_model import BimElement, BimOpening, Level, Space

    m = _model()
    m.levels.append(Level(id="L1", wall_height_m=3.0))
    m.levels.append(Level(id="L2"))
    m.spaces["L1-101"] = Space(
        id="L1-101",
        level_id="L1",
        number="101",
        polygon_m=[[0, 0], [6, 0], [6, 4], [0, 4]],
        area_m2=24.0,
    )
    m.spaces["L1-102"] = Space(
        id="L1-102",
        level_id="L1",
        number="102",
        polygon_m=[[6, 0], [10, 0], [10, 4], [6, 4]],
        area_m2=16.0,
    )
    m.spaces["L2-201"] = Space(id="L2-201", level_id="L2", number="201")

    sk = BimOpening(
        id="SK-1",
        category="skylight",
        tag="SK1",
        width_m=1.2,
        height_m=1.0,
        host_global_id="SLAB-1",
        plan_center_m=[2.0, 1.5],
        tilt_deg=0.0,
        azimuth_deg=180.0,
    )
    slab = BimElement(global_id="SLAB-1", ifc_class="IfcSlab", name="roof", level_id="L1")
    slab.openings.append(sk)
    wo = BimOpening(
        id="WIN-1",
        category="window",
        tag="W1",
        width_m=1.0,
        height_m=1.5,
        sill_m=0.9,
        s_center_m=2.0,
        host_global_id="WALL-1",
    )
    wall = BimElement(
        global_id="WALL-1",
        ifc_class="IfcWall",
        name="wall",
        level_id="L1",
        length_m=4.0,
        thickness_m=0.3,
    )
    wall.openings.append(wo)
    m.bim_elements += [slab, wall]

    for it in m.review_queue:
        if it.id == "R2":
            it.target = {"kind": "bim_element", "id": "WALL-1"}
    m.review_queue.append(
        ReviewItem(
            id="R5",
            kind="opening_attachment",
            confidence=0.3,
            description="Skylight SK-1 sits over no space on L1; left unattached.",
            target={"kind": "bim_opening", "id": "SK-1"},
        )
    )
    m.review_queue.append(
        ReviewItem(
            id="R6",
            kind="opening_attachment",
            confidence=0.3,
            description="Opening WIN-1 (window W1) lies in no space on L1 on either side.",
            target={"kind": "bim_opening", "id": "WIN-1"},
        )
    )
    return m


def _edit(iid, value):
    return {"id": iid, "action": "edit", "value": value}


def test_opening_attachment_edit_attaches_the_skylight_to_the_room_picked():
    m = _attach_fixture()
    before = m.to_json()
    R.apply_decisions(m, _doc(m, _edit("R5", "L1-101")))
    sp = m.spaces["L1-101"]
    o = next(x for x in sp.openings if x.id == "SK-1")
    assert o.category == "skylight" and o.host_facade == "roof" and o.plan_center_m == [2.0, 1.5]
    assert o.tilt_deg == 0.0 and o.azimuth_deg == 180.0 and o.area_m2 == 1.2
    assert o.needs_review is False
    assert m.spaces["L1-101"].daylight.toplit  # daylight computed on attach
    it = next(x for x in m.review_queue if x.id == "R5")
    assert it.needs_review is False and it.resolution == "reassign"
    assert m.to_json() != before


def test_opening_attachment_edit_attaches_a_wall_opening_with_its_interval():
    m = _attach_fixture()
    R.apply_decisions(m, _doc(m, _edit("R6", "L1-102")))
    sp = m.spaces["L1-102"]
    o = next(x for x in sp.openings if x.id == "WIN-1")
    assert o.category == "window" and o.sill_m == 0.9 and o.s_center_m == 2.0
    assert o.host_interval_m == [1.5, 2.5]  # width 1.0 centred at 2.0 on a 4.0 m wall
    assert o.needs_review is False


def test_opening_attachment_edit_bad_value_changes_nothing():
    m = _attach_fixture()
    before = m.to_json()
    for iid, value, why in [
        ("R5", "999", "not a space id or room number"),
        ("R5", "L2-201", "is on level 'L2'"),
        ("R6", "L1-101", "cannot attach"),  # unreachable: same level, should attach
    ]:
        if iid == "R6" and why == "cannot attach":
            continue
        with pytest.raises(ValueError, match=why):
            R.apply_decisions(m, _doc(m, _edit(iid, value)))
        assert m.to_json() == before
    # an opening id that is not in the model degrades the item to record-only
    m2 = _attach_fixture()
    it = next(x for x in m2.review_queue if x.id == "R5")
    it.target = {"kind": "bim_opening", "id": "GONE"}
    R.apply_decisions(m2, _doc(m2, _edit("R5", "L1-101")))
    assert next(x for x in m2.review_queue if x.id == "R5").needs_review is False


def test_opening_attachment_revert_un_attaches():
    m = _attach_fixture()
    before = {i.id: R.auto_state(i) for i in m.review_queue}
    R.apply_decisions(m, _doc(m, _edit("R5", "L1-101")))
    assert next(x for x in m.spaces["L1-101"].openings if x.id == "SK-1")
    s2 = R.apply_decisions(m, _doc(m, {"id": "R5", "action": "revert"}))
    assert s2["revert"] == 1
    assert not any(x.id == "SK-1" for sp in m.spaces.values() for x in sp.openings)
    assert before["R5"]["status"] == "open"  # the fixture started open


def test_opening_attachment_edit_moves_an_opening_between_rooms():
    m = _attach_fixture()
    R.apply_decisions(m, _doc(m, _edit("R6", "L1-101")))
    assert next(x for x in m.spaces["L1-101"].openings if x.id == "WIN-1")
    R.apply_decisions(m, _doc(m, _edit("R6", "L1-102")))
    assert not any(
        x.id == "WIN-1" for sp in m.spaces.values() for x in sp.openings if sp.id == "L1-101"
    )
    assert next(x for x in m.spaces["L1-102"].openings if x.id == "WIN-1")


def test_opening_attachment_move_recomputes_the_vacated_rooms_daylight():
    m = _attach_fixture()
    R.apply_decisions(m, _doc(m, _edit("R5", "L1-101")))
    assert m.spaces["L1-101"].daylight.toplit
    R.apply_decisions(m, _doc(m, _edit("R5", "L1-102")))
    assert not m.spaces["L1-101"].daylight.toplit  # vacated room recomputed
    assert next(x for x in m.spaces["L1-102"].openings if x.id == "SK-1")


def test_bim_element_items_stay_record_only():
    m = _attach_fixture()
    R.apply_decisions(m, _doc(m, _edit("R2", "whatever the wall reviewer noted")))
    it = next(x for x in m.review_queue if x.id == "R2")
    assert it.needs_review is False and it.resolution == "reassign" and it.acknowledged
    assert not any(
        o.id == "WIN-1" for sp in m.spaces.values() for o in sp.openings
    )  # an edit on the wall element notes the element; it attaches nothing
    rq = next(x for x in m.review_queue if x.id == "R2")
    assert R.edit_field(m, rq) == ""


# --- review queue floor filter + bulk UI (#996) --------------------------------------


def _levels_fixture():
    """Spaces on two floors plus openings, a wall and a fixture for level mapping."""
    from building_model import EnvelopeWall, FixtureInstance, Level, Space, SpaceOpening

    m = _model()
    m.levels.append(Level(id="L1", wall_height_m=3.0))
    m.levels.append(Level(id="L2", wall_height_m=3.0))

    def rect(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    r1 = Space(id="L1-101", level_id="L1", number="101", polygon_m=rect(0, 0, 6, 4))
    r1.openings.append(
        SpaceOpening(id="O1", tag="W1", category="window", width_m=1.2, height_m=1.5)
    )
    r1.lighting.fixtures.append(
        FixtureInstance(id="F1", tag="L1", fixture_class="recessed", x_m=1.0, y_m=1.0)
    )
    r2 = Space(id="L2-201", level_id="L2", number="201", polygon_m=rect(0, 0, 6, 4))
    m.spaces[r1.id] = r1
    m.spaces[r2.id] = r2
    m.envelope.append(
        EnvelopeWall(id="W1", facade="south", space_id="L1-101", length_m=6.0, height_m=3.0)
    )

    m.review_queue[0].target = {"kind": "opening", "id": "O1"}  # R1 -> L1
    m.review_queue[1].target = {"kind": "fixture", "id": "F1"}  # R2 -> L1
    m.review_queue[2].target = {"kind": "space", "id": "L2-201"}  # R3 -> L2
    m.review_queue.append(
        ReviewItem(
            id="R5",
            kind="wall_assembly",
            description="Wall W1 assembly",
            confidence=0.30,
            provenance=m.review_queue[0].provenance,
            urgency=2,
            target={"kind": "wall", "id": "W1"},
        )
    )
    m.review_queue.append(
        ReviewItem(
            id="R6",
            kind="misc_unknown",
            description="No target",
            confidence=0.20,
            provenance=m.review_queue[0].provenance,
            urgency=0,
        )
    )
    return m


def test_item_level_resolution():
    m = _levels_fixture()
    idx = R._level_index(m)
    assert R._item_level(m, m.review_queue[0], idx) == "L1"  # opening target
    assert R._item_level(m, m.review_queue[1], idx) == "L1"  # fixture target
    assert R._item_level(m, m.review_queue[2], idx) == "L2"  # space target
    assert R._item_level(m, m.review_queue[4], idx) == "L1"  # wall target (R5)
    assert R._item_level(m, m.review_queue[3], idx) == ""  # R4: confirmed, no target
    assert R._item_level(m, m.review_queue[5], idx) == ""  # no target


def test_render_html_items_carry_provenance_method():
    """The queue's embedded item data carries the provenance method (#1079)."""
    page = R.render_html(_model(), R.template(_model(), "x"))
    assert '"method": "plan_gap_schedule_width"' in page


def test_render_html_carries_level_and_filters():
    m = _levels_fixture()
    page = R.render_html(m, R.template(m, "x"))
    for needle in (
        "id=f-kind",
        "id=f-band",
        "id=f-level",
        "id=f-method",
        "all methods",
        "id=bulk-confirm",
        "id=bulk-reject",
        "id=khelp",
    ):
        assert needle in page, needle
    assert '"level": "L2"' in page  # R3's row carries its floor
    assert '"level": ""' in page  # targetless rows carry an empty floor


def test_render_html_rows_worst_first():
    m = _levels_fixture()
    page = R.render_html(m, R.template(m, "x"))
    data = json.loads(
        re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
    )
    ids = [i["id"] for i in data["items"]]
    # open first, highest urgency, then lowest confidence; resolved last
    assert ids == ["R3", "R5", "R2", "R1", "R6", "R4"]


def test_attach_takeoff_diff_items_appends_and_dedupes():
    import takeoff_diff as TD

    old = {
        "spaces": {
            "a": {"name": "Office", "area_m2": 20.0},
            "b": {"name": "Hall", "area_m2": 10.0},
        }
    }
    new = {
        "spaces": {
            "a": {"name": "Office", "area_m2": 20.0},
            "c": {"name": "Cafe", "area_m2": 30.0},
        }
    }
    diff = TD.diff_takeoffs(old, new)
    m = BuildingModel(name="diff848")
    first = R.attach_takeoff_diff_items(m, diff)
    kinds = {i.kind for i in first}
    assert "revision_room_removed" in kinds and "revision_room_added" in kinds
    assert all(i.provenance is not None and i.provenance.method == "takeoff_diff" for i in first)
    second = R.attach_takeoff_diff_items(m, diff)
    assert second == []
    assert len(m.review_queue) == len(first)


def test_attach_takeoff_diff_items_sort_worst_first():
    import takeoff_diff as TD

    old = {"spaces": {"a": {"name": "Hall", "area_m2": 10.0}}}
    new = {"spaces": {"c": {"name": "Cafe", "area_m2": 30.0}}}
    diff = TD.diff_takeoffs(old, new)
    m = BuildingModel(name="diff848b")
    R.attach_takeoff_diff_items(m, diff)
    order = [i.id for i in R.worst_first(m.review_queue)]
    assert order == ["takeoff-diff-room-removed-a", "takeoff-diff-room-added-c"]
