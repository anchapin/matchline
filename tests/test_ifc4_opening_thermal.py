"""IFC4 export carries window, door and skylight constructions (#747)."""

from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.util.element as _El  # noqa: E402

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4  # noqa: E402
from bem_ifc4 import validate_ifc4  # noqa: E402

RING = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]

OC = {
    "t55-window": {"name": "Window", "category": "window", "u": 2.0442, "shgc": 0.38, "vt": 0.418},
    "t55-door": {"name": "Door", "category": "door", "u": 2.101, "shgc": None, "vt": None},
    "t55-skylight": {
        "name": "Skylight",
        "category": "skylight",
        "u": 2.8391,
        "shgc": 0.4,
        "vt": 0.44,
    },
}


def _model(openings, oc=None):
    return BEMModel(
        building_name="Openings IFC",
        spaces=[BEMSpace("sp-1", "OFFICE 101", "101", list(RING), 200.0, 600.0)],
        openings=openings,
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
        opening_constructions=oc if oc is not None else dict(OC),
    )


def _by_tag(f, cls):
    return {e.Name.split()[0]: e for e in f.by_type(cls)}


def test_window_door_and_skylight_carry_u_shgc_and_vt(tmp_path):
    m = _model(
        [
            BEMOpeningUnit("window", "A", 1.2, 1.5, construction_id="t55-window"),
            BEMOpeningUnit("door", "D", 0.9, 2.1, construction_id="t55-door"),
            BEMOpeningUnit("skylight", "K", 1.0, 1.0, construction_id="t55-skylight"),
        ]
    )
    path = write_ifc4(m, tmp_path / "o.ifc")
    ok, errors = validate_ifc4(path)
    assert ok, errors
    f = ifcopenshell.open(str(path))
    wins = _by_tag(f, "IfcWindow")
    door = _by_tag(f, "IfcDoor")["D"]

    wp = _El.get_psets(wins["A"])
    assert wp["Pset_WindowCommon"]["ThermalTransmittance"] == pytest.approx(2.0442)
    assert wp["Pset_WindowCommon"]["Reference"] == "t55-window"
    assert wp["Pset_DoorWindowGlazingType"]["SolarHeatGainTransmittance"] == pytest.approx(0.38)
    assert wp["Pset_DoorWindowGlazingType"]["VisibleLightTransmittance"] == pytest.approx(0.418)

    sp = _El.get_psets(wins["K"])
    assert sp["Pset_WindowCommon"]["ThermalTransmittance"] == pytest.approx(2.8391)
    assert sp["Pset_DoorWindowGlazingType"]["VisibleLightTransmittance"] == pytest.approx(0.44)

    dp = _El.get_psets(door)
    assert dp["Pset_DoorCommon"]["ThermalTransmittance"] == pytest.approx(2.101)
    assert "Pset_DoorWindowGlazingType" not in dp
    assert any("3 window/door/skylight U-value(s)" in n for n in m.notes)


def test_openings_without_a_construction_get_no_thermal_psets(tmp_path):
    m = _model(
        [
            BEMOpeningUnit("window", "A", 1.2, 1.5),
            BEMOpeningUnit("door", "D", 0.9, 2.1, construction_id="missing"),
        ],
        oc={},
    )
    path = write_ifc4(m, tmp_path / "o.ifc")
    ok, errors = validate_ifc4(path)
    assert ok, errors
    f = ifcopenshell.open(str(path))
    for e in f.by_type("IfcWindow") + f.by_type("IfcDoor"):
        ps = _El.get_psets(e)
        assert "Pset_WindowCommon" not in ps and "Pset_DoorCommon" not in ps
        assert "Pset_DoorWindowGlazingType" not in ps
    assert not any("window/door/skylight U-value" in n for n in m.notes)
