"""Tests for multi-ring BEM export (#984): courtyard holes and multi-wing."""

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from bem.bem_export import write_gbxml
from building_model import BuildingModel, Level, Provenance, Space
from geometry.geometry_simplify import footprint_parts_from_regions, simplify_footprint
from run_pipeline import model_from_linked_model

H = 3.0


def P(note=""):
    return Provenance(sheet_id="arch_A101", revision=1, method="test", confidence=1.0, note=note)


def _space(sid, poly, area):
    return Space(
        id=sid,
        level_id="L1",
        name="ROOM",
        number=sid.split("-")[-1],
        polygon_m=[list(p) for p in poly],
        area_m2=area,
        volume_m3=area * H,
        core_provenance=P(),
        label_confidence=1.0,
    )


def _model(spaces):
    m = BuildingModel(name="multiring_test")
    m.levels.append(Level(id="L1", name="Level 1", wall_height_m=H))
    for sp in spaces:
        m.spaces[sp.id] = sp
    return m


def _courtyard_model():
    return _model(
        [
            _space("L1-101", [(0, 0), (10, 0), (10, 4), (0, 4)], 40.0),
            _space("L1-102", [(0, 6), (10, 6), (10, 10), (0, 10)], 40.0),
            _space("L1-103", [(0, 4), (4, 4), (4, 6), (0, 6)], 8.0),
            _space("L1-104", [(6, 4), (10, 4), (10, 6), (6, 6)], 8.0),
        ]
    )


def _two_wing_model():
    return _model(
        [
            _space("L1-101", [(0, 0), (10, 0), (10, 4), (0, 4)], 40.0),
            _space("L1-102", [(20, 0), (30, 0), (30, 4), (20, 4)], 40.0),
        ]
    )


def _surface_counts(path):
    tree = ET.parse(str(path))
    counts = {}
    for el in tree.iter():
        if el.tag.split("}")[-1] == "Surface":
            st = el.get("surfaceType")
            counts[st] = counts.get(st, 0) + 1
    return counts


def _bem_for(model):
    parts = footprint_parts_from_regions([sp.polygon_m for sp in model.spaces.values()])
    sres = simplify_footprint(parts, tol=0.02, wall_height=H)
    return model_from_linked_model(
        model=model, simplified_ring=sres, wall_height_m=H, simplify_tolerance=2.0
    )


def test_courtyard_exports_all_rings():
    """#984: a courtyard building exports outer walls, courtyard walls, and
    per-band roofs with nothing over the void."""
    bem = _bem_for(_courtyard_model())
    assert len(bem.rings_m) == 1
    assert len(bem.rings_m[0]["holes"]) == 1
    hole = bem.rings_m[0]["holes"][0]
    # hole wound CW in the BEM frame: outward normals point into the void
    a2 = sum(
        hole[i][0] * hole[(i + 1) % len(hole)][1] - hole[(i + 1) % len(hole)][0] * hole[i][1]
        for i in range(len(hole))
    )
    assert a2 < 0
    with tempfile.TemporaryDirectory() as d:
        path = write_gbxml(bem, Path(d) / "model.gbxml")
        counts = _surface_counts(path)
    # the simplified 4-corner outer ring is split per adjacent space
    # (east 3, west 3, north 1, south 1 = 8 pieces) plus 4 courtyard walls
    assert counts["ExteriorWall"] == 12
    assert counts["Roof"] == 4  # one per band; nothing over the courtyard
    assert counts["SlabOnGrade"] == 4


def test_two_wing_exports_both_wings():
    """#984: a two-wing building exports walls and roofs for BOTH wings."""
    bem = _bem_for(_two_wing_model())
    assert len(bem.rings_m) == 2
    assert all(not part["holes"] for part in bem.rings_m)
    with tempfile.TemporaryDirectory() as d:
        path = write_gbxml(bem, Path(d) / "model.gbxml")
        counts = _surface_counts(path)
    assert counts["ExteriorWall"] == 8  # 4 per wing
    assert counts["Roof"] == 2  # one per wing (per-space, per part)
    assert counts["SlabOnGrade"] == 2


def test_single_part_model_has_no_rings_m():
    """#984: a single-part footprint keeps the pre-#984 shape."""
    bem = _bem_for(_model([_space("L1-101", [(0, 0), (10, 0), (10, 4), (0, 4)], 40.0)]))
    assert bem.rings_m == []
