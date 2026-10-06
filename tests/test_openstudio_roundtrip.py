"""OpenStudio round-trip gate for the gbXML export (#628, #627).

Every export shape we write (flat, gable, hip and shed roofs, with a window,
a door, a skylight and a shading device) must import into OpenStudio's gbXML
reverse translator with no translation errors, every surface and sub-surface
present, an enclosed space, and the space volume we computed. CI installs the
``openstudio`` wheel; locally these tests skip without it.
"""

from __future__ import annotations

import pytest

openstudio = pytest.importorskip("openstudio")

from bem_export import BEMOpeningUnit, write_gbxml  # noqa: E402
from bem_geometry import BEMShade  # noqa: E402
from bem_roof import shell_volume, space_shell  # noqa: E402
from tests.test_gbxml_sloped_roofs import BOX, VOL, H, bem, gable, hip, shed  # noqa: E402

# OpenStudio 3.x targets gbXML 7.03 and notes that a 6.01 file skips its own
# schema check; we validate against the 6.01 XSD ourselves (validate_gbxml).
ALLOWED = ("Version of schema specified",)

ROOFS = {"flat": list, "gable": gable, "hip": hip, "shed": shed}
N_ROOF = {"flat": 1, "gable": 2, "hip": 4, "shed": 1}


def _openings():
    return [
        BEMOpeningUnit(category="window", tag="W1", width_m=1.5, height_m=1.2, host_facade="south"),
        BEMOpeningUnit(category="door", tag="D1", width_m=0.9, height_m=2.1, host_facade="east"),
        BEMOpeningUnit(category="skylight", tag="SK-1", width_m=1.0, height_m=1.0),
    ]


def _shade():
    return BEMShade(
        id="OH-1",
        kind="overhang",
        host_wall_id="wall-001",
        vertices=[(1.0, 0.0, 2.4), (3.0, 0.0, 2.4), (3.0, -0.6, 2.4), (1.0, -0.6, 2.4)],
    )


def _load(path):
    openstudio.Logger.instance().standardOutLogger().disable()
    rt = openstudio.gbxml.GbXMLReverseTranslator()
    om = rt.loadModel(openstudio.path(str(path)))
    errors = [e.logMessage() for e in rt.errors() if not e.logMessage().startswith(ALLOWED)]
    return om, errors


@pytest.fixture(params=["flat", "gable", "hip", "shed"])
def imported(request, tmp_path):
    kind = request.param
    m = bem(ROOFS[kind](), openings=_openings())
    m.shades = [_shade()]
    path = tmp_path / f"{kind}.xml"
    write_gbxml(m, path)
    om, errors = _load(path)
    return kind, m, om, errors


def test_imports_with_no_translation_errors(imported):
    kind, _m, om, errors = imported
    assert om.is_initialized(), f"{kind}: OpenStudio could not load the file"
    assert errors == []


def test_every_surface_and_subsurface_arrives(imported):
    kind, _m, om, _ = imported
    mdl = om.get()
    types = {}
    for su in mdl.getSurfaces():
        types[su.surfaceType()] = types.get(su.surfaceType(), 0) + 1
    assert types == {"Wall": 4, "Floor": 1, "RoofCeiling": N_ROOF[kind]}
    subs = sorted(ss.subSurfaceType() for ss in mdl.getSubSurfaces())
    assert subs == ["Door", "FixedWindow", "Skylight"]
    assert len(mdl.getShadingSurfaces()) == 1


def test_space_is_enclosed_and_holds_our_volume(imported):
    kind, m, om, _ = imported
    sp = om.get().getSpaces()
    assert len(sp) == 1
    assert sp[0].isEnclosedVolume()
    want = VOL[kind] if kind != "flat" else 60.0 * H
    assert sp[0].volume() == pytest.approx(want, rel=1e-6)
    loops, _ = space_shell(BOX, m.roof_planes, H) if m.roof_planes else ([], None)
    if loops:
        assert sp[0].volume() == pytest.approx(shell_volume(loops), rel=1e-6)
    assert sp[0].floorArea() == pytest.approx(60.0, rel=1e-6)


def test_walls_are_outward_and_cover_the_envelope(imported):
    kind, _m, om, _ = imported
    mdl = om.get()
    walls = [su for su in mdl.getSurfaces() if su.surfaceType() == "Wall"]
    az = sorted(round(openstudio.radToDeg(w.azimuth())) % 360 for w in walls)
    assert az == [0, 90, 180, 270]
    # eave walls are 10 x 3 and 6 x 3; gable/hip/shed only add area above
    gross = sum(w.grossArea() for w in walls)
    assert gross >= 2 * (10 + 6) * H - 1e-6
