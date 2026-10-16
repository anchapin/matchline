"""Takeoff diff between two drawing-set revisions (#993)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import takeoff_diff  # noqa: E402
from cli import main as cli_main  # noqa: E402


def _space(name, area, level="L1", windows=0, doors=0):
    return {
        "id": name,
        "level_id": level,
        "name": name,
        "area_m2": area,
        "openings": (
            [{"category": "window"} for _ in range(windows)]
            + [{"category": "door"} for _ in range(doors)]
        ),
    }


def test_rooms_added_removed_and_unchanged():
    old = {"model": {"spaces": {"a": _space("Office", 20.0), "b": _space("Hall", 10.0)}}}
    new = {"model": {"spaces": {"a": _space("Office", 20.0), "c": _space("Cafe", 30.0)}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["added"] == 1 and rep["rooms"]["added_ids"] == ["c"]
    assert rep["rooms"]["removed"] == 1 and rep["rooms"]["removed_ids"] == ["b"]
    assert rep["rooms"]["unchanged"] == 1


def test_room_rename_area_and_level_changes():
    old = {"spaces": {"a": _space("Office", 20.0, level="L1")}}
    new = {"spaces": {"a": _space("Office 2", 25.0, level="L2")}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    r = rep["rooms"]
    assert r["renamed"] == 1 and r["area_changed"] == 1 and r["moved_level"] == 1
    assert r["unchanged"] == 0
    # tiny area drift is rounding noise, not a change
    old2 = {"spaces": {"a": _space("Office", 20.0)}}
    new2 = {"spaces": {"a": _space("Office", 20.05)}}
    assert takeoff_diff.diff_takeoffs(old2, new2)["rooms"]["area_changed"] == 0


def test_windows_and_doors_counted_per_space():
    old = {
        "spaces": {
            "a": _space("Office", 20.0, windows=2, doors=1),
            "b": _space("Hall", 10.0, doors=1),
        }
    }
    new = {
        "spaces": {
            "a": _space("Office", 20.0, windows=3, doors=1),
            "b": _space("Hall", 10.0, windows=1),
        }
    }
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"] == {"added": 2, "removed": 0, "moved": 0, "spaces_changed": 2}
    assert rep["doors"] == {"added": 0, "removed": 1, "moved": 0, "spaces_changed": 1}


def test_symbols_from_set_reports():
    old_rep = {
        "hvac_legends": [
            {"rows": [{"class": "diffuser"}, {"class": "diffuser"}, {"class": "sensor"}]}
        ]
    }
    new_rep = {"hvac_legends": [{"rows": [{"class": "diffuser"}, {"class": "grille"}]}]}
    rep = takeoff_diff.diff_takeoffs({}, {}, old_report=old_rep, new_report=new_rep)
    assert rep["symbols"]["provided"] is True
    assert rep["symbols"]["added"] == 1 and rep["symbols"]["removed"] == 2
    assert rep["symbols"]["by_class"]["diffuser"] == {"added": 0, "removed": 1}
    assert rep["symbols"]["by_class"]["grille"] == {"added": 1, "removed": 0}


def test_symbols_legacy_cls_key_fallback():
    """Rows keyed "cls" (the old hand-written-fixture spelling) still count (#1034)."""
    old_rep = {"hvac_legends": [{"rows": [{"cls": "diffuser"}]}]}
    new_rep = {"hvac_legends": [{"rows": [{"class": "grille"}, {"cls": "diffuser"}]}]}
    rep = takeoff_diff.diff_takeoffs({}, {}, old_report=old_rep, new_report=new_rep)
    assert rep["symbols"]["provided"] is True
    # old: 1 diffuser (legacy key); new: grille (real key) + diffuser (legacy key)
    assert rep["symbols"]["added"] == 1 and rep["symbols"]["removed"] == 0
    assert rep["symbols"]["by_class"]["grille"] == {"added": 1, "removed": 0}
    assert "diffuser" not in rep["symbols"]["by_class"]  # 1 -> 1 is no change


def test_symbols_absent_without_reports():
    rep = takeoff_diff.diff_takeoffs({}, {})
    assert rep["symbols"] == {"provided": False, "added": 0, "removed": 0, "by_class": {}}


def test_cli_takeoff_diff(tmp_path, capsys):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(
        '{"spaces": {"a": {"name": "Office", "area_m2": 20.0, '
        '"level_id": "L1", "openings": [{"category": "door"}]}}}'
    )
    new.write_text(
        '{"spaces": {"a": {"name": "Office", "area_m2": 20.0, '
        '"level_id": "L1", "openings": [{"category": "door"}, '
        '{"category": "door"}]}}}'
    )
    out = tmp_path / "diff.json"
    cli_main(["takeoff-diff", "--old", str(old), "--new", str(new), "--out", str(out)])
    assert out.exists()
    import json

    rep = json.loads(out.read_text())
    assert rep["schema"] == "matchline.takeoff_diff/1"
    assert rep["doors"]["added"] == 1


def _opening(cat, tag=None, sheet=None, bbox=None):
    o = {"category": cat}
    if tag:
        o["tag"] = tag
    if sheet and bbox:
        o["provenance"] = {
            "sheet_id": sheet,
            "revision": 1,
            "method": "grid_registration",
            "confidence": 1.0,
            "bbox": bbox,
        }
    return o


def test_moved_opening_by_tag():
    """A window tagged W1 that moved between spaces reports moved, not +1/-1."""
    old = {"spaces": {"a": {"openings": [_opening("window", tag="W1")]}}}
    new = {"spaces": {"b": {"openings": [_opening("window", tag="W1")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"] == {"added": 0, "removed": 0, "moved": 1, "spaces_changed": 2}


def test_same_space_tag_pair_is_not_moved():
    """W1 in the same space in both revisions is no change at all."""
    old = {"spaces": {"a": {"openings": [_opening("window", tag="W1")]}}}
    new = {"spaces": {"a": {"openings": [_opening("window", tag="W1")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"]["moved"] == 0 and rep["windows"]["added"] == 0
    assert rep["windows"]["removed"] == 0 and rep["windows"]["spaces_changed"] == 0


def test_moved_opening_by_bbox_overlap():
    """Untagged openings pair on a shared sheet's bbox overlap (IoU >= 0.5)."""
    old = {
        "spaces": {
            "a": {"openings": [_opening("window", sheet="elev_A201", bbox=[100, 100, 160, 260])]}
        }
    }
    # same opening, shifted 10 px right and down: IoU ~ 0.77
    new = {
        "spaces": {
            "b": {"openings": [_opening("window", sheet="elev_A201", bbox=[110, 110, 170, 270])]}
        }
    }
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"]["moved"] == 1 and rep["windows"]["added"] == 0
    assert rep["windows"]["removed"] == 0


def test_untagged_without_geometry_stays_added_removed():
    """No shared identity evidence: the old +1/-1 accounting stands."""
    old = {"spaces": {"a": {"openings": [_opening("window")]}}}
    new = {"spaces": {"b": {"openings": [_opening("window")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"] == {"added": 1, "removed": 1, "moved": 0, "spaces_changed": 2}


def test_duplicate_tags_never_pair():
    """A tag naming two openings in one revision is ambiguous: no pairing."""
    old = {
        "spaces": {
            "a": {"openings": [_opening("window", tag="W1")]},
            "b": {"openings": [_opening("window", tag="W1")]},
        }
    }
    new = {"spaces": {"c": {"openings": [_opening("window", tag="W1")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"]["moved"] == 0
    assert rep["windows"]["added"] == 1 and rep["windows"]["removed"] == 2


def test_moved_plus_genuine_addition():
    """A moved window and a genuinely new window report separately."""
    old = {"spaces": {"a": {"openings": [_opening("window", tag="W1")]}}}
    new = {
        "spaces": {
            "b": {"openings": [_opening("window", tag="W1")]},
            "a": {"openings": [_opening("window", tag="W2")]},
        }
    }
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"]["moved"] == 1
    assert rep["windows"]["added"] == 1 and rep["windows"]["removed"] == 0
    assert rep["windows"]["spaces_changed"] == 2


def _fpspace(name, area, level="L1", polygon=None):
    sp = {"id": name, "name": name, "area_m2": area, "level_id": level}
    if polygon:
        sp["polygon_m"] = polygon
    return sp


def test_reidentified_on_id_change():
    """Same name, area, and level under a new id: reidentified, not +1/-1."""
    old = {"spaces": {"a": _fpspace("Office", 20.0)}}
    new = {"spaces": {"c": _fpspace("Office", 20.0)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    r = rep["rooms"]
    assert r["reidentified"] == 1
    assert r["reidentified_pairs"] == [{"old_id": "a", "new_id": "c"}]
    assert r["added"] == 0 and r["removed"] == 0


def test_reidentified_with_rename_and_centroid():
    """Renamed AND re-id'd spaces pair on tokens plus a nearby centroid."""
    old = {"spaces": {"a": _fpspace("Office", 20.0, polygon=[[0, 0], [4, 0], [4, 5], [0, 5]])}}
    new = {
        "spaces": {
            "z": _fpspace(
                "Office Area", 20.0, polygon=[[0.2, 0.2], [4.2, 0.2], [4.2, 5.2], [0.2, 5.2]]
            )
        }
    }
    rep = takeoff_diff.diff_takeoffs(old, new)
    r = rep["rooms"]
    assert r["reidentified"] == 1 and r["added"] == 0 and r["removed"] == 0


def test_reidentification_never_across_levels():
    old = {"spaces": {"a": _fpspace("Office", 20.0, level="L1")}}
    new = {"spaces": {"c": _fpspace("Office", 20.0, level="L2")}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["reidentified"] == 0
    assert rep["rooms"]["added"] == 1 and rep["rooms"]["removed"] == 1


def test_reidentification_requires_area_match():
    old = {"spaces": {"a": _fpspace("Office", 20.0)}}
    new = {"spaces": {"c": _fpspace("Office", 30.0)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["reidentified"] == 0


def test_ambiguous_duplicate_names_never_pair():
    """Two removed Offices, one added Office, no geometry: no pairing."""
    old = {
        "spaces": {
            "a": _fpspace("Office", 20.0),
            "b": _fpspace("Office", 25.0),
        }
    }
    new = {"spaces": {"c": _fpspace("Office", 20.0)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["reidentified"] == 0
    assert rep["rooms"]["added"] == 1 and rep["rooms"]["removed"] == 2


def test_centroid_far_blocks_reid():
    """Token overlap alone is not identity: distant centroids stay unpaired."""
    far_a = [[0, 0], [4, 0], [4, 5], [0, 5]]
    far_b = [[100, 100], [104, 100], [104, 105], [100, 105]]
    old = {"spaces": {"a": _fpspace("Conference Room", 20.0, polygon=far_a)}}
    new = {"spaces": {"z": _fpspace("Conference", 20.0, polygon=far_b)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["reidentified"] == 0
    assert rep["rooms"]["added"] == 1 and rep["rooms"]["removed"] == 1


def test_polygon_centroid_helper():
    c = takeoff_diff._polygon_centroid([[0, 0], [4, 0], [4, 4], [0, 4]])
    assert abs(c[0] - 2.0) < 1e-9 and abs(c[1] - 2.0) < 1e-9
    assert takeoff_diff._polygon_centroid([[0, 0], [1, 1]]) is None
    assert takeoff_diff._polygon_centroid(None) is None


def test_review_items_removed_room_first():
    """A removed room is a queue item at urgency 3."""
    old = {"spaces": {"a": _fpspace("Office", 20.0), "b": _fpspace("Hall", 10.0)}}
    new = {"spaces": {"a": _fpspace("Office", 20.0)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    items = takeoff_diff.review_items(rep)
    removed = [i for i in items if i["kind"] == "revision_room_removed"]
    assert len(removed) == 1
    assert removed[0]["id"] == "takeoff-diff-room-removed-b"
    assert removed[0]["urgency"] == 3
    assert removed[0]["target"] == {"kind": "space", "id": "b"}


def test_review_items_opening_counts():
    """Window/door flips become count items; moved-only reports nothing."""
    old = {"spaces": {"a": {"openings": [_opening("window"), _opening("door")]}}}
    new = {"spaces": {"b": {"openings": [_opening("window")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    items = takeoff_diff.review_items(rep)
    by_id = {i["id"]: i for i in items}
    assert by_id["takeoff-diff-windows"]["urgency"] == 2
    assert "1 removed" in by_id["takeoff-diff-windows"]["description"]
    assert by_id["takeoff-diff-doors"]["urgency"] == 2
    assert "1 removed" in by_id["takeoff-diff-doors"]["description"]


def test_review_items_moved_only_has_no_item():
    """A window that moved (paired) does not create an opening item."""
    old = {"spaces": {"a": {"openings": [_opening("window", tag="W1")]}, "k": {}}}
    new = {"spaces": {"a": {}, "k": {"openings": [_opening("window", tag="W1")]}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"]["moved"] == 1
    assert takeoff_diff.review_items(rep) == []


def test_review_items_added_rooms_and_symbols():
    """Added rooms become items; symbols only when reports were provided."""
    old = {"spaces": {}}
    new = {"spaces": {"c": _fpspace("Cafe", 30.0)}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert [i["kind"] for i in takeoff_diff.review_items(rep)] == ["revision_room_added"]
    old_rep = {"hvac_legends": [{"rows": [{"class": "diffuser"}]}]}
    rep2 = takeoff_diff.diff_takeoffs(old, new, old_report=old_rep, new_report={})
    items = takeoff_diff.review_items(rep2)
    sym = [i for i in items if i["kind"] == "revision_symbol_count"]
    assert len(sym) == 1 and sym[0]["urgency"] == 1


def test_cli_takeoff_diff_review_items_flag(tmp_path):
    old = tmp_path / "old.json"
    old.write_text('{"spaces": {"a": {"name": "Hall", "area_m2": 10.0}}}')
    new = tmp_path / "new.json"
    new.write_text('{"spaces": {}}')
    out = tmp_path / "items.json"
    cli_main(["takeoff-diff", "--old", str(old), "--new", str(new), "--review-items", str(out)])
    import json

    items = json.loads(out.read_text())
    assert [i["id"] for i in items] == ["takeoff-diff-room-removed-a"]
    assert items[0]["provenance"]["method"] == "takeoff_diff"
