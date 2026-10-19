"""Semi-exterior and vestibule interior walls reach the IFC4 export (#747)."""

from __future__ import annotations

import pytest

from bem.bem_ifc4 import write_ifc4
from building_model import Construction, Provenance
from constructions import semi_exterior_walls
from ifc_export import _bem_from_model
from ifc_import import import_ifc
from tests.model_factory import make_clean_model

CID = "t55-vest-wall"


def _prov():
    return Provenance(sheet_id="arch_A101", revision=1, method="test", confidence=1.0, note="test")


def _building_model():
    m = make_clean_model()
    m.constructions[CID] = Construction(
        id=CID, name="Vestibule wall", u_value_w_m2k=0.477, provenance=_prov()
    )
    # one settled vestibule sentence record: the partition between the rooms
    m.semi_exterior.append(
        {
            "spaces": ["L1-102", "L1-101"],
            "categories": ["conditioned", "semiheated"],
            "kind": "vestibule",
            "reason": "5.5.2 vestibule sentence",
            "length_m": 6.0,
            "segments": [[[5, 0], [5, 6]]],
            "plane": "wall",
            "construction_id": CID,
        }
    )
    return m


def _write(m, tmp_path, name="b.ifc"):
    bem = _bem_from_model(m)
    bem.interior_wall_constructions = semi_exterior_walls(m)
    p = tmp_path / name
    write_ifc4(bem, p)
    return p


def _interior_walls(p):
    import ifcopenshell

    f = ifcopenshell.open(str(p))
    return f, [w for w in f.by_type("IfcWall") if (w.Name or "").startswith("InteriorWall-")]


def test_exporter_view_carries_y_north_geometry():
    m = _building_model()
    out = semi_exterior_walls(m)
    info = out[CID]
    assert info["pairs"] == [["L1-101", "L1-102"]]
    assert info["geometry"] == [
        {"pair": ["L1-101", "L1-102"], "segments": [[(5.0, -0.0), (5.0, -6.0)]]}
    ]


def test_exporter_view_skips_floors_and_reviews(tmp_path):
    m = _building_model()
    m.semi_exterior.append(
        {
            "spaces": ["L1-101", "L1-102"],
            "categories": ["semiheated", "conditioned"],
            "kind": "semi_exterior",
            "reason": "review",
            "length_m": 6.0,
            "segments": [[[5, 0], [5, 6]]],
            "plane": "floor",
            "construction_id": CID,
        }
    )
    out = semi_exterior_walls(m)
    # the floor record names the same construction but must not add geometry
    assert len(out[CID]["geometry"]) == 1
    m2 = _building_model()
    m2.semi_exterior[0]["kind"] = "review"
    assert semi_exterior_walls(m2) == {}


def test_write_ifc4_emits_interior_walls_with_their_construction(tmp_path):
    p = _write(_building_model(), tmp_path)
    f, walls = _interior_walls(p)
    assert [w.Name for w in walls] == ["InteriorWall-L1-101-L1-102-1"]
    import ifcopenshell.util.element as _El

    props = (_El.get_psets(walls[0]) or {}).get("Pset_WallCommon") or {}
    assert props.get("Reference") == CID
    assert props.get("ThermalTransmittance") == pytest.approx(0.477)
    assert props.get("IsExternal") in (False, ".F.", "FALSE", "False")


def test_write_ifc4_without_interior_constructions_is_unchanged(tmp_path):
    m = _building_model()
    m.semi_exterior = []  # nothing settled: no interior walls, no error
    p = _write(m, tmp_path)
    _, walls = _interior_walls(p)
    assert walls == []


def test_reimport_does_not_count_interior_walls_as_envelope(tmp_path):
    p = _write(_building_model(), tmp_path)
    m2 = import_ifc(str(p))
    # the partition is imported as an element but is not envelope
    interior_ids = [
        w.id for w in m2.envelope if (w.from_m or []) and list(w.from_m) in ([5.0, 0.0], [5.0, 6.0])
    ]
    assert interior_ids == []
    # the outer ring walls still are (make_clean_model's four runs)
    assert len(m2.envelope) >= 4


def test_wall_without_the_pset_still_lands_in_the_envelope(tmp_path):
    m = make_clean_model()  # no semi_exterior records at all
    bem = _bem_from_model(m)
    p = tmp_path / "plain.ifc"
    write_ifc4(bem, p)
    m2 = import_ifc(str(p))
    assert m2.envelope, "ring walls should still count as envelope"
