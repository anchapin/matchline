"""materials.lookup_conductivity and its use on IFC import (roadmap item 7)."""

from __future__ import annotations

import pytest

from materials import TABLE, lookup_conductivity


def _no_u(model):
    """No U-value anywhere: at most an unset-U wall-type construction (#747)."""
    return all(
        c.u_value_w_m2k is None and c.provenance.method == "ifc_import:tier0:wall_type"
        for c in model.constructions.values()
    )


@pytest.mark.parametrize(
    "name, eid",
    [
        ("Gypsum Board 16mm", "gypsum_board"),
        ("Brick, common", "brick"),
        ("Lightweight Concrete", "lw_concrete"),
        ("Concrete Block - Lightweight", "lw_concrete_block"),
        ("Concrete, cast in place", "concrete"),
        ("XPS Insulation 50mm", "xps"),
        ("Mineral Wool Batt", "mineral_wool"),
    ],
)
def test_known_names_resolve(name, eid):
    assert lookup_conductivity(name).id == eid


@pytest.mark.parametrize(
    "name",
    [
        "Timber frame with mineral wool",  # two materials disagree
        "Air cavity",
        "Unvented air gap 25mm",
        "Unobtainium",
        "Aspiring cladding",  # "pir" only inside a word
        "",
    ],
)
def test_unknown_ambiguous_or_air_gives_none(name):
    assert lookup_conductivity(name) is None


def test_table_is_well_formed():
    assert len({e.id for e in TABLE}) == len(TABLE)
    assert all(e.conductivity_w_mk > 0 and e.keywords and e.source_material for e in TABLE)


# --- import integration -----------------------------------------------------

pytest.importorskip("ifcopenshell")

from tests.test_ifc_import_layered_wall_u import WALL, _import, _u  # noqa: E402

NAMED = [
    ("Brick", 0.10, None, False),
    ("Mineral Wool", 0.10, None, False),
    ("Gypsum Board", 0.0125, None, False),
]
LOOKED_UP = [
    ("Brick", 0.10, 0.89, False),
    ("Mineral Wool", 0.10, 0.05, False),
    ("Gypsum Board", 0.0125, 0.16, False),
]


def test_named_layers_get_a_looked_up_u(tmp_path):
    back = _import(tmp_path, NAMED)
    (c,) = back.constructions.values()
    assert c.id.startswith("IFC-UM")
    assert c.u_value_w_m2k == pytest.approx(_u(LOOKED_UP), abs=1e-6)
    assert all(w.construction_id == c.id for w in back.envelope)


def test_lookup_provenance_is_lower_confidence_and_names_materials(tmp_path):
    (c,) = _import(tmp_path, NAMED).constructions.values()
    assert c.provenance.method == "ifc_import:tier0:wall_u_lookup"
    assert c.provenance.confidence == pytest.approx(0.6)
    for frag in ("'Brick'->brick", "'Mineral Wool'->mineral_wool", "ASHRAE HOF 2005", "GlobalId="):
        assert frag in c.provenance.note


def test_file_conductivity_wins_per_layer(tmp_path):
    layers = [WALL[0]] + NAMED[1:]  # brick k=0.77 stated, others looked up
    (c,) = _import(tmp_path, layers).constructions.values()
    want = [WALL[0]] + LOOKED_UP[1:]
    assert c.u_value_w_m2k == pytest.approx(_u(want), abs=1e-6)
    assert "'Brick'" not in c.provenance.note


def test_one_unknown_name_still_means_no_value(tmp_path):
    layers = NAMED[:1] + [("Mystery board", 0.05, None, False)] + NAMED[1:]
    assert _no_u(_import(tmp_path, layers))
