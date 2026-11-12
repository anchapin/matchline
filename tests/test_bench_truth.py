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


_NO_AREA_UNIT = object()


def _unit_ifc(
    path: Path,
    length_prefix: str | None = "MILLI",
    area_prefix=_NO_AREA_UNIT,
    area_value: float = 20.0,
    wall_length: float = 10000.0,
    wall_qto_prefix: str | None = None,
) -> Path:
    """One space and one external wall, with the file's units declared.

    The quantities are stated in the file's units, as an exporter writes
    them: a millimetre file states a 10 m wall as ``Length=10000`` and a
    20 m2 room as ``NetFloorArea=20`` when its AREAUNIT is m2.
    """
    import ifcopenshell
    import ifcopenshell.api.pset
    import ifcopenshell.api.root
    import ifcopenshell.api.unit

    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
    units = [ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix=length_prefix)]
    if area_prefix is not _NO_AREA_UNIT:
        units.append(ifcopenshell.api.unit.add_si_unit(f, unit_type="AREAUNIT", prefix=area_prefix))
    ifcopenshell.api.unit.assign_unit(f, units=units)

    sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name="space-a")
    sp.GlobalId = "space-a"
    q = ifcopenshell.api.pset.add_qto(f, product=sp, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"NetFloorArea": area_value})

    w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="wall-ext")
    w.GlobalId = "wall-ext"
    ps = ifcopenshell.api.pset.add_pset(f, product=w, name="Pset_WallCommon")
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties={"IsExternal": True})
    q = ifcopenshell.api.pset.add_qto(f, product=w, name="Qto_WallBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"Length": wall_length})
    if wall_qto_prefix is not None:
        unit = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix=wall_qto_prefix)
        for rel in w.IsDefinedBy:
            psd = rel.RelatingPropertyDefinition
            if not psd.is_a("IfcElementQuantity"):
                continue
            for qq in psd.Quantities:
                if qq.Name == "Length":
                    qq.Unit = unit
    f.write(str(path))
    return path


def test_qto_lengths_are_read_in_metres_not_in_file_units(tmp_path: Path) -> None:
    """#1363: a millimetre file states a 10 m wall as ``Length=10000``; read
    raw, the truth said 10000 m and the benchmark scored the importer
    against a number 1000x off."""
    t = build_truth(_unit_ifc(tmp_path / "arch.ifc", area_prefix=None))
    assert t["exterior_wall_length_m"] == pytest.approx(10.0)
    assert t["exterior_walls_measured"] == 1


def test_qto_areas_follow_the_project_area_unit(tmp_path: Path) -> None:
    """The project's AREAUNIT decides the area scale: m2 stays m2, mm2
    (20 000 000 mm2) is 20 m2."""
    m2 = build_truth(_unit_ifc(tmp_path / "m2.ifc", area_prefix=None))
    assert m2["rooms"]["space-a"]["area_m2"] == pytest.approx(20.0)
    assert m2["rooms"]["space-a"]["area_source"] == "qto"
    mm2 = build_truth(_unit_ifc(tmp_path / "mm2.ifc", area_prefix="MILLI", area_value=20_000_000.0))
    assert mm2["rooms"]["space-a"]["area_m2"] == pytest.approx(20.0)
    # no AREAUNIT declared: the area falls back to the length scale squared
    fallback = build_truth(_unit_ifc(tmp_path / "fallback.ifc", area_value=20_000_000.0))
    assert fallback["rooms"]["space-a"]["area_m2"] == pytest.approx(20.0)


def test_qto_quantity_with_its_own_unit_overrides_the_file_unit(tmp_path: Path) -> None:
    """A quantity that declares its own unit is stated in that unit, even
    when the file is in metres (#1351, #1360)."""
    t = build_truth(
        _unit_ifc(
            tmp_path / "arch.ifc",
            length_prefix=None,
            area_prefix=None,
            wall_length=10000.0,
            wall_qto_prefix="MILLI",
        )
    )
    assert t["exterior_wall_length_m"] == pytest.approx(10.0)


