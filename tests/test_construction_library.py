"""Cited construction library (#747): ASHRAE 90.1-2019 Table 5.5 by climate zone."""

from __future__ import annotations

import pytest

from building_model import BuildingModel, Construction, EnvelopeWall, Space
from construction_library import (
    METHOD,
    apply_construction_library,
    classify,
    climate_zone_number,
    lookup,
)
from construction_library_data import CONSTRUCTION_LIBRARY
from convention_report import build_convention_report


def _model(**cons) -> BuildingModel:
    m = BuildingModel(name="t")
    m.spaces["S1"] = Space(id="S1", level_id="L1", name="Office")
    for cid, (name, u) in cons.items():
        m.constructions[cid] = Construction(id=cid, name=name, u_value_w_m2k=u)
    m.envelope.append(
        EnvelopeWall(
            id="w1",
            facade="south",
            length_m=10.0,
            height_m=3.0,
            space_id="S1",
            construction_id=next(iter(cons), ""),
        )
    )
    return m


def test_table_rows_are_verbatim_with_si_conversion():
    # spot checks against openstudio-standards v0.8.6 construction_properties
    r = CONSTRUCTION_LIBRARY[("Nonresidential", "4", "ExteriorWall", "Mass")]
    assert r["u_ip"] == 0.104 and r["u_si"] == pytest.approx(0.104 * 5.678263, abs=1e-4)
    assert CONSTRUCTION_LIBRARY[("Nonresidential", "4", "ExteriorRoof", "IEAD")]["u_ip"] == 0.032
    assert len({k[1] for k in CONSTRUCTION_LIBRARY}) == 9  # zones 0-8


@pytest.mark.parametrize(
    "cz,want", [("4A", "4"), ("5", "5"), ("cz 7", "7"), (" 3c ", "3"), ("0B", "0")]
)
def test_climate_zone_number(cz, want):
    assert climate_zone_number(cz) == want


@pytest.mark.parametrize("bad", ["", "9A", "4D", "north"])
def test_climate_zone_rejects_bad(bad):
    with pytest.raises(ValueError):
        climate_zone_number(bad)


@pytest.mark.parametrize(
    "text,surface,want",
    [
        ('W-1 8" CMU', "ExteriorWall", "Mass"),
        ("W-2 brick veneer on 6in metal stud", "ExteriorWall", "SteelFramed"),
        ("W-3 2x6 stud wood framing", "ExteriorWall", "WoodFramed"),
        ("W-4 insulated metal panel", "ExteriorWall", "Metal Building"),
        ("R-1 rigid insulation above deck", "ExteriorRoof", "IEAD"),
        ("R-2 standing seam", "ExteriorRoof", "Metal Building"),
        ("EXT-1", "ExteriorWall", None),
        ("steel stud and wood stud", "ExteriorWall", None),  # ambiguous
    ],
)
def test_classify(text, surface, want):
    assert classify(text, surface)[0] == want


def test_fills_unset_wall_with_provenance_and_rolls_up():
    m = _model(**{"W-1": ("8in CMU", None)})
    s = apply_construction_library(m, "4A")
    c = m.constructions["W-1"]
    assert c.u_value_w_m2k == pytest.approx(0.5905, abs=1e-4)
    assert c.provenance.method == METHOD
    assert "Table 5.5-4" in c.provenance.note and "4A" in c.provenance.note
    assert s.resolved["W-1"]["construction_type"] == "Mass"
    assert m.spaces["S1"].wall_u_value_w_m2k == pytest.approx(0.5905, abs=1e-4)


def test_stated_value_never_overwritten():
    m = _model(**{"W-1": ("8in CMU", 0.33)})
    s = apply_construction_library(m, "4A")
    assert m.constructions["W-1"].u_value_w_m2k == 0.33
    assert s.kept == ["W-1"] and not s.resolved


def test_no_climate_zone_fills_nothing_and_reports():
    m = _model(**{"W-1": ("8in CMU", None)})
    s = apply_construction_library(m, "")
    assert m.constructions["W-1"].u_value_w_m2k is None
    assert s.not_run and s.unmatched["W-1"] == "no climate zone given"


def test_unmatched_and_slab_are_reported_not_filled():
    m = _model(**{"EXT-1": ("", None), "SLAB": ("slab on grade", None)})
    m.slab_construction_id = "SLAB"
    s = apply_construction_library(m, "5A")
    assert m.constructions["EXT-1"].u_value_w_m2k is None
    assert m.constructions["SLAB"].u_value_w_m2k is None
    assert set(s.unmatched) == {"EXT-1", "SLAB"}


def test_roof_resolves_as_roof():
    m = _model(**{"R-1": ("insulation entirely above deck", None)})
    m.roof_construction_id = "R-1"
    apply_construction_library(m, "5A")
    want = lookup("ExteriorRoof", "IEAD", "5A").u_si
    assert m.constructions["R-1"].u_value_w_m2k == want


