"""IFC lining and hidden walls stay out of the envelope (#577)."""

import pytest

from ifc_wall_linings import MAX_CLADDING_THICKNESS_M, find_linings


def _w(key, a, b, t, level="L1"):
    return (key, level, a, b, t)


def test_tile_on_partition_face_is_a_lining():
    # 12 mm tile on the face of a 200 mm partition: shorter, face to face
    walls = [
        _w("P", (0, 0), (4, 0), 0.2),
        _w("T", (0.5, 0.106), (3.5, 0.106), 0.012),
    ]
    assert find_linings(walls) == {"T": ("P", "face to face")}


def test_tile_across_two_partitions_is_cladding():
    # one 6 m tile run over two 4 m partitions: longer than either host, so
    # only the cladding test (half its length on thick hosts) catches it
    walls = [
        _w("P1", (0, 0), (4, 0), 0.2),
        _w("P2", (4, 0), (8, 0), 0.2),
        _w("T", (1, 0.106), (7, 0.106), 0.012),
    ]
    assert find_linings(walls) == {"T": ("P1", "cladding")}


def test_wall_buried_in_thicker_wall_is_embedded():
    # 100 mm wall inside a 300 mm wall
    walls = [
        _w("H", (0, 0), (6, 0), 0.3),
        _w("B", (1, 0.05), (3, 0.05), 0.1),
    ]
    assert find_linings(walls) == {"B": ("H", "embedded")}


def test_shorter_wall_face_to_face_is_lining():
    walls = [
        _w("H", (0, 0), (6, 0), 0.2),
        _w("F", (1, 0.15), (5, 0.15), 0.1),  # faces touch at y=0.1
    ]
    assert find_linings(walls) == {"F": ("H", "face to face")}


def test_equal_length_double_partition_is_kept():
    walls = [_w("A", (0, 0), (6, 0), 0.2), _w("B", (0, 0.2), (6, 0.2), 0.2)]
    assert find_linings(walls) == {}


def test_perpendicular_and_distant_walls_are_not_linings():
    walls = [
        _w("H", (0, 0), (6, 0), 0.2),
        _w("X", (3, 0.1), (3, 4), 0.012),  # perpendicular
        _w("D", (1, 1.0), (3, 1.0), 0.012),  # parallel, 1 m away
    ]
    assert find_linings(walls) == {}


def test_thick_facing_is_not_cladding():
    # 50 mm facing along a longer wall's face but only 40% overlap: neither
    # cladding (too thick) nor a face-to-face lining (overlap too short)
    walls = [_w("H", (0, 0), (6, 0), 0.2), _w("F", (4, 0.125), (9, 0.125), 0.05)]
    assert MAX_CLADDING_THICKNESS_M < 0.05
    assert find_linings(walls) == {}


def test_cladding_needs_half_its_length_on_a_host():
    walls = [_w("H", (0, 0), (2, 0), 0.2), _w("T", (1, 0.106), (5, 0.106), 0.012)]
    assert find_linings(walls) == {}  # only 1 of 4 m lies on the host


def test_unknown_thickness_takes_no_part():
    walls = [_w("H", (0, 0), (6, 0), None), _w("T", (1, 0.106), (3, 0.106), 0.012)]
    assert find_linings(walls) == {}
    walls = [_w("H", (0, 0), (6, 0), 0.2), _w("T", (1, 0.106), (3, 0.106), None)]
    assert find_linings(walls) == {}


def test_other_level_is_ignored():
    walls = [
        _w("H", (0, 0), (6, 0), 0.3),
        _w("B", (1, 0), (3, 0), 0.1, level="L2"),
    ]
    assert find_linings(walls) == {}


def _ifc_with(tmp_path, extra):
    """Base fixture plus extra IfcWalls: (name, ifc_start, refdir, length, thickness, centred)."""
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.spatial as sp
    import ifcopenshell.guid as guid

    from tests.test_ifc_closet_merge import _place
    from tests.test_ifc_import import _add_solid, _rect_profile, make_ifc_fixture

    path = tmp_path / "base.ifc"
    make_ifc_fixture(path)
    if not extra:
        return path
    f = ifcopenshell.open(str(path))
    storey = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    walls = []
    for name, start, ref, length, t in extra:
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        _add_solid(f, w, _rect_profile(f, length, t, ox=length / 2), 3.0, body)
        walls.append(w)
    sp.assign_container(f, products=walls, relating_structure=storey)
    out = tmp_path / "with_linings.ifc"
    f.write(str(out))
    return out


def _exterior_area(model):
    return round(sum(e.area_m2 or 0 for e in model.envelope if e.facade), 4)


def test_ifc_cladding_and_buried_wall_leave_envelope_area_unchanged(tmp_path):
    from ifc_import import import_ifc

    base = import_ifc(_ifc_with(tmp_path, ()))
    # the base south wall runs (0,0)->(20,0) in IFC, 200 mm thick; outside is
    # IFC y < 0. A 12 mm rainscreen panel on its outer face and a 100 mm wall
    # buried in its body both read as exterior walls without #577.
    lined = import_ifc(
        _ifc_with(
            tmp_path,
            (
                ("Tile", (2, -0.106, 0), (1, 0, 0), 6.0, 0.012),
                ("Buried", (12, 0, 0), (1, 0, 0), 4.0, 0.1),
            ),
        )
    )
    assert _exterior_area(lined) == _exterior_area(base)
    assert len(lined.envelope) == len(base.envelope)
    roles = {e.name: e.role for e in lined.bim_elements if e.name in ("Tile", "Buried")}
    assert roles == {"Tile": "lining", "Buried": "lining"}
    notes = {e.name: e.provenance.note for e in lined.bim_elements if e.name in roles}
    assert "(face to face)" in notes["Tile"] and "(embedded)" in notes["Buried"]
    assert "linings: 2 walls kept out of the envelope" in lined.revision_log[-1].note


def test_plain_fixture_has_no_linings(tmp_path):
    from ifc_import import import_ifc

    model = import_ifc(_ifc_with(tmp_path, ()))
    assert all(e.role == "" for e in model.bim_elements)
    assert "linings:" not in model.revision_log[-1].note


def test_lining_that_hosts_an_opening_is_kept(tmp_path):
    from building_model import BimElement, BimOpening, BuildingModel, EnvelopeWall, Provenance
    from ifc_wall_linings import exclude_linings

    def prov(g):
        return Provenance(
            sheet_id="s",
            revision="r",
            method="ifc_import:tier0:envelope",
            confidence=0.95,
            note=f"GlobalId={g} x",
        )

    m = BuildingModel()
    m.envelope = [
        EnvelopeWall("L1-EW1", "", [0, 0], [6, 0], 6, 3, 18, prov("H")),
        EnvelopeWall("L1-EW2", "", [1, 0.106], [3, 0.106], 2, 3, 6, prov("T")),
    ]
    m.bim_elements = [
        BimElement("H", "IfcWall", thickness_m=0.2),
        BimElement("T", "IfcWall", thickness_m=0.012, openings=[BimOpening("O", "window")]),
    ]
    counts = exclude_linings(m, {"H": 0.2, "T": 0.012})
    assert counts == {"lining": 0, "kept": 1}
    assert len(m.envelope) == 2
    assert "hosts 1 opening(s)" in m.envelope[1].provenance.note
    assert m.bim_elements[1].role == ""


def test_role_round_trips_through_json():
    from building_model import BimElement, BuildingModel

    m = BuildingModel()
    m.bim_elements = [BimElement("T", "IfcWall", role="lining")]
    back = BuildingModel.from_json(m.to_json())
    assert back.bim_elements[0].role == "lining"