def _conversion_unit_ifc(
    path: Path,
    *,
    quantity_unit: str | None = None,
    project_area_unit: str | None = None,
    wall_length: float = 100.0,
    area_value: float = 100.0,
) -> Path:
    """One space and one external wall, in metres, with conversion units.

    ``quantity_unit`` names the unit the quantity itself declares ("INCH"
    for the wall length, "SQUARE FOOT" for the room area);
    ``project_area_unit`` names a conversion-based AREAUNIT the project
    declares ("SQUARE FOOT"). The conversion factors are the ones the
    importer's #1364 fixtures use: an inch is 25.4 mm and a square foot is
    0.09290304 m^2.
    """
    import ifcopenshell
    import ifcopenshell.api.pset
    import ifcopenshell.api.root
    import ifcopenshell.api.unit

    f = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="p")
    units = [ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix=None)]
    if project_area_unit == "SQUARE FOOT":
        units.append(
            f.create_entity(
                "IfcConversionBasedUnit",
                UnitType="AREAUNIT",
                Name="SQUARE FOOT",
                ConversionFactor=f.create_entity(
                    "IfcMeasureWithUnit",
                    ValueComponent=f.create_entity("IfcAreaMeasure", 0.09290304),
                    UnitComponent=f.create_entity(
                        "IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"
                    ),
                ),
            )
        )
    ifcopenshell.api.unit.assign_unit(f, units=units)

    sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name="space-a")
    sp.GlobalId = "space-a"
    q = ifcopenshell.api.pset.add_qto(f, product=sp, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"NetFloorArea": area_value})
    if quantity_unit == "SQUARE FOOT":
        for qq in q.Quantities:
            if qq.Name == "NetFloorArea":
                qq.Unit = f.create_entity(
                    "IfcConversionBasedUnit",
                    UnitType="AREAUNIT",
                    Name="SQUARE FOOT",
                    ConversionFactor=f.create_entity(
                        "IfcMeasureWithUnit",
                        ValueComponent=f.create_entity("IfcAreaMeasure", 0.09290304),
                        UnitComponent=f.create_entity(
                            "IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"
                        ),
                    ),
                )

    w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="wall-ext")
    w.GlobalId = "wall-ext"
    ps = ifcopenshell.api.pset.add_pset(f, product=w, name="Pset_WallCommon")
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties={"IsExternal": True})
    q = ifcopenshell.api.pset.add_qto(f, product=w, name="Qto_WallBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"Length": wall_length})
    if quantity_unit == "INCH":
        for qq in q.Quantities:
            if qq.Name == "Length":
                qq.Unit = f.create_entity(
                    "IfcConversionBasedUnit",
                    UnitType="LENGTHUNIT",
                    Name="INCH",
                    ConversionFactor=f.create_entity(
                        "IfcMeasureWithUnit",
                        ValueComponent=f.create_entity("IfcLengthMeasure", 25.4),
                        UnitComponent=f.create_entity(
                            "IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"
                        ),
                    ),
                )
    f.write(str(path))
    return path


def test_qto_length_honours_a_conversion_based_quantity_unit(tmp_path: Path) -> None:
    """A Length quantity denominated in inches via an
    IfcConversionBasedUnit is read in metres (#1364, #1372): 100 in = 2.54 m,
    not the raw 100.0 the project-unit fallback would give."""
    t = build_truth(_conversion_unit_ifc(tmp_path / "inch.ifc", quantity_unit="INCH"))
    assert t["exterior_wall_length_m"] == pytest.approx(2.54)
    assert t["exterior_walls_measured"] == 1


def test_qto_area_honours_a_conversion_based_quantity_unit(tmp_path: Path) -> None:
    """A NetFloorArea quantity denominated in square feet via an
    IfcConversionBasedUnit is read in m^2 (#1364, #1372): 100 ft^2 =
    9.290304 m^2, not the raw 100.0 -- and not 100 x the areal factor
    squared."""
    t = build_truth(_conversion_unit_ifc(tmp_path / "sqft.ifc", quantity_unit="SQUARE FOOT"))
    assert t["rooms"]["space-a"]["area_m2"] == pytest.approx(9.290304)
    assert t["rooms"]["space-a"]["area_source"] == "qto"


def test_project_area_unit_honours_a_conversion_based_unit(tmp_path: Path) -> None:
    """A project whose AREAUNIT is a conversion-based SQUARE FOOT scales
    the areas by the areal factor itself: 100 ft^2 = 9.290304 m^2
    (#1372)."""
    t = build_truth(
        _conversion_unit_ifc(tmp_path / "proj_sqft.ifc", project_area_unit="SQUARE FOOT")
    )
    assert t["rooms"]["space-a"]["area_m2"] == pytest.approx(9.290304)
    assert t["rooms"]["space-a"]["area_source"] == "qto"


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


def test_shared_truth_dirs_flags_two_buildings_in_one_directory(tmp_path: Path) -> None:
    """Two buildings staged in one dir would collide on truth.json (#1259)."""
    from scripts.bench_truth import shared_truth_dirs

    a = tmp_path / "a" / "arch.ifc"
    b = tmp_path / "b" / "arch.ifc"
    shared = tmp_path / "shared"
    entries = [("a", a), ("b", b), ("c1", shared / "one.ifc"), ("c2", shared / "two.ifc")]
    conflicts = shared_truth_dirs(entries)
    assert conflicts == {shared: ["c1", "c2"]}


def test_per_building_staging_is_the_convention_so_truth_pins_never_cross(
    tmp_path: Path,
) -> None:
    """Each building in its own directory: the truth files stay distinct and
    each one is pinned to its own IFC's sha256."""
    root = tmp_path / "bench"
    ifcs = {}
    for name in ("p1", "p2"):
        d = root / name
        d.mkdir(parents=True)
        ifcs[name] = _tiny_ifc(d / "arch.ifc")
    truths = {}
    for name, ifc in ifcs.items():
        truth, how = load_or_freeze_truth(ifc)
        assert how == "built"
        truths[name] = truth
    assert truths["p1"]["ifc_sha256"] != truths["p2"]["ifc_sha256"]
    assert truth_path_for(ifcs["p1"]) != truth_path_for(ifcs["p2"])
    # reloading each finds its own frozen file, still pinned to its own IFC
    for name, ifc in ifcs.items():
        again, how = load_or_freeze_truth(ifc)
        assert how == "frozen"
        assert again["ifc_sha256"] == truths[name]["ifc_sha256"]


def test_shared_truth_dirs_survives_dotdot_spellings(tmp_path: Path) -> None:
    """#1273: two spellings of one directory still collide after resolve()."""
    from scripts.bench_truth import shared_truth_dirs

    shared = tmp_path / "shared"
    shared.mkdir()
    entries = [
        ("c1", str(shared / "one.ifc")),
        ("c2", str(shared / ".." / "shared" / "two.ifc")),
    ]
    conflicts = shared_truth_dirs(entries)
    assert len(conflicts) == 1
    assert sorted(next(iter(conflicts.values()))) == ["c1", "c2"]
