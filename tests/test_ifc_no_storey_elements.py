"""Storey-less structural IFCs import their site-contained walls (#1262).

Structural IFCs (GNI 2026 gni26-p5-str) carry their IfcWalls directly under
IfcSite: no IfcBuilding, no IfcBuildingStorey, no IfcSpace. The #928
fallback synthesizes storey bands from IfcSpaces, so with no spaces it
returned [] and the walk imported nothing at all; the file's pset-external
walls scored 0 m against 53.8 m truth. The fallback now synthesizes the
bands from the storey-less elements' placement z, and the elements flow
through the by-elevation fallback (#585) exactly as site-contained
elements do when spaces exist.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api  # noqa: E402
import ifcopenshell.api.root as _Root  # noqa: E402
import ifcopenshell.guid as _guid  # noqa: E402

from ifc.ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder  # noqa: E402


def _site_wall(b: IfcBuilder, site, name: str, start, end, z: float = 0.0):
    """A wall held by ``site`` (not a storey), pset-flagged IsExternal."""
    (x0, y0), (x1, y1) = start, end
    length = math.hypot(x1 - x0, y1 - y0)
    direction = ((x1 - x0) / length, (y1 - y0) / length, 0.0)
    wall = _Root.create_entity(b.f, ifc_class="IfcWall", name=name)
    wall.ObjectPlacement = b._placement(
        (x0, y0, z), site.ObjectPlacement, refdir=direction
    )
    b._solid(wall, b._rect(length, 0.2, length / 2, 0.0), 3.0)
    b.f.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=_guid.new(),
        RelatedElements=[wall],
        RelatingStructure=site,
    )
    pset = ifcopenshell.api.run("pset.add_pset", b.f, product=wall, name="Pset_WallCommon")
    ifcopenshell.api.run(
        "pset.edit_pset", b.f, pset=pset, properties={"IsExternal": True}
    )
    return wall


def _site_only_file(path, walls):
    """Strip the builder's building/storey, keep an IfcSite holding walls."""
    b = IfcBuilder()
    site = b.f.by_type("IfcSite")[0]
    building = b.f.by_type("IfcBuilding")[0]
    storey = b.f.by_type("IfcBuildingStorey")[0]
    made = [_site_wall(b, site, name, start, end, z=z) for name, start, end, z in walls]
    b.f.remove(building)
    b.f.remove(storey)
    b.write(path)
    return b, made


def test_no_storey_no_space_file_imports_site_walls(tmp_path):
    p = tmp_path / "a.ifc"
    _site_only_file(
        str(p),
        [
            ("W0", (0, 0), (6, 0), 0.0),
            ("W1", (0, 4), (6, 4), 0.0),
        ],
    )
    model = import_ifc(str(p))
    assert not model.spaces
    walls = model.envelope or []
    assert len(walls) == 2, "site-contained walls must import without storeys"
    for wall in walls:
        assert wall.provenance.method == "ifc_import:tier0:envelope"
        assert abs((wall.length_m or 0.0) - 6.0) < 1e-3
    assert model.levels, "a synthesized storey stand-in must create a level"


def test_element_z_bands_become_separate_levels(tmp_path):
    p = tmp_path / "b.ifc"
    _site_only_file(
        str(p),
        [
            ("LOW", (0, 0), (6, 0), 0.0),
            ("HIGH", (0, 4), (6, 4), 3.0),
        ],
    )
    model = import_ifc(str(p))
    assert len(model.levels) == 2, "z bands 0 m and 3 m make two storey stand-ins"
    bim_walls = [
        el for el in (model.bim_elements or []) if el.ifc_class.startswith("IfcWall")
    ]
    levels = {el.level_id for el in bim_walls}
    assert levels == {"L1", "L2"}, "each wall lands on its own synthesized level"
    assert len(model.envelope) == 2
    assert abs(sum((w.length_m or 0.0) for w in model.envelope) - 12.0) < 1e-3


def test_truly_empty_site_file_imports_empty(tmp_path):
    p = tmp_path / "c.ifc"
    _site_only_file(str(p), [])
    model = import_ifc(str(p))
    assert not model.spaces
    assert not model.envelope
    assert not model.levels
