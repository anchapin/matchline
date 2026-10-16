"""Tests for in-viewer corrections (#1002): ops applied through the decisions workflow."""

import pytest

import corrections
from building_model import (
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceOpening,
)
from review_viewer import build_scene, page_html
from viewer_corrections import SCHEMA, apply_ops, check


def _model():
    m = BuildingModel(name="Test")
    m.levels = [Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0)]
    a = Space(
        id="L1-101",
        level_id="L1",
        name="OFFICE",
        number="101",
        polygon_m=[[0, 0], [10, 0], [10, 8], [0, 8]],
        label_confidence=0.95,
        poly_type="room",
        poly_type_confidence=0.9,
        openings=[
            SpaceOpening(
                id="w1",
                tag="W1",
                category="window",
                width_m=2.0,
                height_m=1.5,
                sill_m=0.9,
                head_m=2.4,
                host_facade="south",
                host_interval_m=[2.0, 4.0],
                provenance=Provenance("elev_A201", 1, "elevation_window", 0.62),
            )
        ],
    )
    b = Space(
        id="L1-102",
        level_id="L1",
        name="CLOSET",
        number="102",
        polygon_m=[[10, 0], [14, 0], [14, 8], [10, 8]],
        label_confidence=0.5,
    )
    m.spaces = {"L1-101": a, "L1-102": b}
    m.envelope = [
        EnvelopeWall(
            id="e1",
            facade="south",
            from_m=[0, 0],
            to_m=[14, 0],
            height_m=3.0,
            space_id="L1-101",
            provenance=Provenance("A101", 1, "plan_wall", 0.9),
        )
    ]
    return m


def _ops(*ops):
    return {"schema": SCHEMA, "model": "Test", "ops": list(ops)}


def _history(m, tmp_path):
    p = corrections.path_for(tmp_path / "model.json")
    return corrections.read(p)


def test_check_rejects_bad_schema_and_unknown_op():
    with pytest.raises(ValueError):
        check({"schema": "nope", "ops": []})
    with pytest.raises(ValueError):
        check(_ops({"op": "teleport", "id": "L1-101"}))
    with pytest.raises(ValueError):
        check(_ops({"op": "rename_space", "id": "L1-101"}))  # no value
    with pytest.raises(ValueError):
        check(_ops({"op": "revert_to_auto", "key": "not-a-key"}))


