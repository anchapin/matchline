"""Tests for diff.py — revision diffing at zone/space level."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from building_model import (
    BuildingModel,
    Level,
    Provenance,
    RevisionEvent,
    Space,
    SpaceHVAC,
    SpaceLighting,
    SpaceOpening,
)
from diff import (
    build_change_map,
    compute_geometry_hash,
    compute_schedule_hash,
    diff_models,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def P(rev: int = 1, conf: float = 1.0, method: str = "test"):
    return Provenance(sheet_id="A101", revision=rev, method=method, confidence=conf, note="")


def base_model() -> BuildingModel:
    """Minimal two-space single-level model used as baseline."""
    m = BuildingModel(name="test_bldg")
    m.levels.append(Level(id="L1", name="Level 1", wall_height_m=3.0))

    def sp(sid, name, poly, area=30.0, lighting=None, hvac=None):
        return Space(
            id=sid,
            level_id="L1",
            name=name,
            number=name[:3],
            polygon_m=[list(p) for p in poly],
            area_m2=area,
            volume_m3=area * 3.0,
            lighting=lighting or SpaceLighting(),
            hvac=hvac or SpaceHVAC(),
            core_provenance=P(),
            label_confidence=1.0,
        )

    m.spaces["S1"] = sp("S1", "Office", [[0, 0], [5, 0], [5, 6], [0, 6]])
    m.spaces["S2"] = sp("S2", "Conf", [[5, 0], [10, 0], [10, 6], [5, 6]])
    m.revision_log.append(RevisionEvent(seq=1, sheet_id="A101", revision=1, action="test", note=""))
    return m


def clone_model(m: BuildingModel) -> BuildingModel:
    return BuildingModel.from_dict(m.to_dict())


def tmp_json(m: BuildingModel) -> Path:
    p = Path(tempfile.mkstemp(suffix=".json")[1])
    with open(p, "w") as f:
        json.dump(m.to_json(), f)
    return p


# ---------------------------------------------------------------------------
# Hash primitives
# ---------------------------------------------------------------------------


class TestComputeGeometryHash:
    def test_identical_polygon_same_hash(self):
        poly = [[0, 0], [5, 0], [5, 6], [0, 6]]
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[list(p) for p in poly],
            area_m2=30.0,
            volume_m3=90.0,
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[list(p) for p in poly],
            area_m2=30.0,
            volume_m3=90.0,
        )
        assert compute_geometry_hash(s1) == compute_geometry_hash(s2)

    def test_different_polygon_different_hash(self):
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [10, 0], [10, 6], [0, 6]],
            area_m2=60.0,
            volume_m3=180.0,
        )
        assert compute_geometry_hash(s1) != compute_geometry_hash(s2)

    def test_different_area_same_polygon_still_different(self):
        poly = [[0, 0], [5, 0], [5, 6], [0, 6]]
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[list(p) for p in poly],
            area_m2=30.0,
            volume_m3=90.0,
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[list(p) for p in poly],
            area_m2=31.0,
            volume_m3=93.0,
        )
        assert compute_geometry_hash(s1) != compute_geometry_hash(s2)


class TestComputeScheduleHash:
    def test_no_lighting_hvac_same_hash(self):
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(),
            hvac=SpaceHVAC(),
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(),
            hvac=SpaceHVAC(),
        )
        assert compute_schedule_hash(s1) == compute_schedule_hash(s2)

    def test_different_lighting_watts_different_hash(self):
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(total_w=100.0),
            hvac=SpaceHVAC(),
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(total_w=200.0),
            hvac=SpaceHVAC(),
        )
        assert compute_schedule_hash(s1) != compute_schedule_hash(s2)

    def test_different_hvac_zone_ids_different_hash(self):
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(),
            hvac=SpaceHVAC(zone_ids=["Z1"]),
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            lighting=SpaceLighting(),
            hvac=SpaceHVAC(zone_ids=["Z2"]),
        )
        assert compute_schedule_hash(s1) != compute_schedule_hash(s2)

    def test_openings_change_hash(self):
        s1 = Space(
            id="S1",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            openings=[
                SpaceOpening(
                    id="W1", tag="W1", category="window", width_m=1.5, height_m=1.2, sill_m=0.9
                )
            ],
        )
        s2 = Space(
            id="S2",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[0, 0], [5, 0], [5, 6], [0, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            openings=[
                SpaceOpening(
                    id="W1", tag="W1", category="window", width_m=2.5, height_m=1.2, sill_m=0.9
                )
            ],
        )
        assert compute_schedule_hash(s1) != compute_schedule_hash(s2)


# ---------------------------------------------------------------------------
# diff_models
# ---------------------------------------------------------------------------


class TestDiffModelsIdentical:
    def test_identical_models_all_unchanged(self):
        old = base_model()
        new = clone_model(old)
        result = diff_models(old, new)
        assert result.changed_space_ids == []
        assert set(result.unchanged_space_ids) == {"S1", "S2"}
        assert result.unclassified_space_ids == []

    def test_diff_summary_revision_fields(self):
        old = base_model()
        new = clone_model(old)
        result = diff_models(old, new)
        assert "rev1" in result.old_revision or "1" in result.old_revision
        assert result.total_spaces == 2


class TestDiffModelsGeometryChange:
    def test_polygon_change_detected(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].polygon_m = [[0, 0], [6, 0], [6, 6], [0, 6]]  # wider
        new.spaces["S1"].area_m2 = 36.0
        new.spaces["S1"].volume_m3 = 108.0
        result = diff_models(old, new)
        assert "S1" in result.changed_space_ids
        assert "S2" in result.unchanged_space_ids

    def test_geometry_change_reason_is_geometry(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].polygon_m = [[0, 0], [6, 0], [6, 6], [0, 6]]
        new.spaces["S1"].area_m2 = 36.0
        new.spaces["S1"].volume_m3 = 108.0
        result = diff_models(old, new)
        s1_diff = next(d for d in result.space_diffs if d.space_id == "S1")
        assert s1_diff.geo_changed is True
        assert s1_diff.sched_changed is False
        assert "geometry" in s1_diff.reason


class TestDiffModelsScheduleChange:
    def test_lighting_watts_change_detected(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].lighting = SpaceLighting(total_w=500.0)
        result = diff_models(old, new)
        assert "S1" in result.changed_space_ids
        assert "S2" in result.unchanged_space_ids

    def test_schedule_change_reason(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].hvac = SpaceHVAC(zone_ids=["Z1", "Z2"])
        result = diff_models(old, new)
        s1_diff = next(d for d in result.space_diffs if d.space_id == "S1")
        assert s1_diff.geo_changed is False
        assert s1_diff.sched_changed is True
        assert s1_diff.reason == "schedule"


class TestDiffModelsNewSpace:
    def test_new_space_is_changed(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S3"] = Space(
            id="S3",
            level_id="L1",
            name="Storage",
            number="103",
            polygon_m=[[10, 0], [15, 0], [15, 6], [10, 6]],
            area_m2=30.0,
            volume_m3=90.0,
            core_provenance=P(),
        )
        result = diff_models(old, new)
        assert "S3" in result.changed_space_ids
        assert "S1" in result.unchanged_space_ids
        assert "S2" in result.unchanged_space_ids

    def test_new_space_reason_is_new_space(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S3"] = Space(
            id="S3",
            level_id="L1",
            name="Storage",
            number="103",
            polygon_m=[[10, 0], [15, 0], [15, 6], [10, 6]],
            area_m2=30.0,
            volume_m3=90.0,
        )
        result = diff_models(old, new)
        s3_diff = next(d for d in result.space_diffs if d.space_id == "S3")
        assert s3_diff.reason == "new_space"


class TestDiffModelsRemovedSpace:
    def test_removed_space_goes_to_unclassified(self):
        old = base_model()
        new = clone_model(old)
        del new.spaces["S2"]
        result = diff_models(old, new)
        assert "S2" in result.unclassified_space_ids
        assert "S1" in result.unchanged_space_ids

    def test_removed_space_reason(self):
        old = base_model()
        new = clone_model(old)
        del new.spaces["S1"]
        result = diff_models(old, new)
        s1_diff = next(d for d in result.space_diffs if d.space_id == "S1")
        assert s1_diff.reason == "removed_in_new_revision"
        assert s1_diff.geo_changed is True
        assert s1_diff.sched_changed is True


class TestDiffModelsBothChanged:
    def test_both_geo_and_schedule_change(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].polygon_m = [[0, 0], [6, 0], [6, 6], [0, 6]]
        new.spaces["S1"].area_m2 = 36.0
        new.spaces["S1"].volume_m3 = 108.0
        new.spaces["S1"].lighting = SpaceLighting(total_w=500.0)
        result = diff_models(old, new)
        s1_diff = next(d for d in result.space_diffs if d.space_id == "S1")
        assert s1_diff.geo_changed is True
        assert s1_diff.sched_changed is True
        assert s1_diff.reason == "geometry+schedule"


# ---------------------------------------------------------------------------
# build_change_map
# ---------------------------------------------------------------------------


class TestBuildChangeMap:
    def test_unchanged_spaces_marked_correctly(self):
        old = base_model()
        new = clone_model(old)
        result = diff_models(old, new)
        change_map = build_change_map(result)
        assert change_map["S1"] == "unchanged"
        assert change_map["S2"] == "unchanged"

    def test_changed_spaces_marked_changed(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].lighting = SpaceLighting(total_w=500.0)
        result = diff_models(old, new)
        change_map = build_change_map(result)
        assert change_map["S1"] == "changed"
        assert change_map["S2"] == "unchanged"

    def test_new_space_marked_new(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S3"] = Space(
            id="S3",
            level_id="L1",
            name="x",
            number="x",
            polygon_m=[[10, 0], [15, 0], [15, 6], [10, 6]],
            area_m2=30.0,
            volume_m3=90.0,
        )
        result = diff_models(old, new)
        change_map = build_change_map(result)
        assert change_map["S3"] == "new"

    def test_removed_space_marked_removed(self):
        old = base_model()
        new = clone_model(old)
        del new.spaces["S1"]
        result = diff_models(old, new)
        change_map = build_change_map(result)
        assert change_map["S1"] == "removed"


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


class TestDiffSummaryToDict:
    def test_serializes_all_fields(self):
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].lighting = SpaceLighting(total_w=500.0)
        result = diff_models(old, new)
        d = result.to_dict()
        assert "old_revision" in d
        assert "new_revision" in d
        assert "changed_space_ids" in d
        assert "unchanged_space_ids" in d
        assert "unclassified_space_ids" in d
        assert "space_diffs" in d
        assert d["changed_space_ids"] == ["S1"]


# ---------------------------------------------------------------------------
# Provenance rule (documentation test / invariant)
# ---------------------------------------------------------------------------


class TestProvenanceRuleInvariant:
    def test_unchanged_spaces_preserve_provenance(self):
        """Carried-forward spaces must NOT have their core_provenance re-stamped.

        This is an invariant: unchanged space facts keep their original
        extraction provenance.  The diff module does not modify provenance
        directly, but the pipeline integration MUST honour this rule when
        applying the diff result.
        """
        old = base_model()
        new = clone_model(old)
        prov_old = old.spaces["S1"].core_provenance
        assert prov_old is not None
        new.spaces["S1"].core_provenance = prov_old  # pipeline carries forward
        assert new.spaces["S1"].core_provenance.revision == prov_old.revision

    def test_changed_space_needs_fresh_provenance(self):
        """Changed spaces get fresh extraction provenance with new revision.

        The diff identifies what needs re-extraction; the pipeline stamps
        changed spaces with the new revision number.
        """
        old = base_model()
        new = clone_model(old)
        new.spaces["S1"].lighting = SpaceLighting(total_w=999.0)
        result = diff_models(old, new)
        change_map = build_change_map(result)
        assert change_map["S1"] == "changed"