def test_category_changes_row_and_bad_category_rejected():
    res = lookup("ExteriorWall", "Mass", "6", "Residential")
    non = lookup("ExteriorWall", "Mass", "6", "Nonresidential")
    assert res is not None and non is not None
    with pytest.raises(ValueError):
        apply_construction_library(_model(), "4A", "Industrial")


def test_convention_report_carries_constructions():
    m = _model(**{"W-1": ("8in CMU", None), "EXT-1": ("", None)})
    s = apply_construction_library(m, "4A")
    rep = build_convention_report(m, constructions=s)["constructions"]
    assert "W-1" in rep["resolved"] and "EXT-1" in rep["unmatched"]
    assert "constructions" not in build_convention_report(_model(**{"W-1": ("8in CMU", None)}))


def test_baseline_defaults_fill_unnamed_walls_and_roof():
    """#768/#781: no assembly named at all -> Table 5.5 code maximum, cited."""
    m = _model()
    s = apply_construction_library(m, "5A")
    wall = lookup("ExteriorWall", "SteelFramed", "5A")
    roof = lookup("ExteriorRoof", "IEAD", "5A")
    assert m.envelope[0].construction_id == "t55-wall"
    assert m.constructions["t55-wall"].u_value_w_m2k == wall.u_si
    assert m.roof_construction_id == "t55-roof"
    assert m.constructions["t55-roof"].u_value_w_m2k == roof.u_si
    for cid in ("t55-wall", "t55-roof"):
        prov = m.constructions[cid].provenance
        assert (
            prov.method == METHOD
            and "Table 5.5-5" in prov.note
            and "no assembly stated" in prov.note
        )
    assert s.defaulted["t55-wall"]["segments"] == ["w1"]
    assert m.spaces["S1"].wall_u_value_w_m2k == pytest.approx(wall.u_si)


def test_baseline_defaults_need_a_climate_zone():
    m = _model()
    s = apply_construction_library(m, "")
    assert not s.defaulted and not m.constructions
    assert m.envelope[0].construction_id == "" and m.roof_construction_id == ""


def test_baseline_defaults_never_replace_a_named_assembly():
    m = _model(**{"EXT-1": ("", None), "R-1": ("roof", 0.25)})
    m.roof_construction_id = "R-1"
    s = apply_construction_library(m, "5A")
    assert not s.defaulted
    assert m.envelope[0].construction_id == "EXT-1"
    assert m.constructions["R-1"].u_value_w_m2k == 0.25


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _slab_model(**spaces):
    from building_model import Level

    m = BuildingModel(name="t")
    m.levels = [
        Level(id="L1", name="L1", elevation_z_m=0.0),
        Level(id="L2", name="L2", elevation_z_m=4.0),
    ]
    for sid, (lid, poly) in spaces.items():
        m.spaces[sid] = Space(id=sid, level_id=lid, name=sid, polygon_m=poly)
    return m


def test_unset_slab_gets_unheated_f_factor_as_effective_u():
    """#747/#781: Table 5.5 unheated slab F-factor, U = F x P / A on the exported footprint."""
    m = _slab_model(A=("L1", _rect(0, 0, 10, 20)), B=("L1", _rect(10, 0, 20, 20)))
    s = apply_construction_library(m, "5A")
    row = lookup("GroundContactFloor", "Unheated", "5A")
    assert row.f_ip is not None and row.u_si is None
    want = row.f_ip * 1.730735 * 80.0 / 400.0  # 20 x 20 footprint: P 80 m, A 400 m2
    assert m.slab_construction_id == "t55-slab"
    assert m.constructions["t55-slab"].u_value_w_m2k == pytest.approx(want, rel=1e-5)
    d = s.defaulted["t55-slab"]
    assert d["exposed_perimeter_m"] == pytest.approx(80.0) and d["area_m2"] == pytest.approx(400.0)
    note = m.constructions["t55-slab"].provenance.note
    assert (
        "Table 5.5 unheated slab" in note
        and "Table 5.5-5" in note
        and "F x exposed perimeter" in note
    )


def test_slab_uses_the_lowest_level_only():
    m = _slab_model(G=("L1", _rect(0, 0, 10, 10)), U=("L2", _rect(0, 0, 30, 30)))
    s = apply_construction_library(m, "5A")
    assert s.defaulted["t55-slab"]["area_m2"] == pytest.approx(100.0)
    assert s.defaulted["t55-slab"]["exposed_perimeter_m"] == pytest.approx(40.0)


def test_stated_slab_never_replaced_and_no_zone_no_slab():
    m = _slab_model(G=("L1", _rect(0, 0, 10, 10)))
    m.constructions["S"] = Construction(id="S", name="slab", u_value_w_m2k=0.5)
    m.slab_construction_id = "S"
    s = apply_construction_library(m, "5A")
    assert "t55-slab" not in s.defaulted and m.constructions["S"].u_value_w_m2k == 0.5
    m2 = _slab_model(G=("L1", _rect(0, 0, 10, 10)))
    apply_construction_library(m2, "")
    assert m2.slab_construction_id == "" and "t55-slab" not in m2.constructions


