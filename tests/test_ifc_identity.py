"""matchline ids survive IFC export and re-import (#588).

Exported spaces and openings carry a Matchline_Identity property set; import
prefers those ids and skips reconstruction heuristics (name-based closet/shaft
classification, closet/shaft merging, duplicate-opening removal) for them.
Idea from Pascal's "Pascal round trip" / isPascalAuthored (MIT, Copyright (c)
2026 Pascal Group Inc., commit 67f8041).
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.util.element as El  # noqa: E402

from ifc_export import _export_ifc  # noqa: E402
from ifc_import import IDENTITY_PSET, _drop_duplicate_openings, import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402


def _ids(m):
    return {s.id: sorted(o.id for o in s.openings) for s in m.spaces.values()}


def _round_trip(tmp_path, edit=None):
    b = IfcBuilder()
    box_plan(b)
    m1 = import_ifc(b.write(tmp_path / "a.ifc"))
    if edit:
        edit(m1)
    out = tmp_path / "b.ifc"
    _export_ifc(m1, out)
    return m1, import_ifc(out), out


def test_export_writes_identity_on_spaces_and_openings(tmp_path):
    m1, _, out = _round_trip(tmp_path)
    f = ifcopenshell.open(str(out))
    sp = f.by_type("IfcSpace")[0]
    props = El.get_psets(sp)[IDENTITY_PSET]
    assert props["MatchlineId"] == "L1-101"
    assert props["SourceMethod"] == m1.spaces["L1-101"].core_provenance.method
    assert props["PolyType"] == "room"
    want = {o.id for s in m1.spaces.values() for o in s.openings}
    got = {El.get_psets(o)[IDENTITY_PSET]["MatchlineId"] for o in f.by_type("IfcOpeningElement")}
    assert got == want
    assert not any(
        n.startswith("Pset_Matchline") for e in f.by_type("IfcSpace") for n in El.get_psets(e)
    )


def test_round_trip_keeps_space_and_opening_ids(tmp_path):
    m1, m2, _ = _round_trip(tmp_path)
    assert _ids(m2) == _ids(m1)
    note = m2.spaces["L1-101"].core_provenance.note
    assert f"id L1-101 from {IDENTITY_PSET}" in note


def test_round_trip_keeps_merged_from_and_poly_type(tmp_path):
    def edit(m):
        sp = m.spaces["L1-101"]
        sp.merged_from = ["L1-101A", "L1-SHAFT-1"]

    _, m2, _ = _round_trip(tmp_path, edit)
    sp = m2.spaces["L1-101"]
    assert sp.merged_from == ["L1-101A", "L1-SHAFT-1"]
    assert sp.poly_type == "room"


def test_authored_closet_keeps_its_type_and_is_not_merged(tmp_path):
    def edit(m):
        m.spaces["L1-101"].poly_type = "closet"

    _, m2, _ = _round_trip(tmp_path, edit)
    assert "L1-101" in m2.spaces
    assert m2.spaces["L1-101"].poly_type == "closet"


def test_round_trip_is_stable_across_imports(tmp_path):
    _, m2, out = _round_trip(tmp_path)
    assert _ids(import_ifc(out)) == _ids(m2)


def test_file_without_identity_keeps_derived_ids(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    path = b.write(tmp_path / "a.ifc")
    f = ifcopenshell.open(str(path))
    assert not any(IDENTITY_PSET in El.get_psets(e) for e in f.by_type("IfcSpace"))
    m = import_ifc(path)
    gids = {o.GlobalId for o in f.by_type("IfcOpeningElement")}
    assert {o.id for s in m.spaces.values() for o in s.openings} <= gids
    assert not [r for r in m.review_queue if r.kind == "matchline_identity"]


def _duplicate_opening_identity(tmp_path, gids=None):
    """Round-trip, give the second opening the first one's id, re-import.

    ``gids`` optionally pins the two openings' GlobalIds so the test controls
    which one sorts first.
    """
    _, _, out = _round_trip(tmp_path)
    f = ifcopenshell.open(str(out))
    ops = f.by_type("IfcOpeningElement")
    assert len(ops) >= 2
    first = El.get_psets(ops[0])[IDENTITY_PSET]["MatchlineId"]
    import ifcopenshell.api.pset as Ps

    for el in (ops[1], *[r.RelatedBuildingElement for r in ops[1].HasFillings]):
        pset = f.by_id(El.get_psets(el)[IDENTITY_PSET]["id"])
        Ps.edit_pset(f, pset=pset, properties={"MatchlineId": first})
    if gids is not None:
        ops[0].GlobalId, ops[1].GlobalId = gids
    pair = (ops[0].GlobalId, ops[1].GlobalId)
    f.write(str(tmp_path / "dup.ifc"))
    return import_ifc(tmp_path / "dup.ifc"), first, pair


def _assert_lowest_globalid_keeps_id(m, mid, pair):
    keeper, loser = sorted(pair)
    ids = [o.id for s in m.spaces.values() for o in s.openings]
    assert ids.count(mid) == 1
    assert loser in ids and keeper not in ids
    items = [r for r in m.review_queue if r.kind == "matchline_identity"]
    assert len(items) == 1 and loser in items[0].description


def test_duplicate_identity_keeps_globalid_and_flags_review(tmp_path):
    m, mid, pair = _duplicate_opening_identity(tmp_path)
    _assert_lowest_globalid_keeps_id(m, mid, pair)


@pytest.mark.parametrize(
    "gids",
    [
        ("0000000000000000000001", "3zzzzzzzzzzzzzzzzzzzzz"),
        ("3zzzzzzzzzzzzzzzzzzzzz", "0000000000000000000001"),
    ],
)
def test_duplicate_opening_identity_goes_to_lowest_globalid(tmp_path, gids):
    # either opening may be reached first; the lowest GlobalId keeps the id (#636)
    m, mid, pair = _duplicate_opening_identity(tmp_path, gids)
    assert pair == gids
    _assert_lowest_globalid_keeps_id(m, mid, pair)


@pytest.mark.parametrize("swap", [False, True])
def test_duplicate_space_identity_goes_to_lowest_globalid(tmp_path, swap):
    b = IfcBuilder()
    box_plan(b)
    b.space("ROOM 102", [(10, 0), (16, 0), (16, 6), (10, 6)])
    out = tmp_path / "b.ifc"
    _export_ifc(import_ifc(b.write(tmp_path / "a.ifc")), out)
    f = ifcopenshell.open(str(out))
    sps = [sp for sp in f.by_type("IfcSpace") if IDENTITY_PSET in El.get_psets(sp)]
    assert len(sps) >= 2
    a, b = sps[0], sps[1]
    mid = El.get_psets(a)[IDENTITY_PSET]["MatchlineId"]
    import ifcopenshell.api.pset as Ps

    pset = f.by_id(El.get_psets(b)[IDENTITY_PSET]["id"])
    Ps.edit_pset(f, pset=pset, properties={"MatchlineId": mid})
    low, high = "0000000000000000000001", "3zzzzzzzzzzzzzzzzzzzzz"
    a.GlobalId, b.GlobalId = (high, low) if swap else (low, high)
    f.write(str(tmp_path / "dup.ifc"))
    m = import_ifc(tmp_path / "dup.ifc")
    keeper, loser = (b, a) if swap else (a, b)
    assert mid in m.spaces and len(m.spaces) == 2
    items = [r for r in m.review_queue if r.kind == "matchline_identity"]
    assert len(items) == 1
    assert loser.GlobalId in items[0].description
    assert keeper.GlobalId not in items[0].description


class _Op:
    def __init__(self, oid):
        self.id = oid
        self.category = "window"
        self.host_global_id = "W"
        self.s_center_m = 1.0
        self.width_m = 1.0
        self.height_m = 1.0
        self.sill_m = 0.9
        self.provenance = None


def test_authored_openings_are_never_dropped_as_duplicates():
    a, b = _Op("south-W1"), _Op("south-W2")
    kept, dup = _drop_duplicate_openings([a, b], keep={"south-W1", "south-W2"})
    assert [o.id for o in kept] == ["south-W1", "south-W2"] and not dup
