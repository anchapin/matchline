"""Space conditioning category per 90.1-2019 Section 3.2 (#747)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import space_conditioning as sc  # noqa: E402

A = 1000.0  # ft2: Btu/h / A gives Btu/h-ft2 directly x 1000


def cat(**kw):
    return sc.classify_space(A, **kw)


# ---- units --------------------------------------------------------------


@pytest.mark.parametrize(
    "value,unit,btuh",
    [
        (12.0, "MBH", 12000.0),
        (12000.0, "BTUH", 12000.0),
        (12000.0, "Btu/h", 12000.0),
        (3.0, "tons", 36000.0),
        (1.0, "kW", 3412.141633),
        (100.0, "W", 341.2141633),
    ],
)
def test_capacity_units_convert_to_btuh(value, unit, btuh):
    assert sc.to_btuh(value, unit) == pytest.approx(btuh)


@pytest.mark.parametrize("unit", ["", "cfm", "hp", "gpm"])
def test_an_unstated_or_unknown_capacity_unit_is_not_guessed(unit):
    with pytest.raises(ValueError):
        sc.to_btuh(10.0, unit)


# ---- Table 3.2 ------------------------------------------------------------


@pytest.mark.parametrize(
    "cz,th",
    [
        ("0A", (5.0,)),
        ("1", (5.0,)),
        ("2B", (5.0,)),
        ("3A", (9.0,)),
        ("3B", (9.0,)),
        ("3C", (7.0,)),
        ("3", (7.0, 9.0)),
        ("4A", (10.0,)),
        ("4b", (10.0,)),
        ("4C", (8.0,)),
        ("4", (8.0, 10.0)),
        ("5A", (12.0,)),
        ("CZ6", (14.0,)),
        ("7", (16.0,)),
        ("8", (19.0,)),
        ("", (5.0, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0, 16.0, 19.0)),
    ],
)
def test_table_3_2_thresholds(cz, th):
    assert sc.heated_thresholds(cz) == th


@pytest.mark.parametrize("cz", ["9", "4D", "hot", "A4"])
def test_text_that_is_not_a_climate_zone_is_refused(cz):
    with pytest.raises(ValueError):
        sc.heated_thresholds(cz)
    r = cat(sensible_cooling_btuh=0, heating_btuh=20000, climate_zone=cz, indirect=False)
    assert r.category == "review" and "climate zone" in r.reasons[0]


# ---- cooled: sensible > 3.4 -------------------------------------------------


def test_cooled_just_above_3_4_is_conditioned():
    r = cat(sensible_cooling_btuh=3401, heating_btuh=0, climate_zone="4A")
    assert (r.category, r.cooled) == ("conditioned", True)


def test_exactly_3_4_sensible_is_not_cooled():
    r = cat(sensible_cooling_btuh=3400, heating_btuh=0, climate_zone="4A", indirect=False)
    assert r.cooled is False and r.category == "unconditioned"


def test_total_cooling_alone_never_makes_a_space_cooled():
    r = cat(total_cooling_btuh=50000, heating_btuh=0, climate_zone="4A", indirect=False)
    assert r.category == "review" and r.cooled is None
    assert any("only total cooling" in s for s in r.reasons)


def test_total_cooling_is_moot_when_heating_already_conditions_the_space():
    r = cat(total_cooling_btuh=50000, heating_btuh=12000, climate_zone="4A")
    assert (r.category, r.heated) == ("conditioned", True)


# ---- heated: at or above Table 3.2 ------------------------------------------


@pytest.mark.parametrize(
    "cz,th",
    [
        ("1A", 5),
        ("3A", 9),
        ("3C", 7),
        ("4A", 10),
        ("4C", 8),
        ("5B", 12),
        ("6A", 14),
        ("7", 16),
        ("8", 19),
    ],
)
def test_heated_at_the_table_value_and_not_just_below(cz, th):
    at = cat(sensible_cooling_btuh=0, heating_btuh=th * A, climate_zone=cz)
    assert (at.category, at.heated, at.heated_threshold) == ("conditioned", True, th)
    below = cat(sensible_cooling_btuh=0, heating_btuh=th * A - 1, climate_zone=cz, indirect=False)
    assert below.heated is False and below.category == "semiheated"


def test_without_a_climate_zone_heating_settles_only_at_the_extremes():
    hot = cat(sensible_cooling_btuh=0, heating_btuh=19000)
    assert (hot.category, hot.heated) == ("conditioned", True)
    low = cat(sensible_cooling_btuh=0, heating_btuh=3000, indirect=False)
    assert (low.category, low.heated) == ("unconditioned", False)
    mid = cat(sensible_cooling_btuh=0, heating_btuh=10000, indirect=False)
    assert mid.category == "review" and mid.heated is None
    assert any("depends on the climate zone" in s for s in mid.reasons)


def test_a_letterless_zone_settles_when_both_readings_agree():
    # CZ 3: 3C needs 7, 3A/3B need 9
    assert cat(sensible_cooling_btuh=0, heating_btuh=9000, climate_zone="3").heated is True
    assert cat(sensible_cooling_btuh=0, heating_btuh=6999, climate_zone="3").heated is False
    assert cat(sensible_cooling_btuh=0, heating_btuh=8000, climate_zone="3").heated is None


# ---- semiheated, unconditioned, indirect ------------------------------------


def test_semiheated_at_3_4_and_unconditioned_below():
    semi = cat(sensible_cooling_btuh=0, heating_btuh=3400, climate_zone="5A", indirect=False)
    assert (semi.category, semi.semiheated) == ("semiheated", True)
    un = cat(sensible_cooling_btuh=0, heating_btuh=3399, climate_zone="5A", indirect=False)
    assert (un.category, un.semiheated) == ("unconditioned", False)


def test_no_hvac_at_all_is_unconditioned_once_indirect_is_ruled_out():
    r = cat(sensible_cooling_btuh=0, heating_btuh=0, climate_zone="5A", indirect=False)
    assert r.category == "unconditioned"


def test_indirectly_conditioned_space_is_conditioned():
    r = cat(sensible_cooling_btuh=0, heating_btuh=3400, climate_zone="5A", indirect=True)
    assert (r.category, r.semiheated) == ("conditioned", False)
    assert "indirectly conditioned" in r.reasons


def test_an_unrun_indirect_test_leaves_a_not_heated_not_cooled_space_in_review():
    for heat in (0, 3400):
        r = cat(sensible_cooling_btuh=0, heating_btuh=heat, climate_zone="5A")
        assert r.category == "review" and r.semiheated is None
        assert "indirectly conditioned test not run" in r.reasons


# ---- unknowns go to review, never a default ----------------------------------


def test_unknown_cooling_or_heating_is_review_unless_the_other_conditions_it():
    assert cat(heating_btuh=0, climate_zone="4A", indirect=False).category == "review"
    assert cat(sensible_cooling_btuh=0, climate_zone="4A", indirect=False).category == "review"
    assert cat(sensible_cooling_btuh=5000, climate_zone="4A").category == "conditioned"
    assert cat(heating_btuh=12000, climate_zone="4A").category == "conditioned"
    assert cat().category == "review"


@pytest.mark.parametrize("area", [None, 0, -5])
def test_no_floor_area_is_review(area):
    r = sc.classify_space(area, sensible_cooling_btuh=5000, heating_btuh=0, climate_zone="4A")
    assert r.category == "review" and r.reasons == ["no floor area"]


def test_negative_output_is_review():
    r = cat(sensible_cooling_btuh=-1, heating_btuh=0, climate_zone="4A")
    assert r.category == "review" and "negative" in r.reasons[0]


def test_result_records_the_per_area_values_and_round_trips():
    r = cat(sensible_cooling_btuh=2000, heating_btuh=12500, climate_zone="5A")
    d = r.to_dict()
    assert d["category"] == "conditioned" and d["category"] in sc.CATEGORIES
    assert d["cooling_btuh_ft2"] == pytest.approx(2.0)
    assert d["heating_btuh_ft2"] == pytest.approx(12.5)
    assert d["heated_threshold"] == 12.0 and d["cooled"] is False and d["heated"] is True


def test_metric_area_converts_with_ft2_per_m2():
    assert 10.0 * sc.FT2_PER_M2 == pytest.approx(107.639, rel=1e-4)
