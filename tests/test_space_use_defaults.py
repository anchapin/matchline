"""Tests for space_use_defaults.py (#685): DOE prototype space-use defaults."""

import pytest

import space_use_defaults as sud
import space_use_defaults_data as data
from building_model import BuildingModel, Provenance, Space, SpaceLighting, SpaceUse


def _model(*spaces):
    m = BuildingModel()
    for sp in spaces:
        m.spaces[sp.id] = sp
    return m


def _space(sid, name, area=20.0, poly_type="room"):
    return Space(id=sid, level_id="L1", name=name, area_m2=area, poly_type=poly_type)


class TestTable:
    def test_header_names_source_and_edition(self):
        assert data.SOURCE == "DOE Commercial Prototype Building Models"
        assert data.EDITION == "ASHRAE 90.1-2019"
        assert data.SOURCE_VERSION.startswith("openstudio-standards v")
        doc = data.__doc__
        assert "90.1-2019" in doc and "openstudio-standards" in doc

    def test_every_row_traces_to_source_and_converts_consistently(self):
        for key, row in data.SPACE_USE_DEFAULTS.items():
            assert row["source_building_type"] and row["source_space_type"], key
            if row["lighting_w_ft2"] is not None:
                assert row["lpd_w_m2"] == pytest.approx(row["lighting_w_ft2"] * 10.7639, abs=1e-3)
            if row["occupancy_per_1000ft2"] is not None:
                assert row["people_per_m2"] == pytest.approx(
                    row["occupancy_per_1000ft2"] / 1000 * 10.7639, abs=1e-5
                )
            if row["equipment_w_ft2"] is not None:
                assert row["equipment_w_m2"] == pytest.approx(
                    row["equipment_w_ft2"] * 10.7639, abs=1e-3
                )
            for s in ("lighting_schedule", "occupancy_schedule", "equipment_schedule"):
                if row[s]:
                    assert row[s] in data.SCHEDULES, (key, row[s])

    def test_open_office_matches_prototype_row(self):
        row = data.SPACE_USE_DEFAULTS["open_office"]
        assert (row["source_building_type"], row["source_space_type"]) == ("Office", "OpenOffice")
        assert (row["lighting_w_ft2"], row["occupancy_per_1000ft2"], row["equipment_w_ft2"]) == (
            0.61,
            5.25,
            0.71,
        )

    def test_schedules_are_24_hourly_fractions(self):
        for name, days in data.SCHEDULES.items():
            assert set(days) == {"weekday", "saturday", "sunday_holiday"}, name
            for vals in days.values():
                assert len(vals) == 24 and all(0.0 <= v <= 1.0 for v in vals), name
        assert sud.schedule_profile("OfficeLarge BLDG_OCC_SCH")["weekday"][9] == 0.95
        with pytest.raises(KeyError):
            sud.schedule_profile("nope")


class TestClassify:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("OPEN OFFICE", "open_office"),
            ("OFFICE 101", "closed_office"),
            ("CONF. RM", "conference"),
            ("ELEVATOR LOBBY", "elevator_lobby"),
            ("LOBBY", "lobby"),
            ("MEN'S TOILET", "restroom"),
            ("ELEC.", "mechanical_electrical"),
            ("IDF", "it_room"),
            ("CORRIDOR", "corridor"),
            ("STAIR 2", "stair"),
            ("BREAK ROOM", "break_room"),
        ],
    )
    def test_names(self, name, expected):
        assert sud.classify_space_type(name) == (expected, sud.MATCH_CONFIDENCE)

    def test_unknown_name_falls_back_to_whole_building(self):
        assert sud.classify_space_type("ROOM 7") == (sud.FALLBACK_TYPE, sud.FALLBACK_CONFIDENCE)

    def test_shafts_and_cores_get_nothing_closets_are_storage(self):
        assert sud.classify_space_type("SHAFT", "shaft") == (None, 0.0)
        assert sud.classify_space_type("", "elevator_core") == (None, 0.0)
        assert sud.classify_space_type("CL", "closet")[0] == "storage"


