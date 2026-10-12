"""Multi-storey IFC4 export (#639)."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.util.element as _El  # noqa: E402
import ifcopenshell.util.placement as _Pl  # noqa: E402

from bem_export import validate_ifc4, write_ifc4  # noqa: E402
from building_model import SpaceOpening  # noqa: E402
from ifc_export import _bem_from_model  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from tests.test_multistorey_export import _model, _two_storey  # noqa: E402


def _basement():
    return _model(
        [("B1", -3.0, False), ("L1", 0.0, True), ("L2", 3.0, True)],
        [
            ("B1-001", "B1", (0, 0, 20, 10)),
            ("L1-101", "L1", (0, 0, 20, 10)),
            ("L2-201", "L2", (0, 0, 12, 10)),
        ],
    )


def _write(m, tmp_path, name="m.ifc"):
    p = write_ifc4(_bem_from_model(m), tmp_path / name)
    ok, errs = validate_ifc4(p)
    assert ok, errs[:5]
    return p, ifcopenshell.open(str(p))


def _z(el):
    return float(_Pl.get_local_placement(el.ObjectPlacement)[2][3])


def _storey(el):
    c = _El.get_container(el)
    if c is None:
        c = _El.get_aggregate(el)
    return c.Name


def test_one_storey_per_level_with_elevation_and_stated_above_ground(tmp_path):
    _, f = _write(_basement(), tmp_path)
    st = sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation)
    assert [(s.Name, s.Elevation) for s in st] == [
        ("Level B1", -3.0),
        ("Level L1", 0.0),
        ("Level L2", 3.0),
    ]
    above = [_El.get_psets(s)["Pset_BuildingStoreyCommon"]["AboveGround"] for s in st]
    assert above == [False, True, True]
    assert [_z(s) for s in st] == [-3.0, 0.0, 3.0]


def test_unstated_above_ground_writes_no_pset(tmp_path):
    _, f = _write(_two_storey(), tmp_path)
    for s in f.by_type("IfcBuildingStorey"):
        assert "Pset_BuildingStoreyCommon" not in _El.get_psets(s)


def test_spaces_and_walls_sit_on_their_own_storey(tmp_path):
    _, f = _write(_basement(), tmp_path)
    spaces = sorted(_storey(s) for s in f.by_type("IfcSpace"))
    assert spaces == ["Level B1", "Level L1", "Level L2"]
    by_storey = {}
    for w in f.by_type("IfcWall"):
        by_storey.setdefault(_storey(w), []).append(_z(w))
    assert {k: len(v) for k, v in by_storey.items()} == {
        "Level B1": 4,
        "Level L1": 4,
        "Level L2": 4,
    }
    assert set(by_storey["Level B1"]) == {-3.0} and set(by_storey["Level L2"]) == {3.0}


def test_slabs_typed_and_placed_on_their_surface(tmp_path):
    _, f = _write(_basement(), tmp_path)
    slabs = {}
    for s in f.by_type("IfcSlab"):
        slabs.setdefault(s.PredefinedType, []).append(s)
    # underground slab under B1, interior floors over B1 and L1, setback roof
    # over L1 and top roof over L2
    assert len(slabs["BASESLAB"]) == 1
    (base,) = slabs["BASESLAB"]
    assert _storey(base) == "Level B1" and _z(base) == pytest.approx(-3.0 - 0.15)
    base_pset = _El.get_psets(base)["Pset_SlabCommon"]
    assert base_pset["IsExternal"] is True and "ThermalTransmittance" not in base_pset
    floors = slabs["FLOOR"]
    assert len(floors) == 2
    assert all(_El.get_psets(s)["Pset_SlabCommon"]["IsExternal"] is False for s in floors)
    assert sorted(_z(s) for s in floors) == pytest.approx([-0.2, 2.8])
    roofs = sorted(slabs["ROOF"], key=_z)
    assert [(_storey(r), _z(r)) for r in roofs] == [("Level L1", 3.0), ("Level L2", 6.0)]


def test_openings_and_skylights_on_their_own_storey(tmp_path):
    m = _two_storey()
    m.spaces["L2-201"].openings.append(
        SpaceOpening(
            id="w1", tag="A", category="window", width_m=1.2, height_m=1.5, host_facade="south"
        )
    )
    m.spaces["L2-201"].openings.append(
        SpaceOpening(id="s1", tag="SK", category="skylight", width_m=1.0, height_m=1.0)
    )
    _, f = _write(m, tmp_path)
    wins = f.by_type("IfcWindow")
    assert len(wins) == 2
    for w in wins:
        assert _storey(w) == "Level L2"
    (sky,) = [w for w in wins if w.PredefinedType == "SKYLIGHT"]
    host = sky.FillsVoids[0].RelatingOpeningElement.VoidsElements[0].RelatingBuildingElement
    assert host.is_a("IfcSlab") and host.PredefinedType == "ROOF" and _z(host) == 6.0


def test_single_storey_ifc_has_one_level_and_no_level_slabs(tmp_path):
    m = _model([("L1", 0.0, None)], [("L1-101", "L1", (0, 0, 20, 10))])
    _, f = _write(m, tmp_path)
    assert [s.Name for s in f.by_type("IfcBuildingStorey")] == ["Level 1"]
    assert len(f.by_type("IfcSlab")) == 0


def test_reimport_keeps_levels_and_each_space_on_its_level(tmp_path):
    m = _basement()
    p, _ = _write(m, tmp_path)
    back = import_ifc(p)
    elev = sorted(round(lv.elevation_z_m, 3) for lv in back.levels)
    assert elev == [-3.0, 0.0, 3.0]
    level_z = {lv.id: round(lv.elevation_z_m, 3) for lv in back.levels}
    got = {sp.id: level_z[sp.level_id] for sp in back.spaces.values()}
    assert got == {"B1-001": -3.0, "L1-101": 0.0, "L2-201": 3.0}
    above = {round(lv.elevation_z_m, 3): lv.above_ground for lv in back.levels}
    assert above == {-3.0: False, 0.0: True, 3.0: True}
