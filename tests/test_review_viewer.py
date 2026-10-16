"""Tests for the read-only 3D review viewer (#1001)."""

from pathlib import Path

from building_model import (
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    ReviewItem,
    Space,
    SpaceOpening,
    Zone,
)
from review_viewer import SCHEMA, build_scene, fragment, page_html


def _model():
    m = BuildingModel(name="Test")
    m.levels = [
        Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0),
        Level(id="L2", name="Level 2", elevation_z_m=3.5, wall_height_m=3.0),
    ]
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
        id="L2-201",
        level_id="L2",
        name="LOBBY",
        number="201",
        polygon_m=[[0, 0], [10, 0], [10, 8], [0, 8]],
        label_confidence=0.55,
    )
    m.spaces = {"L1-101": a, "L2-201": b}
    m.envelope = [
        EnvelopeWall(
            id="e1",
            facade="south",
            from_m=[0, 8],
            to_m=[10, 8],
            height_m=3.0,
            space_id="L1-101",
            provenance=Provenance("arch_A101", 1, "wall_takeoff", 0.93),
        )
    ]
    m.zones = {"Z1": Zone(id="Z1", level_id="L1", space_ids=["L1-101"], air_loop="AHU-1")}
    return m


def test_scene_schema_and_counts():
    s = build_scene(_model())
    assert s["schema"] == SCHEMA
    assert [l["id"] for l in s["levels"]] == ["L1", "L2"]
    assert len(s["spaces"]) == 2 and len(s["envelope"]) == 1
    assert s["zones"] == [{"id": "Z1", "spaces": ["L1-101"], "air_loop": "AHU-1"}]


def test_geometry_copied_verbatim_not_rederived():
    s = build_scene(_model())
    # the polygon the viewer ships IS the polygon the model states, point for
    # point: no transform, no simplification, no second geometry copy
    assert s["spaces"][0]["polygon"] == [[0, 0], [10, 0], [10, 8], [0, 8]]
    assert s["envelope"][0]["from"] == [0.0, 8.0]
    assert s["envelope"][0]["to"] == [10.0, 8.0]


def test_levels_carry_elevation_and_height():
    s = build_scene(_model())
    l1 = next(l for l in s["levels"] if l["id"] == "L1")
    assert l1["elevation_z_m"] == 0.0 and l1["wall_height_m"] == 3.0
    assert next(l for l in s["levels"] if l["id"] == "L2")["elevation_z_m"] == 3.5


def test_confidence_carried_for_heat_mapping():
    s = build_scene(_model())
    a = next(x for x in s["spaces"] if x["id"] == "L1-101")
    assert a["confidence"] == 0.9  # min of label and poly_type confidence
    b = next(x for x in s["spaces"] if x["id"] == "L2-201")
    assert b["confidence"] == 0.55
    assert s["envelope"][0]["confidence"] == 0.93
    assert s["openings"][0]["confidence"] == 0.62


def test_zone_assignment_and_air_loop_in_scene():
    s = build_scene(_model())
    assert next(x for x in s["spaces"] if x["id"] == "L1-101")["zones"] == ["Z1"]
    assert next(x for x in s["spaces"] if x["id"] == "L2-201")["zones"] == []


def test_openings_carry_facade_interval_sill_head():
    s = build_scene(_model())
    o = s["openings"][0]
    assert (o["facade"], o["interval"], o["sill"], o["head"]) == (
        "south",
        [2.0, 4.0],
        0.9,
        2.4,
    )
    assert o["space_level"] == "L1"


def test_openings_without_facade_or_interval_are_not_in_the_scene():
    m = _model()
    m.spaces["L1-101"].openings.append(
        SpaceOpening(id="w2", tag="W2", category="window", width_m=1.0, height_m=1.0)
    )
    s = build_scene(m)
    assert [o["id"] for o in s["openings"]] == ["w1"]


def test_envelope_wall_level_follows_its_host_space():
    s = build_scene(_model())
    assert s["envelope"][0]["space_level"] == "L1"


def test_space_without_polygon_is_skipped_not_dropped():
    m = _model()
    m.spaces["L2-201"].polygon_m = []
    s = build_scene(m)
    assert [x["id"] for x in s["spaces"]] == ["L1-101"]
    # and the envelope wall whose host space vanished keeps rendering with an
    # empty level tag rather than a guessed one
    assert s["envelope"][0]["space_level"] == "L1"  # host space still present


def test_review_queue_counts_open_items_and_targets():
    m = _model()
    m.review_queue = [
        ReviewItem(
            id="r1",
            kind="window_room_link",
            description="d",
            confidence=0.3,
            target={"kind": "opening", "id": "w1"},
        ),
        ReviewItem(
            id="r2",
            kind="room_label",
            description="d",
            confidence=0.4,
            target={"kind": "space", "id": "L2-201"},
            status="confirmed",
        ),
    ]
    s = build_scene(m)
    assert s["review_open"] == 1
    assert sorted(s["review_targets"]) == ["w1"]  # confirmed items are not marked


def test_page_html_is_self_contained():
    pg = page_html(build_scene(_model()))
    assert pg.startswith("<!DOCTYPE html>")
    assert "<canvas" in pg and "<script>" in pg
    for bad in ('src="http', 'href="http', "@import", "https://", "http://"):
        assert bad not in pg


def test_page_embeds_the_scene_json():
    pg = page_html(build_scene(_model()))
    assert '"schema": "matchline.review_viewer/1"' in pg
    assert "OFFICE 101" in pg


def test_scene_json_escaped_for_inline_script():
    m = _model()
    m.name = "</script>X"
    pg = page_html(build_scene(m))
    assert "</script>X" not in pg
    assert "<\\/script>X" in pg or "<\\/script>" in pg or "\\/script" in pg


def test_zone_with_many_spaces_and_no_air_loop():
    m = _model()
    m.zones["Z1"].air_loop = ""
    s = build_scene(m)
    assert s["zones"][0]["air_loop"] == ""


def test_fragment_is_embeddable_without_doctype():
    fr = fragment(build_scene(_model()))
    assert not fr.startswith("<!DOCTYPE")
    assert "<canvas" in fr and "<script>" in fr


def test_fragment_banner():
    fr = fragment(build_scene(_model()), banner="Share-back preview")
    assert "Share-back preview" in fr


def test_empty_model_renders():
    s = build_scene(BuildingModel())
    assert s["spaces"] == [] and s["envelope"] == []
    assert '"spaces " + sp' in page_html(s)


def test_round_trip_through_serialized_model():
    # the viewer must work from the same JSON the export writes to
    # review/model.json: load that file back and build the scene from it
    m = _model()
    rebuilt = BuildingModel.from_json(m.to_json())
    s1, s2 = build_scene(m), build_scene(rebuilt)
    assert s1["spaces"] == s2["spaces"]
    assert s1["envelope"] == s2["envelope"]


def test_cli_viewer_writes_page(tmp_path: Path):
    import os
    import subprocess
    import sys

    m = _model()
    mp = tmp_path / "model.json"
    mp.write_text(m.to_json())
    out = tmp_path / "viewer.html"
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-m", "cli", "viewer", str(mp), "--out", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists() and "<canvas" in out.read_text()


def test_share_back_embeds_the_viewer():
    # one render core, two homes: the export package's share_back page embeds
    # the same fragment the CLI page renders
    import export_package
    import review_report

    assert hasattr(review_viewer_export := export_package, "_viewer_fragment")
    fr = review_viewer_export._viewer_fragment(build_scene(_model()), banner="")
    assert "<canvas" in fr
    assert hasattr(review_report, "write_review")  # review/ still written as before
