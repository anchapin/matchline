"""IFC import reads window, door and skylight U/SHGC/VT (#787)."""

from __future__ import annotations

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.api.pset as _Ps  # noqa: E402
import ifcopenshell.util.element as _El  # noqa: E402

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4  # noqa: E402
from construction_library import apply_construction_library  # noqa: E402
from constructions import opening_constructions  # noqa: E402
from ifc_import import import_ifc  # noqa: E402

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


def _export(tmp_path, oc=None):
    m = BEMModel(
        building_name="Openings RT",
        spaces=[BEMSpace("sp-1", "OFFICE 101", "101", list(RING), 200.0, 600.0)],
        openings=[
            BEMOpeningUnit("window", "A", 1.2, 1.5, construction_id="t55-window"),
            BEMOpeningUnit("door", "D", 0.9, 2.1, construction_id="t55-door"),
            BEMOpeningUnit("skylight", "K", 1.0, 1.0, construction_id="t55-skylight"),
        ],
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
        opening_constructions=dict(OC) if oc is None else oc,
    )
    return write_ifc4(m, tmp_path / "rt.ifc")


def _openings(bm):
    return {op.category: op for sp in bm.spaces.values() for op in sp.openings}


def test_round_trip_keeps_opening_u_shgc_and_vt(tmp_path):
    bm = import_ifc(_export(tmp_path))
    ops = _openings(bm)
    assert set(ops) >= {"window", "door", "skylight"}
    win = bm.constructions[ops["window"].construction_id]
    door = bm.constructions[ops["door"].construction_id]
    sky = bm.constructions[ops["skylight"].construction_id]
    assert win.u_value_w_m2k == pytest.approx(2.0442)
    assert (win.shgc, win.vt) == (pytest.approx(0.38), pytest.approx(0.418))
    assert door.u_value_w_m2k == pytest.approx(2.101) and door.shgc is None
    assert sky.u_value_w_m2k == pytest.approx(2.8391)
    assert (sky.shgc, sky.vt) == (pytest.approx(0.4), pytest.approx(0.44))
    # stated values, kept apart from the library's own t55-* ids
    assert win.provenance.confidence == 0.9
    assert win.provenance.method == "ifc_import:tier0:opening_u"
    assert "Reference 't55-window'" in win.provenance.note
    assert not any(c.startswith("t55-") for c in bm.constructions)
    # gbXML gets them through the same map the exporter reads
    oc = opening_constructions(bm)
    assert oc[ops["skylight"].construction_id]["vt"] == pytest.approx(0.44)


def test_table_5_5_never_overwrites_stated_openings(tmp_path):
    bm = import_ifc(_export(tmp_path))
    before = {c: op.construction_id for c, op in _openings(bm).items()}
    s = apply_construction_library(bm, climate_zone="5A")
    after = {c: op.construction_id for c, op in _openings(bm).items()}
    assert before == after
    for cid in ("t55-window", "t55-door", "t55-skylight"):
        assert cid not in s.defaulted


def test_partial_and_bad_values(tmp_path):
    path = _export(tmp_path)
    f = ifcopenshell.open(str(path))
    for w in f.by_type("IfcWindow"):
        gp = _El.get_psets(w)["Pset_DoorWindowGlazingType"]
        pset = f.by_id(gp["id"])
        # SHGC above 1 is ignored, VT removed: U alone survives
        _Ps.edit_pset(
            f,
            pset=pset,
            properties={"SolarHeatGainTransmittance": 1.5, "VisibleLightTransmittance": None},
        )
    for d in f.by_type("IfcDoor"):
        cp = f.by_id(_El.get_psets(d)["Pset_DoorCommon"]["id"])
        _Ps.edit_pset(f, pset=cp, properties={"ThermalTransmittance": -1.0})
    f.write(str(path))
    bm = import_ifc(path)
    ops = _openings(bm)
    win = bm.constructions[ops["window"].construction_id]
    assert win.u_value_w_m2k == pytest.approx(2.0442)
    assert win.shgc is None and win.vt is None
    assert ops["door"].construction_id == ""  # non-positive U ignored


def test_openings_without_thermal_psets_have_no_construction(tmp_path):
    bm = import_ifc(_export(tmp_path, oc={}))
    for op in _openings(bm).values():
        assert op.construction_id == ""