class TestApply:
    def test_fills_missing_values_with_provenance(self):
        sp = _space("L1-101", "OPEN OFFICE")
        summary = sud.apply_space_use_defaults(_model(sp))
        row = data.SPACE_USE_DEFAULTS["open_office"]
        assert sp.use.space_type == "open_office"
        assert sp.lighting.lpd_w_m2 == row["lpd_w_m2"]
        assert sp.use.people_per_m2 == row["people_per_m2"]
        assert sp.use.equipment_w_m2 == row["equipment_w_m2"]
        assert sp.use.occupancy_schedule == "OfficeLarge BLDG_OCC_SCH"
        for prov in (sp.lighting.provenance, sp.use.provenance):
            assert prov.method == "doe_prototype_default"
            assert "ASHRAE 90.1-2019" in prov.note and "Office / OpenOffice" in prov.note
        assert summary.filled["lpd_w_m2"] == 1 and summary.typed == 1

    def test_never_overwrites_extracted_values(self):
        sp = _space("L1-102", "OFFICE")
        drawn = Provenance("arch_E101", 1, "schedule_join", 0.9)
        sp.lighting = SpaceLighting(total_w=300.0, lpd_w_m2=15.0, provenance=drawn)
        sp.use = SpaceUse(people_per_m2=0.2, occupancy_schedule="CUSTOM")
        summary = sud.apply_space_use_defaults(_model(sp))
        assert sp.lighting.lpd_w_m2 == 15.0 and sp.lighting.provenance is drawn
        assert sp.use.people_per_m2 == 0.2 and sp.use.occupancy_schedule == "CUSTOM"
        assert sp.use.equipment_w_m2 == data.SPACE_USE_DEFAULTS["closed_office"]["equipment_w_m2"]
        assert summary.kept["lpd_w_m2"] == 1 and summary.kept["people_per_m2"] == 1

    def test_respects_preset_space_type_and_skips_shafts(self):
        a = _space("L1-1", "ROOM 7")
        a.use = SpaceUse(space_type="conference")
        b = _space("L1-2", "", poly_type="shaft")
        c = _space("L1-3", "ROOM 9")
        summary = sud.apply_space_use_defaults(_model(a, b, c))
        assert a.use.people_per_m2 == data.SPACE_USE_DEFAULTS["conference"]["people_per_m2"]
        assert b.use.space_type == "" and b.lighting.lpd_w_m2 is None
        assert summary.skipped == ["L1-2"] and summary.fallback == ["L1-3"]
        assert c.use.provenance.confidence == sud.FALLBACK_CONFIDENCE

    def test_unknown_preset_type_raises(self):
        sp = _space("L1-1", "X")
        sp.use = SpaceUse(space_type="spaceship")
        with pytest.raises(ValueError, match="spaceship"):
            sud.apply_space_use_defaults(_model(sp))

    def test_round_trips_through_model_dict(self):
        sp = _space("L1-101", "OPEN OFFICE")
        m = _model(sp)
        sud.apply_space_use_defaults(m)
        back = BuildingModel.from_dict(m.to_dict()).spaces["L1-101"]
        assert isinstance(back.use, SpaceUse)
        assert back.use.provenance.method == "doe_prototype_default"
        assert back.use.equipment_w_m2 == sp.use.equipment_w_m2

    def test_lighting_watts_prefers_fixtures_then_lpd(self):
        sp = _space("L1-1", "OPEN OFFICE", area=10.0)
        assert sud.lighting_watts(sp) == 0.0
        sud.apply_space_use_defaults(_model(sp))
        assert sud.lighting_watts(sp) == pytest.approx(10.0 * sp.lighting.lpd_w_m2)
        sp.lighting.total_w = 123.0
        assert sud.lighting_watts(sp) == 123.0