def test_slab_without_polygons_is_reported_not_filled():
    m = _model()
    s = apply_construction_library(m, "5A")
    assert m.slab_construction_id == "" and "t55-slab" not in m.constructions
    assert "perimeter and area" in s.unmatched["t55-slab"]


def test_slab_u_reaches_the_exporters():
    from constructions import slab_u_value

    m = _slab_model(G=("L1", _rect(0, 0, 10, 10)))
    apply_construction_library(m, "5A")
    assert slab_u_value(m) == pytest.approx(m.constructions["t55-slab"].u_value_w_m2k)


# --- #781: Appendix G baseline envelope from Table G3.4 (PRM 2019) ------------


def _baseline_model(window_m2: float = 0.0, skylight_m2: float = 0.0):
    from building_model import SpaceOpening

    m = _slab_model(A=("L1", _rect(0, 0, 20, 20)))
    m.envelope.append(EnvelopeWall(id="w1", facade="south", length_m=80.0, height_m=4.0))
    sp = m.spaces["A"]
    if window_m2:
        sp.openings.append(
            SpaceOpening(id="o1", tag="W1", category="window", width_m=1.0, height_m=window_m2)
        )
    if skylight_m2:
        sp.openings.append(
            SpaceOpening(id="o2", tag="S1", category="skylight", width_m=1.0, height_m=skylight_m2)
        )
    return m


def test_prm_rows_are_table_g34_not_table_55():
    from construction_library import prm_rows

    wall, _ = prm_rows("ExteriorWall", "SteelFramed", "5A", "Nonresidential")
    roof, _ = prm_rows("ExteriorRoof", "IEAD", "5A", "Nonresidential")
    slab, _ = prm_rows("GroundContactFloor", "Unheated", "5A", "Nonresidential")
    assert wall[0]["u_ip"] == 0.084 and wall[0]["u_si"] == pytest.approx(0.084 * 5.678263, abs=1e-4)
    assert roof[0]["u_ip"] == 0.063 and slab[0]["f_ip"] == 0.73
    assert lookup("ExteriorWall", "SteelFramed", "5A").u_ip == 0.055  # Table 5.5 is stricter


def test_baseline_recorded_but_proposed_keeps_table_55():
    m = _baseline_model(window_m2=32.0)  # 32 / 320 m2 wall = 10% WWR
    s = apply_construction_library(m, "5A")
    b = s.baseline["surfaces"]
    assert b["wall"]["u_ip"] == 0.084 and b["roof"]["u_ip"] == 0.063
    assert m.constructions["t55-wall"].u_value_w_m2k == pytest.approx(
        lookup("ExteriorWall", "SteelFramed", "5A").u_si
    )
    assert b["slab"]["u_si_effective"] == pytest.approx(0.73 * 1.730735 * 80.0 / 400.0, rel=1e-5)
    assert s.to_dict()["baseline"]["source"].startswith("ASHRAE Standard 90.1-2019, Appendix G")


def test_baseline_glazing_band_follows_the_model_wwr():
    low = apply_construction_library(_baseline_model(window_m2=16.0), "5A")  # 5%
    mid = apply_construction_library(_baseline_model(window_m2=80.0), "5A")  # 25%
    high = apply_construction_library(_baseline_model(window_m2=160.0), "5A")  # 50%
    assert low.baseline["window_to_wall_pct"] == pytest.approx(5.0)
    assert low.baseline["surfaces"]["vertical_glazing"]["shgc"] == 0.49
    assert mid.baseline["surfaces"]["vertical_glazing"]["band_pct"] == [20.001, 30]
    g = high.baseline["surfaces"]["vertical_glazing"]
    assert g["band_pct"] == [30.001, 40] and "cap" in g["note"]
    assert g["u_ip"] == 0.57


def test_baseline_skylight_band_and_unknown_wwr():
    s = apply_construction_library(_baseline_model(skylight_m2=12.0), "5A")  # 3% of 400 m2
    assert s.baseline["skylight_to_roof_pct"] == pytest.approx(3.0)
    assert s.baseline["surfaces"]["skylight"]["shgc"] == 0.39
    m = _slab_model(A=("L1", _rect(0, 0, 20, 20)))  # no walls: WWR unknown
    s2 = apply_construction_library(m, "5A")
    assert "vertical_glazing" in s2.baseline["unmatched"]


def test_baseline_zone_3_uses_the_lettered_row_and_no_zone_records_nothing():
    from construction_library import prm_rows

    rows, zk = prm_rows("ExteriorWall", "SteelFramed", "3B", "Nonresidential")
    assert zk == "3B" and rows
    rows4, zk4 = prm_rows("ExteriorWall", "SteelFramed", "4A", "Nonresidential")
    assert zk4 == "4" and rows4[0]["u_ip"] == 0.124
    assert apply_construction_library(_baseline_model(), "").baseline == {}
