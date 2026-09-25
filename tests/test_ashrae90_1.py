"""Tests for ASHRAE 90.1-2019 compliance checking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from building_model import (
    BuildingModel,
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
# Wall U-factor tests
# ---------------------------------------------------------------------------


class TestWallUFactor:
    def test_wall_u_compliant(self):
        model = make_model()
        model.climate_zone = "5A"
        model.building_type = "other"
        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        wall.u_factor = 0.30  # Btu/h·ft²·°F (below 0.40 limit for 5A)
        model.envelope = [wall]

        result = _check_wall_u_factor(_ctx(model))
        assert result.check_id == "ashrae_wall_u_factor"
        assert result.severity == "pass"

    def test_wall_u_non_compliant(self):
        model = make_model()
        model.climate_zone = "5A"
        model.building_type = "other"
        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        wall.u_factor = 0.60  # Btu/h·ft²·°F (above 0.40 limit)
        model.envelope = [wall]

        result = _check_wall_u_factor(_ctx(model))
        assert result.check_id == "ashrae_wall_u_factor"
        assert result.severity == "error"
        assert "exceeds maximum" in result.message

    def test_wall_u_no_u_factor_skips(self):
        model = make_model()
        model.climate_zone = "5A"
        model.building_type = "other"
        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        model.envelope = [wall]

        result = _check_wall_u_factor(_ctx(model))
        assert result.check_id == "ashrae_wall_u_factor"
        assert result.severity == "skip"

    def test_wall_u_cold_climate_stringent(self):
        model = make_model()
        model.climate_zone = "6A"
        model.building_type = "other"
        # 6A max = 0.35 Btu/h·ft²·°F
        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        wall.u_factor = 0.36  # above 0.35 -> fail
        model.envelope = [wall]

        result = _check_wall_u_factor(_ctx(model))
        assert result.severity == "error"

        wall.u_factor = 0.34  # below 0.35 -> pass
        result = _check_wall_u_factor(_ctx(model))
        assert result.severity == "pass"


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
        assert result.severity == "error"
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
        assert result.severity == "error"


# ---------------------------------------------------------------------------
# Roof U-factor tests
# ---------------------------------------------------------------------------


class TestRoofUFactor:
    def test_roof_u_compliant(self):
        model = make_model()
        model.climate_zone = "5A"
        model.roof_u_factor = 0.25

        result = _check_roof_u_factor(_ctx(model))
        assert result.severity == "pass"

    def test_roof_u_non_compliant(self):
        model = make_model()
        model.climate_zone = "5A"
        model.roof_u_factor = 0.50

        result = _check_roof_u_factor(_ctx(model))
        assert result.severity == "error"

    def test_roof_u_no_data_skips(self):
        model = make_model()
        model.climate_zone = "5A"

        result = _check_roof_u_factor(_ctx(model))
        assert result.severity == "skip"


# ---------------------------------------------------------------------------
# Window U-factor tests
# ---------------------------------------------------------------------------


class TestWindowUFactor:
    def _window(self, wid: str, u_factor: float, shgc: float) -> Any:
        @dataclass
        class WindowStub:
            id: str
            u_factor: float
            shgc: float

        return WindowStub(id=wid, u_factor=u_factor, shgc=shgc)

    def test_window_compliant(self):
        model = make_model()
        model.climate_zone = "5A"
        model.windows = [self._window("WIN1", 0.35, 0.35)]

        result = _check_window_u_factor(_ctx(model))
        assert result.severity == "pass"

    def test_window_u_above_limit(self):
        model = make_model()
        model.climate_zone = "5A"
        model.windows = [self._window("WIN1", 0.60, 0.30)]

        result = _check_window_u_factor(_ctx(model))
        assert result.severity == "error"
        assert "U-factor" in result.message

    def test_window_shgc_above_limit(self):
        model = make_model()
        model.climate_zone = "5A"
        model.windows = [self._window("WIN1", 0.30, 0.60)]

        result = _check_window_u_factor(_ctx(model))
        assert result.severity == "error"
        assert "SHGC" in result.message

    def test_window_no_windows_skips(self):
        model = make_model()
        model.climate_zone = "5A"

        result = _check_window_u_factor(_ctx(model))
        assert result.severity == "skip"


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
# Mixed compliance report
# ---------------------------------------------------------------------------


class TestComplianceReport:
    def test_mixed_compliance_all_pass(self):
        model = make_model()
        model.climate_zone = "5A"
        model.building_type = "other"

        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        wall.u_factor = 0.30
        model.envelope = [wall]

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
        model.spaces = {"L1-101": space}

        model.roof_u_factor = 0.25
        model.windows = []

        report = compliance_report(_ctx(model))
        checks = {r.check_id: r for r in report}
        assert checks["ashrae_wall_u_factor"].severity == "pass"
        assert checks["ashrae_roof_u_factor"].severity == "pass"
        assert checks["ashrae_lighting_power_density"].severity == "pass"
        assert checks["ashrae_window_u_factor"].severity == "skip"

    def test_mixed_compliance_some_fail(self):
        model = make_model()
        model.climate_zone = "5A"
        model.building_type = "other"

        wall = EnvelopeWall(
            id="W1",
            facade="north",
            from_m=[0, 0],
            to_m=[10, 0],
            length_m=10.0,
            height_m=H,
            area_m2=10.0 * H,
            provenance=_provenance(),
        )
        wall.u_factor = 0.70  # fail
        model.envelope = [wall]

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
        space.lighting = SpaceLighting(lpd_w_m2=8.0)  # pass
        model.spaces = {"L1-101": space}

        model.roof_u_factor = 0.25  # pass
        model.windows = []

        report = compliance_report(_ctx(model))
        checks = {r.check_id: r for r in report}
        assert checks["ashrae_wall_u_factor"].severity == "error"
        assert checks["ashrae_lighting_power_density"].severity == "pass"