def test_rename_space_writes_and_records(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    summary = apply_ops(
        m, _ops({"op": "rename_space", "id": "L1-101", "field": "name", "value": "CONFERENCE"}), mp
    )
    assert m.spaces["L1-101"].name == "CONFERENCE"
    assert summary["edit"] == 1
    item = next(
        i
        for i in m.review_queue
        if i.target.get("id") == "L1-101" and i.target.get("field") == "name"
    )
    assert item.kind == "viewer_correction"
    assert item.status == "confirmed" and item.needs_review is False
    hist = _history(m, tmp_path)["corrections"]
    assert len(hist) == 1
    e = hist[-1]
    assert e["source"] == "human" and e["before"] == "OFFICE" and e["after"] == "CONFERENCE"
    assert e["field"] == "name"


def test_rename_replay_is_idempotent(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    ops = _ops({"op": "rename_space", "id": "L1-101", "field": "name", "value": "CONFERENCE"})
    apply_ops(m, ops, mp)
    n = len(_history(m, tmp_path)["corrections"])
    summary = apply_ops(m, ops, mp)
    assert summary["edit"] == 0 and summary["unchanged"] == 1
    assert len(_history(m, tmp_path)["corrections"]) == n


def test_renumber_space(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(
        m, _ops({"op": "rename_space", "id": "L1-101", "field": "number", "value": "101A"}), mp
    )
    assert m.spaces["L1-101"].number == "101A"


def test_reclassify_door_to_window_and_back(tmp_path):
    m = _model()
    m.spaces["L1-101"].openings[0].category = "door"
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(m, _ops({"op": "reclassify_opening", "id": "w1", "value": "window"}), mp)
    assert m.spaces["L1-101"].openings[0].category == "window"
    apply_ops(m, _ops({"op": "reclassify_opening", "id": "w1", "value": "door"}), mp)
    assert m.spaces["L1-101"].openings[0].category == "door"
    hist = _history(m, tmp_path)["corrections"]
    assert [e["after"] for e in hist] == ["window", "door"]


def test_reclassify_skylight_refused(tmp_path):
    m = _model()
    m.spaces["L1-101"].openings[0].category = "skylight"
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(ValueError, match="skylight"):
        apply_ops(m, _ops({"op": "reclassify_opening", "id": "w1", "value": "window"}), mp)
    assert m.spaces["L1-101"].openings[0].category == "skylight"


def test_merge_spaces_and_revert_to_auto(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(m, _ops({"op": "merge_spaces", "id": "L1-102", "value": "L1-101"}), mp)
    assert "L1-102" not in m.spaces
    assert "L1-102" in m.spaces["L1-101"].merged_from
    item = next(
        i for i in m.review_queue if i.kind == "space_merge" and i.target.get("id") == "L1-102"
    )
    assert item.target.get("merge_state")
    out = corrections.revert_to_automatic(m, mp, "space:L1-102:merge_into")
    assert out["reverted"] >= 1
    assert "L1-102" in m.spaces and "L1-102" not in m.spaces["L1-101"].merged_from


def test_merge_not_adjacent_refused_atomically(tmp_path):
    m = _model()
    m.spaces["L1-102"].polygon_m = [[50, 50], [54, 50], [54, 58], [50, 58]]
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(ValueError):
        apply_ops(m, _ops({"op": "merge_spaces", "id": "L1-102", "value": "L1-101"}), mp)
    assert "L1-102" in m.spaces and "L1-101" in m.spaces


def test_split_space_creates_new_space_and_keeps_opening(tmp_path):
    m = _model()
    del m.spaces["L1-102"]
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(m, _ops({"op": "split_space", "id": "L1-101", "a": [6, 0], "b": [6, 8]}), mp)
    assert "L1-SPLIT-1" in m.spaces
    nsp = m.spaces["L1-SPLIT-1"]
    assert nsp.level_id == "L1" and nsp.area_m2 == pytest.approx(32.0, abs=0.01)
    assert m.spaces["L1-101"].area_m2 == pytest.approx(48.0, abs=0.01)
    # the window at x=3 sits in the kept piece; nothing moved
    assert [o.id for o in m.spaces["L1-101"].openings] == ["w1"]
    assert [o.id for o in nsp.openings] == []


def test_split_moved_opening_follows_its_piece(tmp_path):
    m = _model()
    del m.spaces["L1-102"]
    m.spaces["L1-101"].openings[0].host_interval_m = [7.0, 9.0]  # centre x=8: the new piece
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(m, _ops({"op": "split_space", "id": "L1-101", "a": [6, 0], "b": [6, 8]}), mp)
    assert [o.id for o in m.spaces["L1-SPLIT-1"].openings] == ["w1"]
    assert [o.id for o in m.spaces["L1-101"].openings] == []


def test_split_replay_idempotent_and_revert(tmp_path):
    m = _model()
    del m.spaces["L1-102"]
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    ops = _ops({"op": "split_space", "id": "L1-101", "a": [6, 0], "b": [6, 8]})
    apply_ops(m, ops, mp)
    summary = apply_ops(m, ops, mp)
    assert summary["edit"] == 0 and summary["unchanged"] == 1
    assert len(m.spaces) == 2  # still exactly kept + one split piece
    out = corrections.revert_to_automatic(m, mp, "space:L1-101:split")
    assert out["reverted"] >= 1
    assert "L1-SPLIT-1" not in m.spaces
    assert m.spaces["L1-101"].polygon_m == [[0, 0], [10, 0], [10, 8], [0, 8]]
    assert [o.id for o in m.spaces["L1-101"].openings] == ["w1"]


def test_split_bad_cut_refused(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(ValueError):
        apply_ops(m, _ops({"op": "split_space", "id": "L1-101", "a": [20, 20], "b": [6, 8]}), mp)


def test_revert_to_auto_rename(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    apply_ops(
        m, _ops({"op": "rename_space", "id": "L1-101", "field": "name", "value": "CONFERENCE"}), mp
    )
    apply_ops(m, _ops({"op": "revert_to_auto", "key": "space:L1-101:name"}), mp)
    assert m.spaces["L1-101"].name == "OFFICE"
    hist = _history(m, tmp_path)["corrections"]
    assert all(e.get("undone") for e in hist)


def test_unknown_target_refused_before_changes(tmp_path):
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(ValueError, match="L9-999"):
        apply_ops(
            m, _ops({"op": "rename_space", "id": "L9-999", "field": "name", "value": "X"}), mp
        )
    assert m.spaces["L1-101"].name == "OFFICE"
    assert not _history(m, tmp_path)["corrections"]


def test_viewer_page_carries_correct_mode():
    scene = build_scene(_model())
    page = page_html(scene)
    assert 'id="t-corr"' in page
    assert "viewer_corrections.json" in page
    assert "matchline.viewer_corrections/1" in page


def test_scene_carries_standing_corrections():
    m = _model()
    doc = {
        "schema": corrections.SCHEMA,
        "corrections": [
            {
                "item_id": "vc-rename-L1-101",
                "target": {"kind": "space", "id": "L1-101"},
                "field": "name",
                "before": "OFFICE",
                "after": "CONFERENCE",
                "undone": False,
            },
            {
                "item_id": "vc-old",
                "target": {"kind": "space", "id": "L1-101"},
                "field": "name",
                "undone": True,
            },
        ],
    }
    scene = build_scene(m, corrections_doc=doc)
    keys = [c["key"] for c in scene["corrections"]]
    assert keys == ["space:L1-101:name"]
    assert scene["corrections"][0]["after"] == "CONFERENCE"


def test_unresolvable_revert_key_refused_before_changes(tmp_path):
    """A revert key with no standing correction stops the whole file (#1002)."""
    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    with pytest.raises(ValueError, match="no accepted correction"):
        apply_ops(
            m,
            _ops(
                {"op": "rename_space", "id": "L1-101", "field": "name", "value": "CONFERENCE"},
                {"op": "revert_to_auto", "key": "space:L1-101:name"},
            ),
            mp,
        )
    # the rename never landed and nothing reached the correction history
    assert m.spaces["L1-101"].name == "OFFICE"
    assert _history(m, tmp_path)["corrections"] == []
