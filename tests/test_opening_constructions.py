"""Windows, doors and skylights get Table 5.5 values and export them (#747)."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from building_model import BuildingModel, Construction, Space, SpaceOpening
from construction_library import apply_construction_library
from constructions import opening_constructions

NS = {"g": "http://www.gbxml.org/schema"}


def _model() -> BuildingModel:
    m = BuildingModel(name="t")
    sp = Space(id="S1", level_id="L1", name="Office", polygon_m=[[0, 0], [10, 0], [10, 8], [0, 8]])
    sp.openings += [
        SpaceOpening(id="w1", tag="A", category="window", width_m=1.2, height_m=1.5),
        SpaceOpening(id="d1", tag="D", category="door", width_m=0.9, height_m=2.1),
        SpaceOpening(id="k1", tag="K", category="skylight", width_m=1.0, height_m=1.0),
        # interior door between two rooms: not part of the envelope
        SpaceOpening(
            id="d2", tag="D2", category="door", width_m=0.9, height_m=2.1, adjacent_space_id="S2"
        ),
    ]
    m.spaces["S1"] = sp
    return m


def test_exterior_openings_get_table_55_with_shgc():
    m = _model()
    s = apply_construction_library(m, "5A")
    ops = {op.id: op for op in m.spaces["S1"].openings}
    assert ops["w1"].construction_id == "t55-window"
    assert ops["d1"].construction_id == "t55-door"
    assert ops["k1"].construction_id == "t55-skylight"
    assert ops["d2"].construction_id == ""  # interior door untouched
    win, door, sky = (m.constructions[c] for c in ("t55-window", "t55-door", "t55-skylight"))
    # 90.1-2019 Table 5.5-5 nonresidential: fixed U 0.36 SHGC 0.38, swinging
    # door U 0.37, skylight U 0.50 SHGC 0.40
    assert win.u_value_w_m2k == pytest.approx(0.36 * 5.678263, abs=1e-3) and win.shgc == 0.38
    assert door.u_value_w_m2k == pytest.approx(0.37 * 5.678263, abs=1e-3) and door.shgc is None
    assert sky.u_value_w_m2k == pytest.approx(0.50 * 5.678263, abs=1e-3) and sky.shgc == 0.40
    assert "fixed class used" in win.provenance.note
    # VT at Table 5.5's minimum VT/SHGC (1.10) for vertical glazing; skylight
    # rows give no VT, so skylights take the PRM 2019 data's 1.10 ratio (#784)
    assert win.vt == pytest.approx(0.418) and sky.vt == pytest.approx(0.44)
    assert "ashrae_90_1_prm_2019" in sky.provenance.note
    assert s.defaulted["t55-skylight"]["vt"] == pytest.approx(0.44)
    assert s.defaulted["t55-window"]["openings"] == ["w1"]
    assert s.defaulted["t55-door"]["openings"] == ["d1"]


def test_stated_opening_construction_is_kept_and_no_zone_does_nothing():
    m = _model()
    m.constructions["W-STATED"] = Construction(
        id="W-STATED", name="Schedule glazing", u_value_w_m2k=1.7, shgc=0.25
    )
    m.spaces["S1"].openings[0].construction_id = "W-STATED"
    s = apply_construction_library(m, "5A")
    assert m.spaces["S1"].openings[0].construction_id == "W-STATED"
    assert "t55-window" not in s.defaulted  # the only window states its own
    assert "W-STATED" not in s.defaulted
    m2 = _model()
    apply_construction_library(m2, "")
    assert all(op.construction_id == "" for op in m2.spaces["S1"].openings)


def test_opening_constructions_map_for_exporters():
    m = _model()
    apply_construction_library(m, "4A")
    oc = opening_constructions(m)
    assert set(oc) == {"t55-window", "t55-door", "t55-skylight"}
    assert oc["t55-door"]["category"] == "door"
    assert oc["t55-window"]["shgc"] == m.constructions["t55-window"].shgc
    # an id with no construction behind it is left out
    m.spaces["S1"].openings[0].construction_id = "missing"
    assert "missing" not in opening_constructions(m)


def test_gbxml_writes_window_type_and_door_construction():
    space = BEMSpace(
        sid="S1",
        name="Office",
        number="1",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        area_m2=100.0,
        volume_m3=300.0,
        lighting_w=0.0,
    )
    openings = [
        BEMOpeningUnit("window", "A", 1.2, 1.5, construction_id="t55-window"),
        BEMOpeningUnit("door", "D", 0.9, 2.1, construction_id="t55-door"),
        BEMOpeningUnit("window", "B", 1.2, 1.5),  # no construction: no reference
        # glazing with no VT (e.g. a stated U and SHGC only): written
        # unreferenced, with a note
        BEMOpeningUnit("skylight", "K", 1.0, 1.0, construction_id="t55-skylight"),
    ]
    model = BEMModel(
        building_name="t",
        spaces=[space],
        openings=openings,
        ring_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.1,
        opening_constructions={
            "t55-window": {
                "name": "Window",
                "category": "window",
                "u": 2.0442,
                "shgc": 0.38,
                "vt": 0.418,
            },
            "t55-skylight": {
                "name": "Skylight",
                "category": "skylight",
                "u": 2.8391,
                "shgc": 0.4,
                "vt": None,
            },
            "t55-door": {"name": "Door", "category": "door", "u": 2.101, "shgc": None},
        },
    )
    with tempfile.TemporaryDirectory() as d:
        path = write_gbxml(model, Path(d) / "out.xml")
        ok, errors = validate_gbxml(path)
        assert ok, errors
        root = ET.parse(path).getroot()
    wt = root.find("g:WindowType[@id='t55-window']", NS)
    assert wt is not None
    assert float(wt.find("g:U-value", NS).text) == pytest.approx(2.0442)
    assert float(wt.find("g:SolarHeatGainCoeff", NS).text) == pytest.approx(0.38)
    vt = wt.find("g:Transmittance", NS)
    assert vt.get("type") == "Visible" and float(vt.text) == pytest.approx(0.418)
    assert root.find("g:WindowType[@id='t55-skylight']", NS) is None
    assert any("t55-skylight" in n for n in model.notes)
    door = root.find("g:Construction[@id='t55-door']", NS)
    assert door is not None and door.find("g:LayerId", NS) is not None
    ops = root.findall(".//g:Opening", NS)
    refs = sorted((o.get("windowTypeIdRef") or "", o.get("constructionIdRef") or "") for o in ops)
    assert refs == [("", ""), ("", ""), ("", "t55-door"), ("t55-window", "")]


def test_skylight_vt_exports_simple_glazing_windowtype():
    """A defaulted skylight now carries VT, so it exports a referenced WindowType (#784)."""
    from construction_library import SKYLIGHT_VT_SHGC

    assert SKYLIGHT_VT_SHGC == 1.10
    space = BEMSpace(
        sid="S1",
        name="Office",
        number="1",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        area_m2=100.0,
        volume_m3=300.0,
        lighting_w=0.0,
    )
    model = BEMModel(
        building_name="t",
        spaces=[space],
        openings=[BEMOpeningUnit("skylight", "K", 1.0, 1.0, construction_id="t55-skylight")],
        ring_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.1,
        opening_constructions={
            "t55-skylight": {
                "name": "Skylight",
                "category": "skylight",
                "u": 2.8391,
                "shgc": 0.4,
                "vt": 0.44,
            },
        },
    )
    with tempfile.TemporaryDirectory() as d:
        path = write_gbxml(model, Path(d) / "out.xml")
        ok, errors = validate_gbxml(path)
        assert ok, errors
        root = ET.parse(path).getroot()
    wt = root.find("g:WindowType[@id='t55-skylight']", NS)
    assert wt is not None
    assert float(wt.find("g:Transmittance", NS).text) == pytest.approx(0.44)
    refs = [o.get("windowTypeIdRef") for o in root.findall(".//g:Opening", NS)]
    assert "t55-skylight" in refs
    assert not any("t55-skylight" in n for n in model.notes)
