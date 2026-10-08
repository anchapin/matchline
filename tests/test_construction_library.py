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
    """#768: no assembly named at all -> Appendix G baseline classes, cited."""
    m = _model()
    s = apply_construction_library(m, "5A")
    wall = lookup("ExteriorWall", "SteelFramed", "5A")
    roof = lookup("ExteriorRoof", "IEAD", "5A")
    assert m.envelope[0].construction_id == "appg-wall"
    assert m.constructions["appg-wall"].u_value_w_m2k == wall.u_si
    assert m.roof_construction_id == "appg-roof"
    assert m.constructions["appg-roof"].u_value_w_m2k == roof.u_si
    for cid in ("appg-wall", "appg-roof"):
        prov = m.constructions[cid].provenance
        assert prov.method == METHOD and "Table 5.5-5" in prov.note and "G3.1-5(b)" in prov.note
    assert s.defaulted["appg-wall"]["segments"] == ["w1"]
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
