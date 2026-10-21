"""Tests for scripts/bench_truth.py (#1165): independent, frozen benchmark truth."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bench_truth import (  # noqa: E402
    TRUTH_SCHEMA,
    TruthMismatch,
    build_truth,
    load_or_freeze_truth,
    truth_path_for,
)
from scripts.benchmark import Building, Row, evaluate_building, score_against_truth  # noqa: E402


def _tiny_ifc(path: Path, external: bool | None = True) -> Path:
    """Two spaces with NetFloorArea, two walls (one external, Qto length), a window."""
    import ifcopenshell
    import ifcopenshell.api.pset
    import ifcopenshell.api.root

    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
    for gid, area in (("space-a", 20.0), ("space-b", 12.5)):
        sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=gid)
        sp.GlobalId = gid
        q = ifcopenshell.api.pset.add_qto(f, product=sp, name="Qto_SpaceBaseQuantities")
        ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"NetFloorArea": area})
    for gid, length, ext in (("wall-ext", 10.0, external), ("wall-int", 4.0, False)):
        w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name=gid)
        w.GlobalId = gid
        if ext is not None:
            ps = ifcopenshell.api.pset.add_pset(f, product=w, name="Pset_WallCommon")
            ifcopenshell.api.pset.edit_pset(f, pset=ps, properties={"IsExternal": ext})
        q = ifcopenshell.api.pset.add_qto(f, product=w, name="Qto_WallBaseQuantities")
        ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"Length": length})
    win = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWindow", name="w")
    win.OverallWidth, win.OverallHeight = 2.0, 1.5
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcDoor", name="d")
    f.write(str(path))
    return path


def test_truth_module_never_imports_matchlines_ifc_reader() -> None:
    """Truth must be independent of import_ifc, or the benchmark scores itself."""
    src = (ROOT / "scripts" / "bench_truth.py").read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any("ifc_import" in m for m in mods), mods
    assert not any(m == "building_model" or m.startswith("ifc.") for m in mods), mods


def test_build_truth_reads_quantities_straight_from_the_ifc(tmp_path: Path) -> None:
    t = build_truth(_tiny_ifc(tmp_path / "arch.ifc"))
    assert t["schema"] == TRUTH_SCHEMA
    assert len(t["ifc_sha256"]) == 64
    assert {g: r["area_m2"] for g, r in t["rooms"].items()} == {"space-a": 20.0, "space-b": 12.5}
    assert {r["area_source"] for r in t["rooms"].values()} == {"qto"}
    # only the IsExternal wall counts
    assert t["exterior_wall_length_m"] == pytest.approx(10.0)
    assert t["exterior_walls_measured"] == 1
    assert t["windows"] == 1 and t["doors"] == 1
    assert t["window_area_m2"] == pytest.approx(3.0)


def test_no_external_flag_means_no_wall_truth_not_zero(tmp_path: Path) -> None:
    t = build_truth(_tiny_ifc(tmp_path / "arch.ifc", external=None))
    assert t["exterior_wall_length_m"] is None
    assert "IsExternal" in t["exterior_wall_reason"]


def test_truth_is_frozen_then_reused_and_pinned_to_the_ifc(tmp_path: Path) -> None:
    ifc = _tiny_ifc(tmp_path / "arch.ifc")
    truth, how = load_or_freeze_truth(ifc)
    assert how == "built"
    assert truth_path_for(ifc).exists()
    again, how2 = load_or_freeze_truth(ifc)
    assert how2 == "frozen" and again == truth
    ifc.write_text(ifc.read_text() + "\n")  # IFC changes under frozen truth
    with pytest.raises(TruthMismatch):
        load_or_freeze_truth(ifc)


def test_stale_truth_is_an_error_row_not_a_silent_rescore(tmp_path: Path, monkeypatch) -> None:
    ifc = _tiny_ifc(tmp_path / "arch.ifc")
    stale = build_truth(ifc) | {"ifc_sha256": "0" * 64}
    truth_path_for(ifc).write_text(json.dumps(stale))
    # the tiny fixture has no units, so stand in for the import; the truth
    # check runs before anything reads the model
    monkeypatch.setattr("scripts.benchmark.import_ifc", lambda p: SimpleNamespace())
    row = evaluate_building(Building(name="stale", arch_ifc=ifc), tmp_path)
    assert row.status == "error"
    assert "sha256" in row.detail


def test_score_against_truth_measures_wall_length_and_areas() -> None:
    truth = {
        "rooms": {"a": {"area_m2": 20.0}, "b": {"area_m2": 10.0}, "c": {"area_m2": 5.0}},
        "exterior_wall_length_m": 100.0,
        "windows": 4,
        "doors": 2,
        "window_area_m2": 8.0,
    }
    model_q = {
        "rooms": 2,
        "areas_by_gid": {"a": 18.0, "b": 10.0},
        "wall_len_m": 90.0,
        "windows": 3,
        "window_area_m2": 6.0,
        "doors": 2,
    }
    row = Row(name="x")
    score_against_truth(row, model_q, truth)
    assert row.rooms_truth == 3 and row.rooms_model == 2
    assert row.area_err == pytest.approx(0.05)  # (0.1 + 0.0) / 2
    assert row.extras["rooms_matched"] == 2
    assert row.wall_len_err == pytest.approx(0.10)
    assert row.window_area_err == pytest.approx(0.25)
    assert (row.windows_truth, row.windows_model) == (4, 3)


def test_missing_wall_truth_leaves_the_column_blank() -> None:
    truth = {"rooms": {}, "exterior_wall_length_m": None, "exterior_wall_reason": "why"}
    model_q = {
        "rooms": 0,
        "areas_by_gid": {},
        "wall_len_m": 50.0,
        "windows": 0,
        "window_area_m2": 0.0,
        "doors": 0,
    }
    row = Row(name="y")
    score_against_truth(row, model_q, truth)
    assert row.wall_len_err is None
    assert row.area_err is None
    assert row.extras["wall_len"] == "why"


def _body_wall_ifc(path: Path, with_body: bool) -> Path:
    """One external wall with no Qto and no Axis; a swept body only if asked."""
    import ifcopenshell.api.context
    import ifcopenshell.api.geometry
    import ifcopenshell.api.pset
    import ifcopenshell.api.root

    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
    w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="wall-bare")
    w.GlobalId = "wall-bare"
    ps = ifcopenshell.api.pset.add_pset(f, product=w, name="Pset_WallCommon")
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties={"IsExternal": True})
    if with_body:
        body = ifcopenshell.api.context.add_context(
            f, context_type="Model", context_identifier="Body"
        )
        rep = ifcopenshell.api.geometry.add_wall_representation(
            f, context=body, length=10.0, height=3.0, thickness=0.2
        )
        ifcopenshell.api.geometry.assign_representation(f, product=w, representation=rep)
    f.write(str(path))
    return path


def test_unmeasured_exterior_wall_falls_back_to_body_geometry(tmp_path: Path) -> None:
    """A flagged wall with neither Qto length nor Axis must not shrink the
    total: the body's longest plan extent stands in for it (#1205)."""
    t = build_truth(_body_wall_ifc(tmp_path / "arch.ifc", with_body=True))
    assert t["exterior_wall_length_m"] == pytest.approx(10.0)
    assert t["exterior_walls_measured"] == 0
    assert t["exterior_walls_from_geometry"] == 1
    assert t["exterior_walls_unmeasured"] == 0


