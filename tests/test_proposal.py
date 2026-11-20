"""The native building proposal: schema, validator and mapper (#1417).

Covered here:

  * the golden mapping of the worked example against the #1415 ground
    truth (spaces, envelope segments, BIM inventory, openings),
  * the interior-door variant (L10: stored once, on the lower-id space),
  * determinism (L1), provenance completeness (L3) and no silent loss (L2),
  * one validator test per error code, plus the warning vocabulary,
  * the generated JSON Schema (the same field tables the validator reads),
  * the model round trip through BuildingModel.from_json, and
  * the mapped model through the repo's own export gate.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from proposal import (
    Diagnostic,
    ProposalValidation,
    map_proposal,
    proposal_json_schema,
    validate_proposal,
)

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE_PATH = FIXTURES / "proposal_example.json"
GROUND_TRUTH_PATH = FIXTURES / "proposal_ground_truth.json"

#: Relative tolerance for the float comparisons against the ground truth.
REL_TOL = 1e-6


def _example() -> dict:
    """A fresh copy of the worked example."""
    return json.loads(EXAMPLE_PATH.read_text())


def _ground_truth() -> dict:
    return json.loads(GROUND_TRUTH_PATH.read_text())


def _close(actual: float, expected: float, tol: float = REL_TOL) -> bool:
    if expected == 0:
        return abs(actual) <= tol
    return abs(actual - expected) / abs(expected) <= tol


def _error_codes(result: ProposalValidation) -> set[str]:
    return {d.code for d in result.errors}


def _find(result: ProposalValidation, code: str, path: str) -> Diagnostic:
    """The one diagnostic with this code and path."""
    hits = [d for d in result.errors if d.code == code and d.path == path]
    assert hits, f"no {code} diagnostic at {path!r}; got " + ", ".join(
        f"{d.code}@{d.path}" for d in result.errors
    )
    return hits[0]


# --- the golden mapping ----------------------------------------------------


def test_golden_example_matches_ground_truth():
    """The worked example maps to the #1415 ground truth, field for field."""
    model, report = map_proposal(_example())
    truth = _ground_truth()
    assert report.reconciles()

    # --- spaces ----------------------------------------------------------
    assert set(model.spaces) == {"ground-101", "ground-102"}
    for expected in truth["spaces"]:
        space = model.spaces[expected["matchline_id"]]
        assert space.id == expected["matchline_id"]
        assert space.number == expected["number"]
        assert space.name == expected["name"]
        assert _close(space.area_m2, expected["area_m2"]), (
            f"{space.id} area {space.area_m2} != {expected['area_m2']}"
        )
        assert _close(space.volume_m3, expected["area_m2"] * 3.0)

    # --- envelope --------------------------------------------------------
    assert len(model.envelope) == 4
    for expected in truth["envelope_segments"]:
        segment_id = f"{truth['level_id']}-{expected['wall_id']}#{expected['segment_index']}"
        segment = next((e for e in model.envelope if e.id == segment_id), None)
        assert segment is not None, f"missing envelope segment {segment_id}"
        assert segment.facade == expected["facade"]
        assert segment.space_id == expected["space_id"]
        assert _close(segment.length_m, expected["length_m"])
        assert segment.from_m == expected["from"]
        assert segment.to_m == expected["to"]
    facades = {e.facade for e in model.envelope}
    assert facades == {"north", "east", "south", "west"}

    # --- BIM inventory ---------------------------------------------------
    walls = [el for el in model.bim_elements if el.ifc_class == "IfcWall"]
    slabs = [el for el in model.bim_elements if el.ifc_class == "IfcSlab"]
    assert len(walls) == 5
    assert len(slabs) == 1
    assert [el.global_id for el in walls] == [
        "outer-walls#0",
        "outer-walls#1",
        "outer-walls#2",
        "outer-walls#3",
        "divider#0",
    ]

    # --- openings --------------------------------------------------------
    assert sum(len(sp.openings) for sp in model.spaces.values()) == 3
    bim_openings = {op.id: (el, op) for el in model.bim_elements for op in el.openings}
    for expected in truth["openings"]:
        space = model.spaces[expected["space_id"]]
        opening = next((o for o in space.openings if o.id == expected["id"]), None)
        assert opening is not None, f"opening {expected['id']} not on {space.id}"
        assert opening.host_facade == expected["facade"]
        assert len(opening.host_interval_m) == 2
        assert _close(opening.host_interval_m[0], expected["interval_m"][0])
        assert _close(opening.host_interval_m[1], expected["interval_m"][1])
        assert _close(opening.s_center_m, expected["s_center_m"])
        assert _close(opening.sill_m, expected["sill_m"])
        assert _close(opening.head_m, expected["head_m"])
        assert _close(opening.area_m2, expected["area_m2"])
        # the host segment, on the BIM side
        host_id = f"{expected['wall_id']}#{expected['segment_index']}"
        assert expected["id"] in bim_openings
        host_element, host_opening = bim_openings[expected["id"]]
        assert host_element.global_id == host_id
        assert _close(host_opening.s_center_m, expected["s_center_m"])

    # --- no silent loss (L2) ---------------------------------------------
    assert report.reconciles()
    counts = report.counts("ground")
    assert counts.walls_proposed == counts.walls_mapped == 2
    assert counts.spaces_proposed == counts.spaces_mapped == 2
    assert counts.openings_proposed == counts.openings_mapped == 3
    assert counts.slabs_proposed == counts.slabs_mapped == 1
    assert report.entries == []


