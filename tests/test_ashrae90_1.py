"""Tests for ASHRAE 90.1-2019 compliance checking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from building_model import (
    BuildingModel,
    Construction,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceHVAC,
    SpaceLighting,
    Zone,
)
from validate import _Ctx
from validate.ashrae90_1 import (
    _check_hvac_efficiency,
    _check_lighting_power_density,
    _check_roof_u_factor,
    _check_wall_u_factor,
    _check_window_u_factor,
    compliance_report,
)

H = 3.0


def _provenance() -> Provenance:
    return Provenance(sheet_id="TEST", revision=1, method="test", confidence=1.0)


def _ctx(model: BuildingModel) -> _Ctx:
    return _Ctx(model=model)


def make_model() -> BuildingModel:
    model = BuildingModel(name="test_bldg")
    model.levels.append(Level(id="L1", name="Level 1", wall_height_m=H))
    return model


# ---------------------------------------------------------------------------
# Lighting Power Density tests
# ---------------------------------------------------------------------------


class TestLightingPowerDensity:
    def _make_space(self, space_id: str, name: str, lpd_w_m2: float) -> Space:
        space = Space(
            id=space_id,
            level_id="L1",
            name=name,
            number="101",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=30.0 * H,
            core_provenance=_provenance(),
            label_confidence=1.0,
        )
        space.lighting = SpaceLighting(lpd_w_m2=lpd_w_m2)
        return space

    def test_lpd_compliant_office(self):
        """Office LPD below Table 9.5.1 max (0.98 W/ft² = 10.55 W/m²)."""
        model = make_model()
        model.spaces = {"L1-101": self._make_space("L1-101", "OFFICE", 8.0)}

        result = _check_lighting_power_density(_ctx(model))
        assert result.check_id == "ashrae_lighting_power_density"
        assert result.severity == "pass"

    def test_lpd_non_compliant_office(self):
        """Office LPD above Table 9.5.1 max fails."""
        model = make_model()
        model.spaces = {"L1-101": self._make_space("L1-101", "OFFICE", 15.0)}

        result = _check_lighting_power_density(_ctx(model))
        assert result.check_id == "ashrae_lighting_power_density"
        assert result.severity == "warn"
        assert "LPD violations" in result.message

    def test_lpd_classroom(self):
        """Classroom has its own limit (1.24 W/ft² = 13.3 W/m²)."""
        model = make_model()
        model.spaces = {"L1-101": self._make_space("L1-101", "CLASSROOM", 12.0)}

        result = _check_lighting_power_density(_ctx(model))
        assert result.severity == "pass"

    def test_lpd_unknown_type_uses_default(self):
        """Unrecognized type falls back to 'other' limit (1.00 W/ft² = 10.76 W/m²)."""
        model = make_model()
        model.spaces = {"L1-101": self._make_space("L1-101", "MY CUSTOM ROOM", 9.0)}

        result = _check_lighting_power_density(_ctx(model))
        assert result.severity == "pass"

    def test_lpd_multiple_violations(self):
        """Multiple violations are reported."""
        model = make_model()
        model.spaces = {
            "L1-101": self._make_space("L1-101", "OFFICE", 20.0),
            "L1-102": self._make_space("L1-102", "CLASSROOM", 25.0),
        }

        result = _check_lighting_power_density(_ctx(model))
        assert result.severity == "warn"


# ---------------------------------------------------------------------------
# HVAC efficiency tests
# ---------------------------------------------------------------------------


class TestHVACEfficiency:
    def _make_zone_space(
        self,
        *,
        equipment_type: str = "*",
        cooling_eer: float | None = None,
        heating_cop: float | None = None,
        cooling_capacity_kbtu: float = 50.0,
    ) -> tuple[Zone, Space]:
        zone = Zone(id="Z1", level_id="L1", space_ids=["L1-101"])
        space = Space(
            id="L1-101",
            level_id="L1",
            name="OFFICE",
            number="101",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=30.0 * H,
            core_provenance=_provenance(),
            label_confidence=1.0,
        )
        space.lighting = SpaceLighting(lpd_w_m2=10.0)
        space.hvac = SpaceHVAC(zone_ids=["Z1"])
        space.hvac.equipment_type = equipment_type  # type: ignore[attr-defined]
        space.hvac.cooling_eer = cooling_eer  # type: ignore[attr-defined]
        space.hvac.heating_cop = heating_cop  # type: ignore[attr-defined]
        space.hvac.cooling_capacity_kbtu = cooling_capacity_kbtu  # type: ignore[attr-defined]
        return zone, space

    def test_hvac_cooling_eer_compliant(self):
        model = make_model()
        zone, space = self._make_zone_space(
            equipment_type="residential_split",
            cooling_eer=12.0,
        )
        model.spaces = {"L1-101": space}
        model.zones = {"Z1": zone}

        result = _check_hvac_efficiency(_ctx(model))
        assert result.severity == "pass"

    def test_hvac_cooling_eer_below_minimum_fails(self):
        model = make_model()
        zone, space = self._make_zone_space(
            equipment_type="*",
            cooling_eer=10.0,
        )
        model.spaces = {"L1-101": space}
        model.zones = {"Z1": zone}

        result = _check_hvac_efficiency(_ctx(model))
        assert result.severity == "error"
        assert "EER" in result.message

    def test_hvac_heating_cop_below_minimum_fails(self):
        model = make_model()
        zone, space = self._make_zone_space(
            equipment_type="heat_pump",
            cooling_eer=12.0,
            heating_cop=2.5,
        )
        model.spaces = {"L1-101": space}
        model.zones = {"Z1": zone}

        result = _check_hvac_efficiency(_ctx(model))
        assert result.severity == "error"
        assert "COP" in result.message


# ---------------------------------------------------------------------------
# Envelope checks (#763): limits from construction_library_data.py, the
# ASHRAE 90.1-2019 Table 5.5 rows; the climate zone is never assumed
# ---------------------------------------------------------------------------


def _wall(wid="W1", cid="", u_factor=None) -> EnvelopeWall:
    w = EnvelopeWall(
        id=wid,
        facade="north",
        from_m=[0, 0],
        to_m=[10, 0],
        length_m=10.0,
        height_m=H,
        area_m2=10.0 * H,
        provenance=_provenance(),
        construction_id=cid,
    )
    if u_factor is not None:
        w.u_factor = u_factor
    return w


def _zoned(cz="4A", **cons) -> BuildingModel:
    m = make_model()
    m.climate_zone = cz
    for cid, (name, u_si) in cons.items():
        m.constructions[cid] = Construction(id=cid, name=name, u_value_w_m2k=u_si)
    return m


@dataclass
class WindowStub:
    id: str
    u_factor: Any = None
    shgc: Any = None
    operable: Any = None


class TestEnvelopeLimits:
    def test_mass_wall_4a_limit_is_0_104(self):
        m = _zoned("4A", **{"W-1": ("8in CMU", None)})
        m.envelope = [_wall("W1", "W-1", 0.10)]
        assert _check_wall_u_factor(_ctx(m)).severity == "pass"
        m.envelope = [_wall("W1", "W-1", 0.11)]
        r = _check_wall_u_factor(_ctx(m))
        assert r.severity == "error" and "0.104" in r.message and "Mass" in r.message

    def test_wall_u_from_construction_si(self):
        # 0.5905 W/m2K = 0.104 Btu/h-ft2-F, exactly the 4A mass limit
        m = _zoned("4A", **{"W-1": ("8in CMU", 0.5905)})
        m.envelope = [_wall("W1", "W-1")]
        assert _check_wall_u_factor(_ctx(m)).severity == "pass"
        m.constructions["W-1"].u_value_w_m2k = 0.70
        assert _check_wall_u_factor(_ctx(m)).severity == "error"

    def test_wall_uses_its_own_class(self):
        # 4A steel-framed limit is 0.064, tighter than mass 0.104
        m = _zoned("4A", **{"W-2": ("metal stud", None)})
        m.envelope = [_wall("W2", "W-2", 0.08)]
        r = _check_wall_u_factor(_ctx(m))
        assert r.severity == "error" and "SteelFramed" in r.message

    def test_unclassed_wall_checked_against_loosest_and_not_confirmed(self):
        m = _zoned("4A")
        m.envelope = [_wall("W1", "", 0.08)]  # below loosest (0.104), above steel
        r = _check_wall_u_factor(_ctx(m))
        assert r.severity == "warn" and "not confirmed" in r.message
        m.envelope = [_wall("W1", "", 0.30)]
        assert _check_wall_u_factor(_ctx(m)).severity == "error"

    def test_no_climate_zone_skips_never_assumes_5a(self):
        m = make_model()
        m.envelope = [_wall("W1", "", 5.0)]
        m.roof_u_factor = 5.0
        m.windows = [WindowStub("WIN1", 5.0, 0.9)]
        for check in (_check_wall_u_factor, _check_roof_u_factor, _check_window_u_factor):
            r = check(_ctx(m))
            assert r.severity == "skip" and "none is assumed" in r.message

    def test_bad_zone_and_category_skip(self):
        m = _zoned("9Z")
        m.envelope = [_wall("W1", "", 0.1)]
        assert _check_wall_u_factor(_ctx(m)).severity == "skip"
        m = _zoned("4A")
        m.building_category = "Industrial"
        m.envelope = [_wall("W1", "", 0.1)]
        assert _check_wall_u_factor(_ctx(m)).severity == "skip"

    def test_wall_without_u_skips(self):
        m = _zoned("5A")
        m.envelope = [_wall("W1")]
        assert _check_wall_u_factor(_ctx(m)).severity == "skip"

    def test_iead_roof_4a_limit_is_0_032(self):
        m = _zoned("4A", **{"R-1": ("insulation above deck", None)})
        m.roof_construction_id = "R-1"
        m.roof_u_factor = 0.030
        assert _check_roof_u_factor(_ctx(m)).severity == "pass"
        m.roof_u_factor = 0.25  # passed the old 0.282 table, fails Table 5.5-4
        r = _check_roof_u_factor(_ctx(m))
        assert r.severity == "error" and "0.032" in r.message

    def test_roof_from_construction(self):
        m = _zoned("5A", **{"R-1": ("standing seam metal building roof", 0.5)})
        m.roof_construction_id = "R-1"
        assert _check_roof_u_factor(_ctx(m)).severity == "error"  # 0.088 > 0.037

    def test_roof_no_data_skips(self):
        assert _check_roof_u_factor(_ctx(_zoned("5A"))).severity == "skip"

    def test_window_fixed_and_operable_rows(self):
        m = _zoned("4A")
        m.windows = [WindowStub("WIN1", 0.40, 0.30, operable=False)]  # fixed U max 0.36
        assert _check_window_u_factor(_ctx(m)).severity == "error"
        m.windows = [WindowStub("WIN1", 0.40, 0.30, operable=True)]  # operable U max 0.45
        assert _check_window_u_factor(_ctx(m)).severity == "pass"

    def test_window_shgc(self):
        m = _zoned("4A")
        m.windows = [WindowStub("WIN1", 0.30, 0.60, operable=False)]
        r = _check_window_u_factor(_ctx(m))
        assert r.severity == "error" and "SHGC" in r.message

    def test_window_unknown_type_not_confirmed(self):
        m = _zoned("4A")
        m.windows = [WindowStub("WIN1", 0.40, 0.30)]
        assert _check_window_u_factor(_ctx(m)).severity == "warn"

    def test_no_windows_skips(self):
        m = _zoned("5A")
        m.windows = []
        assert _check_window_u_factor(_ctx(m)).severity == "skip"

    def test_construction_library_sets_zone_for_validation(self):
        from construction_library import apply_construction_library

        m = _zoned("", **{"W-1": ("8in CMU", None)})
        m.envelope = [_wall("W1", "W-1")]
        apply_construction_library(m, "4A")
        assert m.climate_zone == "4A" and m.building_category == "Nonresidential"
        # the library fills the code maximum, which is exactly compliant
        assert _check_wall_u_factor(_ctx(m)).severity == "pass"

    def test_climate_zone_round_trips(self):
        m = _zoned("6B")
        m.building_category = "Semiheated"
        back = BuildingModel.from_json(m.to_json())
        assert back.climate_zone == "6B" and back.building_category == "Semiheated"


class TestComplianceReport:
    def test_report_runs_all_checks(self):
        m = _zoned("5A", **{"W-1": ("8in CMU", None)})
        m.envelope = [_wall("W1", "W-1", 0.70)]
        space = Space(
            id="L1-101",
            level_id="L1",
            name="OFFICE",
            number="101",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=30.0 * H,
            core_provenance=_provenance(),
            label_confidence=1.0,
        )
        space.lighting = SpaceLighting(lpd_w_m2=8.0)
        m.spaces = {"L1-101": space}
        m.windows = []
        checks = {r.check_id: r for r in compliance_report(_ctx(m))}
        assert checks["ashrae_wall_u_factor"].severity == "error"
        assert checks["ashrae_roof_u_factor"].severity == "skip"
        assert checks["ashrae_lighting_power_density"].severity == "pass"
        assert checks["ashrae_window_u_factor"].severity == "skip"