def test_exterior_wall_with_no_measurable_tier_is_null_not_zero(tmp_path: Path) -> None:
    t = build_truth(_body_wall_ifc(tmp_path / "arch.ifc", with_body=False))
    assert t["exterior_wall_length_m"] is None
    assert "measurable body" in t["exterior_wall_reason"]
    assert t["exterior_walls_unmeasured"] == 1
    assert t["exterior_walls_from_geometry"] == 0


def _footprint_ifc(path: Path) -> Path:
    """One extruded-solid room and one face-set hallway, both L-shaped, no Qto."""
    from tests.ifc_builder import IfcBuilder

    b = IfcBuilder()
    l_shape = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]
    b.space("ROOM 101", l_shape, height=3.0, long_name="Room")
    b.space_face_set("HALL 201", l_shape, height=2.9, long_name="Hallway")
    return b.write(path)


def test_face_set_space_truth_single_counts_the_downward_wound_top(tmp_path: Path) -> None:
    """#1218: an open surface model's top face is wound downward too; the
    old downward-facing sum doubled the hallway's 12 m2 floor to 24."""
    t = build_truth(_footprint_ifc(tmp_path / "arch.ifc"))
    rooms = t["rooms"]
    by_area = sorted(round(r["area_m2"], 6) for r in rooms.values())
    assert by_area == [12.0, 12.0]
    assert {r["area_source"] for r in rooms.values()} == {"geometry"}