def test_interior_door_is_stored_once_on_the_lower_id_space():
    """L10: an interior opening lives on the lower-id space, with the far side named."""
    doc = _example()
    divider = doc["levels"][0]["walls"][1]
    divider["openings"] = [
        {
            "id": "interior-door",
            "kind": "door",
            "offset_m": 2.0,
            "width_m": 0.9,
            "height_m": 2.1,
        }
    ]
    model, _report = map_proposal(doc)

    attached = [
        op for sp in model.spaces.values() for op in sp.openings if op.id == "interior-door"
    ]
    assert len(attached) == 1
    door = attached[0]
    assert door.provenance is not None
    assert "interior-door" in door.provenance.note
    assert door.adjacent_space_id == "ground-102"
    assert door.host_facade == ""
    assert _close(door.host_interval_m[0], 2.0)
    assert _close(door.host_interval_m[1], 2.9)
    assert _close(door.s_center_m, 2.45)
    assert _close(door.head_m, 2.1)

    # it is stored on the lower-id space and nowhere else
    owners = [
        sp.id for sp in model.spaces.values() if any(o.id == "interior-door" for o in sp.openings)
    ]
    assert owners == ["ground-101"]

    # the BIM opening still records it, on its host segment
    hosts = [el for el in model.bim_elements if any(o.id == "interior-door" for o in el.openings)]
    assert len(hosts) == 1
    assert hosts[0].global_id == "divider#0"


def test_mapping_is_deterministic():
    """L1: the same document maps to the same bytes, twice."""
    doc = _example()
    first, _ = map_proposal(copy.deepcopy(doc))
    second, _ = map_proposal(copy.deepcopy(doc))
    assert first.to_json() == second.to_json()


def test_every_mapped_fact_carries_provenance():
    """L3: no mapped fact without provenance, and the provenance says where it came from."""
    doc = _example()
    model, _report = map_proposal(doc)
    proposal_id = doc["id"]

    def check(provenance, source: str):
        assert provenance is not None, f"{source} has no provenance"
        assert provenance.sheet_id == f"proposal:{proposal_id}"
        assert provenance.method == "proposal_mapping"
        assert provenance.confidence == 1.0
        assert source in provenance.note, f"{source} is not named in {provenance.note!r}"

    for space in model.spaces.values():
        check(space.core_provenance, space.number)
        for opening in space.openings:
            check(opening.provenance, opening.id)
    for segment in model.envelope:
        check(segment.provenance, segment.id.split("-", 1)[1].split("#")[0])
    for element in model.bim_elements:
        check(element.provenance, element.global_id.split("#")[0])
        for opening in element.openings:
            check(opening.provenance, opening.id)


def test_map_proposal_refuses_a_document_with_errors():
    """L4: a document with an error is not mapped at all."""
    doc = _example()
    doc["levels"][0]["spaces"][0]["polygon"] = [[0.0, 0.0], [1.0, 1.0]]
    with pytest.raises(ValueError):
        map_proposal(doc)


