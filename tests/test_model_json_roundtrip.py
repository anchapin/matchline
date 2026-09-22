"""Tests for BuildingModel JSON serialization: round-trip via to_json/from_json."""

import pytest

from building_model import BuildingModel
from link import build_model
from synth.multidiscipline import generate_building


class TestBuildingModelRoundTrip:
    """Verify that BuildingModel survives a to_json -> from_json round-trip."""

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_roundtrip_preserves_model(self, seed):
        """Every field that was serialized must be present after deserialization."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        json_str = model.to_json()
        restored = BuildingModel.from_json(json_str)
        assert restored.name == model.name
        assert len(restored.levels) == len(model.levels)
        assert len(restored.spaces) == len(model.spaces)
        assert len(restored.zones) == len(model.zones)
        assert len(restored.envelope) == len(model.envelope)
        assert len(restored.review_queue) == len(model.review_queue)

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_roundtrip_preserves_spaces(self, seed):
        """Space polygons, names, numbers, and areas survive round-trip."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        for sid, sp in model.spaces.items():
            rp = restored.spaces[sid]
            assert rp.name == sp.name
            assert rp.number == sp.number
            assert rp.area_m2 == pytest.approx(sp.area_m2)
            assert rp.polygon_m == sp.polygon_m
            assert rp.level_id == sp.level_id

    @pytest.mark.parametrize("seed", [101, 102])
    def test_roundtrip_preserves_openings(self, seed):
        """Opening dimensions and categories survive round-trip."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        for sid, sp in model.spaces.items():
            rp = restored.spaces[sid]
            assert len(rp.openings) == len(sp.openings)
            for o, ro in zip(sp.openings, rp.openings):
                assert o.tag == ro.tag
                assert o.category == ro.category
                assert o.width_m == pytest.approx(ro.width_m)
                assert o.height_m == pytest.approx(ro.height_m)
                assert o.area_m2 == pytest.approx(ro.area_m2)

    @pytest.mark.parametrize("seed", [101, 102])
    def test_roundtrip_preserves_envelope(self, seed):
        """Envelope wall areas and facade directions survive round-trip."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        assert len(restored.envelope) == len(model.envelope)
        for w, rw in zip(model.envelope, restored.envelope):
            assert w.id == rw.id
            assert w.facade == rw.facade
            assert w.length_m == pytest.approx(rw.length_m)
            assert w.area_m2 == pytest.approx(w.area_m2)

    @pytest.mark.parametrize("seed", [101, 102])
    def test_roundtrip_preserves_provenance(self, seed):
        """Every provenance record survives round-trip intact."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        for sid, sp in model.spaces.items():
            rp = restored.spaces[sid]
            assert rp.core_provenance.sheet_id == sp.core_provenance.sheet_id
            assert rp.core_provenance.method == sp.core_provenance.method
            assert rp.core_provenance.confidence == pytest.approx(sp.core_provenance.confidence)
        for w, rw in zip(model.envelope, restored.envelope):
            assert w.provenance.sheet_id == rw.provenance.sheet_id

    @pytest.mark.parametrize("seed", [101, 102])
    def test_roundtrip_preserves_zones(self, seed):
        """Zone space membership survives round-trip."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        assert set(model.zones.keys()) == set(restored.zones.keys())
        for zid, z in model.zones.items():
            rz = restored.zones[zid]
            assert set(z.space_ids) == set(rz.space_ids)

    @pytest.mark.parametrize("seed", [101, 102])
    def test_roundtrip_preserves_review_queue(self, seed):
        """Review queue items survive round-trip with all triage fields."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        restored = BuildingModel.from_json(model.to_json())
        assert len(restored.review_queue) == len(model.review_queue)
        for item, restored_item in zip(model.review_queue, restored.review_queue):
            assert restored_item.id == item.id
            assert restored_item.kind == item.kind
            assert restored_item.description == item.description
            assert restored_item.confidence == pytest.approx(item.confidence)
            assert restored_item.status == item.status


class TestBuildingModelFromDict:
    """Verify BuildingModel.from_dict works on raw dicts from pipeline JSON."""

    @pytest.mark.parametrize("seed", [101, 102])
    def test_from_dict_usable_without_json(self, seed):
        """from_dict can be called directly with a plain dict."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        d = model.to_dict()
        restored = BuildingModel.from_dict(d)
        assert restored.name == model.name
        assert len(restored.spaces) == len(model.spaces)
