"""Storey by elevation for elements no storey contains (#585).

Idea from Pascal's storey-semantics.ts (MIT, Copyright (c) 2026 Pascal
Group Inc., commit 67f8041). matchline assigns only when exactly one storey
band fits and reports the rest; it never falls back to the lowest storey.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.aggregate as agg  # noqa: E402
import ifcopenshell.api.spatial as sp  # noqa: E402
import ifcopenshell.guid as guid  # noqa: E402

from ifc_import import _storey_by_elevation, import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _place  # noqa: E402
from tests.test_ifc_import import _add_solid, _rect_profile, make_ifc_fixture  # noqa: E402


def _two_storeys(tmp_path, walls, l2_elevations=(3.0,)):
    """Fixture plus extra storeys at ``l2_elevations``; ``walls`` is a list of
    (name, z, container) with container "building", "site" or None."""
    path = make_ifc_fixture(tmp_path / "base.ifc")
    f = ifcopenshell.open(str(path))
    bldg = f.by_type("IfcBuilding")[0]
    site = f.by_type("IfcSite")[0]
    for k, e in enumerate(l2_elevations):
        st = f.create_entity(
            "IfcBuildingStorey", GlobalId=guid.new(), Name=f"Upper {k + 1}", Elevation=e
        )
        st.ObjectPlacement = _place(f, (0, 0, e), bldg.ObjectPlacement)
        agg.assign_object(f, products=[st], relating_object=bldg)
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    made = {}
    for name, z, where in walls:
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, (2, 4, z), bldg.ObjectPlacement, refdir=(1, 0, 0))
        _add_solid(f, w, _rect_profile(f, 3.0, 0.1, ox=1.5), 2.5, body)
        if where:
            sp.assign_container(
                f, products=[w], relating_structure=bldg if where == "building" else site
            )
        made[name] = w.GlobalId
    out = tmp_path / "storeys.ifc"
    f.write(str(out))
    return out, made


def _el(model, gid):
    return next((e for e in model.bim_elements if e.global_id == gid), None)


def test_building_contained_wall_lands_on_storey_two(tmp_path):
    path, gids = _two_storeys(tmp_path, [("Up", 3.0, "building")])
    m = import_ifc(path)
    e = _el(m, gids["Up"])
    assert e is not None and e.level_id == "L2"
    assert e.storey_method == "elevation"
    assert e.provenance.confidence <= 0.6
    assert "storey from placement height" in e.provenance.note
    assert "storey fallback: 1 by elevation" in m.revision_log[-1].note


def test_just_under_a_storey_counts_as_that_storey(tmp_path):
    path, gids = _two_storeys(tmp_path, [("Sill", 2.95, "site")])
    assert _el(import_ifc(path), gids["Sill"]).level_id == "L2"


def test_wall_with_no_containment_at_all_is_read(tmp_path):
    path, gids = _two_storeys(tmp_path, [("Loose", 0.0, None)])
    e = _el(import_ifc(path), gids["Loose"])
    assert e.level_id == "L1" and e.storey_method == "elevation"


def test_below_lowest_storey_is_reported_not_dropped_silently(tmp_path):
    path, gids = _two_storeys(tmp_path, [("Pit", -1.0, "building")])
    m = import_ifc(path)
    assert _el(m, gids["Pit"]) is None
    note = m.revision_log[-1].note
    assert "storey fallback: 0 by elevation, 1 unassigned" in note
    assert f"{gids['Pit']} (below)" in note


def test_two_storeys_at_one_elevation_leave_it_unassigned(tmp_path):
    path, gids = _two_storeys(tmp_path, [("Twin", 3.2, "building")], l2_elevations=(3.0, 3.0))
    m = import_ifc(path)
    assert _el(m, gids["Twin"]) is None
    assert f"{gids['Twin']} (tie)" in m.revision_log[-1].note


def test_contained_elements_are_untouched(tmp_path):
    m = import_ifc(make_ifc_fixture(tmp_path / "plain.ifc"))
    assert all(e.storey_method == "" for e in m.bim_elements)
    assert "storey fallback" not in m.revision_log[-1].note


@pytest.mark.parametrize(
    "z,elevs,want",
    [
        (0.0, [0.0, 3.0], (0, "")),
        (2.89, [0.0, 3.0], (0, "")),
        (2.91, [0.0, 3.0], (1, "")),
        (40.0, [0.0, 3.0], (1, "")),
        (-0.05, [0.0, 3.0], (0, "")),
        (-0.5, [0.0, 3.0], (None, "below")),
        (3.5, [0.0, 3.0, 3.0], (None, "tie")),
        (1.0, [0.0, 3.0, 3.0], (0, "")),
    ],
)
def test_storey_band_rule(z, elevs, want):
    assert _storey_by_elevation(z, elevs) == want