def test_an_opening_with_no_bordering_space_is_reported_and_left_unattached():
    """L10: an opening nothing borders is reported, and its BIM opening survives."""
    doc = _example()
    doc["levels"][0]["walls"].append(
        {
            "id": "detached",
            "points": [[1.0, 0.05], [3.0, 0.05]],
            "thickness_m": 0.05,
            "height_m": 3.0,
            "openings": [
                {
                    "id": "orphan-door",
                    "kind": "door",
                    "offset_m": 0.5,
                    "width_m": 0.9,
                    "height_m": 2.1,
                }
            ],
        }
    )
    model, report = map_proposal(doc)

    assert not any(op.id == "orphan-door" for sp in model.spaces.values() for op in sp.openings), (
        "an opening nothing borders must not be attached to a space"
    )
    hosts = [el for el in model.bim_elements if any(o.id == "orphan-door" for o in el.openings)]
    assert len(hosts) == 1
    assert hosts[0].global_id == "detached#0"

    entries = [e for e in report.entries if e.element_id == "orphan-door"]
    assert len(entries) == 1
    assert entries[0].unmapped
    counts = report.counts("ground")
    assert counts.openings_proposed == 4
    assert counts.openings_mapped == 3
    assert counts.openings_reported == 1
    assert report.reconciles()


def test_a_non_axis_aligned_exterior_segment_gets_no_guessed_facade():
    """L9: an unnamed exterior segment keeps an empty facade and is flagged."""
    doc = _example()
    doc["levels"][0]["walls"].append(
        {
            "id": "diagonal",
            "points": [[0.1, 0.1], [2.0, 0.2]],
            "thickness_m": 0.2,
            "height_m": 3.0,
        }
    )
    model, report = map_proposal(doc)

    segment = next((e for e in model.envelope if e.id == "ground-diagonal#0"), None)
    assert segment is not None
    assert segment.facade == ""
    flags = [e for e in report.entries if e.element_id == "ground-diagonal#0"]
    assert len(flags) == 1
    assert "facade" in flags[0].reason


# --- the validator ---------------------------------------------------------


def test_example_validates_with_only_tag_warnings():
    """The worked example is valid; its three untagged openings warn."""
    result = validate_proposal(_example())
    assert result.ok, [d.as_dict() for d in result.errors]
    assert result.errors == []
    assert len(result.warnings) == 3
    assert {d.code for d in result.warnings} == {"opening-tag-absent"}
    for warning in result.warnings:
        assert warning.severity == "warning"
        assert warning.path.endswith(".tag")


def test_validator_does_not_mutate_its_input():
    doc = _example()
    before = copy.deepcopy(doc)
    validate_proposal(doc)
    assert doc == before


def _wall(doc: dict) -> dict:
    return doc["levels"][0]["walls"][0]