def test_profile_floor_area_declared_footprint_of_one_vertical_extrusion(
    tmp_path: Path,
) -> None:
    t = build_truth(_footprint_ifc(tmp_path / "arch.ifc"))
    # both spaces are single vertical extrusions, so the healthy kernel
    # tessellation and the declared profile agree and geometry stays primary
    assert {r["area_source"] for r in t["rooms"].values()} == {"geometry"}


def test_under_tessellated_extrusion_falls_back_to_the_declared_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """#1218: the duplex stair space's bottom came back as a 3.8 m2 landing
    of a 20.5 m2 profile; below half the declared footprint, trust the
    profile and say so."""
    import scripts.bench_truth as bt

    monkeypatch.setattr(bt, "_geom_floor_area", lambda el: 2.0)
    t = bt.build_truth(_footprint_ifc(tmp_path / "arch.ifc"))
    rooms = t["rooms"]
    # the extruded room trusts its declared profile; the face-set hallway
    # has no profile to fall back to and keeps the (stubbed) tessellation
    by_source = {r["area_source"]: r["area_m2"] for r in rooms.values()}
    assert abs(by_source["profile"] - 12.0) < 1e-6
    assert abs(by_source["geometry"] - 2.0) < 1e-6


def _broken_attr_window_ifc(path: Path, with_body: bool) -> Path:
    """One window whose stated attributes are corrupt (gni-49 style: frame
    thickness in the width field) and whose body exists only if asked."""
    import ifcopenshell
    import ifcopenshell.api.context
    import ifcopenshell.api.geometry
    import ifcopenshell.api.root

    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
    win = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWindow", name="w")
    win.GlobalId = "win-broken"
    win.OverallWidth, win.OverallHeight = 0.1795, 1.8
    if with_body:
        body = ifcopenshell.api.context.add_context(
            f, context_type="Model", context_identifier="Body"
        )
        profile = f.create_entity(
            "IfcRectangleProfileDef",
            ProfileType="AREA",
            XDim=1.8,
            YDim=2.1,
            Position=f.create_entity("IfcAxis2Placement2D"),
        )
        solid = f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=f.create_entity("IfcAxis2Placement3D"),
            ExtrudedDirection=f.create_entity("IfcDirection", (0.0, 0.0, 1.0)),
            Depth=0.18,
        )
        rep = f.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=body,
            RepresentationIdentifier="Body",
            RepresentationType="SweptSolid",
            Items=(solid,),
        )
        ifcopenshell.api.geometry.assign_representation(f, product=win, representation=rep)
    f.write(str(path))
    return path


def test_window_area_reads_geometry_when_attributes_lie(tmp_path: Path) -> None:
    """gni-49's stated window attributes are corrupt; the tessellated body
    measures the real opening, so geometry outranks the attributes (#1220)."""
    t = build_truth(_broken_attr_window_ifc(tmp_path / "arch.ifc", with_body=True))
    assert t["window_area_m2"] == pytest.approx(1.8 * 2.1, abs=0.05)
    assert t["windows"] == 1
    assert t["windows_sized"] == 1
    assert t["windows_from_geometry"] == 1


def test_window_area_falls_back_to_stated_attributes_without_a_body(tmp_path: Path) -> None:
    """A window with no tessellable body keeps the stated-attribute fallback."""
    t = build_truth(_broken_attr_window_ifc(tmp_path / "arch.ifc", with_body=False))
    assert t["window_area_m2"] == pytest.approx(0.1795 * 1.8)
    assert t["windows_from_geometry"] == 0