def _cases() -> list[tuple[str, str, dict]]:
    """One case per error code: (code, path, the broken document)."""
    cases: list[tuple[str, str, dict]] = []

    def build(mutate) -> dict:
        doc = _example()
        mutate(doc)
        return doc

    cases.append(
        (
            "unknown-field",
            "zones",
            build(lambda d: d.__setitem__("zones", [])),
        )
    )
    cases.append(
        (
            "missing-field",
            "levels[0].id",
            build(lambda d: d["levels"][0].pop("id")),
        )
    )
    cases.append(
        (
            "wrong-type",
            "levels[0].height_m",
            build(lambda d: d["levels"][0].__setitem__("height_m", "3 m")),
        )
    )
    cases.append(
        (
            "unsupported-version",
            "version",
            build(lambda d: d.__setitem__("version", 2)),
        )
    )
    cases.append(
        (
            "unsupported-units",
            "units",
            build(lambda d: d.__setitem__("units", "feet")),
        )
    )
    cases.append(
        (
            "duplicate-id",
            "levels[0].walls[1].id",
            build(lambda d: d["levels"][0]["walls"][1].__setitem__("id", "outer-walls")),
        )
    )
    cases.append(
        (
            "duplicate-room-number",
            "levels[0].spaces[1].number",
            build(lambda d: d["levels"][0]["spaces"][1].__setitem__("number", "101")),
        )
    )
    cases.append(
        (
            "bad-polygon",
            "levels[0].spaces[0].polygon",
            build(
                lambda d: d["levels"][0]["spaces"][0].__setitem__(
                    "polygon", [[0.1, 0.1], [4.95, 0.1]]
                )
            ),
        )
    )
    cases.append(
        (
            "bad-polygon",
            "levels[0].spaces[0].polygon",
            build(
                lambda d: d["levels"][0]["spaces"][0].__setitem__(
                    "polygon", [[0.1, 0.1], [4.95, 4.9], [4.95, 0.1], [0.1, 4.9]]
                )
            ),
        )
    )
    cases.append(
        (
            "space-overlap",
            "levels[0].spaces[1].polygon",
            build(
                lambda d: d["levels"][0]["spaces"][1].__setitem__(
                    "polygon", [[4.0, 0.1], [7.9, 0.1], [7.9, 4.9], [4.0, 4.9]]
                )
            ),
        )
    )
    cases.append(
        (
            "wall-too-short",
            "levels[0].walls[0].openings[0].width_m",
            build(lambda d: _wall(d)["openings"][0].update({"offset_m": 0.0, "width_m": 40.0})),
        )
    )
    cases.append(
        (
            "opening-out-of-range",
            "levels[0].walls[0].openings[0].offset_m",
            build(lambda d: _wall(d)["openings"][0].update({"offset_m": 25.5})),
        )
    )
    cases.append(
        (
            "opening-straddles-vertex",
            "levels[0].walls[0].openings[0].offset_m",
            build(lambda d: _wall(d)["openings"][0].update({"offset_m": 7.5, "width_m": 1.2})),
        )
    )
    cases.append(
        (
            "opening-overlap",
            "levels[0].walls[0].openings[1].offset_m",
            build(lambda d: _wall(d)["openings"][1].update({"offset_m": 2.0, "width_m": 1.2})),
        )
    )
    cases.append(
        (
            "opening-does-not-fit",
            "levels[0].walls[0].openings[0].sill_m",
            build(lambda d: _wall(d)["openings"][0].update({"sill_m": 2.5, "height_m": 1.2})),
        )
    )
    cases.append(
        (
            "unknown-type-ref",
            "levels[0].walls[0].type_ref",
            build(
                lambda d: _wall(d).update({"type_ref": "not-a-type"}),
            ),
        )
    )
    cases.append(
        (
            "layer-sum-mismatch",
            "levels[0].walls[0].thickness_m",
            build(
                lambda d: (
                    d.__setitem__(
                        "wallTypes",
                        {
                            "ext": {
                                "name": "Ext",
                                "layers": [
                                    {
                                        "material": "concrete",
                                        "thickness_m": 0.3,
                                        "function": "structure",
                                    }
                                ],
                            }
                        },
                    ),
                    _wall(d).update({"type_ref": "ext"}),
                )
            ),
        )
    )
    return cases


@pytest.mark.parametrize(
    "code,path,doc",
    _cases(),
    ids=[f"{code}-{path}" for code, path, _doc in _cases()],
)
def test_validator_reports_each_error_code(code, path, doc):
    """One test per error code: the code is raised, at the right path."""
    result = validate_proposal(doc)
    assert not result.ok
    assert code in _error_codes(result), [d.as_dict() for d in result.errors]
    diagnostic = _find(result, code, path)
    assert diagnostic.severity == "error"
    assert diagnostic.message.endswith(".")
    assert diagnostic.message


def test_geometric_messages_carry_the_numbers():
    """Where a number exists, the message states it."""
    doc = _example()
    _wall(doc)["openings"][0].update({"offset_m": 7.5, "width_m": 1.2})
    diagnostic = _find(
        validate_proposal(doc),
        "opening-straddles-vertex",
        "levels[0].walls[0].openings[0].offset_m",
    )
    assert "7.5" in diagnostic.message
    assert "8" in diagnostic.message


def test_opening_does_not_fit_carries_the_fitting_range():
    """The fitting range and the nearest fitting offset travel in details."""
    doc = _example()
    _wall(doc)["openings"][0].update({"sill_m": 2.5, "height_m": 1.2})
    result = validate_proposal(doc)
    diagnostic = _find(result, "opening-does-not-fit", "levels[0].walls[0].openings[0].sill_m")
    assert diagnostic.details["fitting_range_m"] == [0.0, 1.8]
    assert diagnostic.details["nearest_fitting_offset_m"] == 1.8
    assert diagnostic.details["wall_height_m"] == 3.0


def test_window_without_a_sill_is_a_missing_field():
    """sill_m is required for windows; doors and voids default to 0."""
    doc = _example()
    _wall(doc)["openings"][0].pop("sill_m")
    result = validate_proposal(doc)
    assert "missing-field" in _error_codes(result)
    _find(result, "missing-field", "levels[0].walls[0].openings[0].sill_m")

    doc = _example()
    _wall(doc)["openings"][2].pop("sill_m")  # the door
    result = validate_proposal(doc)
    assert result.ok, [d.as_dict() for d in result.errors]


def test_wall_height_defaulted_is_a_warning():
    """A wall without a height takes the level height, and says so."""
    doc = _example()
    _wall(doc).pop("height_m")
    result = validate_proposal(doc)
    assert result.ok
    codes = {d.code for d in result.warnings}
    assert "wall-height-defaulted" in codes
    warning = next(d for d in result.warnings if d.code == "wall-height-defaulted")
    assert warning.path == "levels[0].walls[0].height_m"


def test_a_wall_needs_a_thickness_or_a_type_ref():
    doc = _example()
    _wall(doc).pop("thickness_m")
    result = validate_proposal(doc)
    assert not result.ok
    assert "missing-field" in _error_codes(result)


def test_unknown_keys_are_refused_outside_metadata_and_property_sets():
    doc = _example()
    doc["metadata"] = {"anything": {"goes": True}}
    doc["levels"][0]["walls"][0]["property_sets"] = {"custom": 1}
    assert validate_proposal(doc).ok

    doc = _example()
    doc["levels"][0]["spaces"][0]["colour"] = "red"
    result = validate_proposal(doc)
    assert "unknown-field" in _error_codes(result)
    _find(result, "unknown-field", "levels[0].spaces[0].colour")


# --- the schema ------------------------------------------------------------


def test_json_schema_validates_the_example():
    """The generated schema accepts the worked example."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = proposal_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.validate(_example(), schema)


def test_json_schema_names_every_top_level_field():
    """The schema and the validator read the same field table."""
    schema = proposal_json_schema()
    for field in ("schema", "version", "id", "units", "wallTypes", "levels", "metadata"):
        assert field in schema["properties"], f"{field} is missing from the schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema", "version", "id", "units", "levels"}
    assert "$defs" in schema
    for name in ("level", "wall", "opening", "space", "slab", "wallType", "layer"):
        assert name in schema["$defs"]


def test_json_schema_refuses_a_broken_document():
    jsonschema = pytest.importorskip("jsonschema")
    schema = proposal_json_schema()
    doc = _example()
    doc["levels"][0]["walls"][0].pop("thickness_m")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


# --- round trip and the export gate ----------------------------------------


def test_model_round_trips_through_json():
    """BuildingModel.from_json reproduces the areas and the opening intervals."""
    from building_model import BuildingModel

    model, _report = map_proposal(_example())
    reloaded = BuildingModel.from_json(model.to_json())

    assert set(reloaded.spaces) == set(model.spaces)
    for space_id, space in model.spaces.items():
        other = reloaded.spaces[space_id]
        assert _close(other.area_m2, space.area_m2)
        assert _close(other.volume_m3, space.volume_m3)
        assert len(other.openings) == len(space.openings)
        for opening, expected in zip(other.openings, space.openings):
            assert opening.id == expected.id
            assert _close(opening.host_interval_m[0], expected.host_interval_m[0])
            assert _close(opening.host_interval_m[1], expected.host_interval_m[1])
            assert _close(opening.s_center_m, expected.s_center_m)
            assert opening.host_facade == expected.host_facade
            assert opening.adjacent_space_id == expected.adjacent_space_id


def test_mapped_model_passes_the_export_gate(tmp_path):
    """The mapped model goes out through the repo's own adapter and export gate."""
    from bem.bem_geometry import simplify_tolerance_pct
    from bem.export_gate import publish_exports
    from geometry.geometry_simplify import footprint_multipart_from_regions, simplify_footprint
    from run_pipeline import model_from_linked_model

    model, _report = map_proposal(_example())
    level_height = model.levels[0].wall_height_m

    parts = footprint_multipart_from_regions([sp.polygon_m for sp in model.spaces.values()])
    assert len(parts) == 2, "the two rooms are two footprint parts"
    simplified = simplify_footprint(parts, tol=0.02, wall_height=level_height)
    bem_model = model_from_linked_model(
        model=model,
        simplified_ring=simplified,
        wall_height_m=level_height,
        simplify_tolerance=simplify_tolerance_pct(0.02),
    )

    out_dir = tmp_path / "bem_out"
    gate = publish_exports(bem_model, out_dir, "proposal")
    assert gate.ok, {"errors": gate.errors, "failed_checks": gate.failed_checks}
    assert gate.gbxml_ok
    assert gate.ifc4_ok
    assert (out_dir / "proposal.xml").exists()
    assert (out_dir / "proposal.ifc").exists()
