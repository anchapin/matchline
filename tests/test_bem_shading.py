"""Roadmap item 5, wave 2: shading surfaces into gbXML as Shade surfaces."""

import xml.etree.ElementTree as ET

import pytest

from bem_export import GBXML_NS, validate_gbxml, write_gbxml
from bem_shading import shades_from_model
from ifc_export import _bem_from_model
from run_pipeline import model_from_linked_model
from tests.model_factory import add_shading, make_clean_model

NS = {"g": GBXML_NS}


def _shaded():
    m = make_clean_model()
    add_shading(m)
    return m


def _by_id(bem):
    return {s.id: s for s in bem.shades}


def _area2_xy(verts):
    return sum(
        verts[i][0] * verts[(i + 1) % len(verts)][1] - verts[(i + 1) % len(verts)][0] * verts[i][1]
        for i in range(len(verts))
    )


def test_overhang_projects_outward_from_the_south_face_north_up_frame():
    sh = _by_id(_bem_from_model(_shaded()))["SH-OVH-1"]
    # canonical south wall is y=6 (y-down); north-up flips it to y=-6 and
    # the building lies at y in [-6, 0], so outward is -y
    ys = sorted({round(v[1], 6) for v in sh.vertices})
    assert ys == [-6.6, -6.0]
    assert {round(v[2], 6) for v in sh.vertices} == {2.2}
    xs = sorted({round(v[0], 6) for v in sh.vertices})
    assert xs == [0.5, 4.5]
    assert _area2_xy(sh.vertices) > 0  # CCW from above: normal up


def test_fin_is_vertical_and_off_the_east_face():
    m = _shaded()
    sh = _by_id(_bem_from_model(m))["SH-FIN-1"]
    assert sorted({round(v[0], 6) for v in sh.vertices}) == [10.0, 10.4]
    h = m.envelope[0].height_m
    assert sorted({round(v[2], 6) for v in sh.vertices}) == [0.0, round(h, 6)]


def test_linked_model_adapter_places_shades_in_its_own_frame():
    m = _shaded()
    bem = model_from_linked_model(
        m, [[0, 0], [10, 0], [10, 6], [0, 6]], wall_height_m=3.0, simplify_tolerance=2.0
    )
    sh = _by_id(bem)["SH-OVH-1"]
    # this adapter keeps the linked frame, building at y in [0, 6]: outward +y
    assert sorted({round(v[1], 6) for v in sh.vertices}) == [6.0, 6.6]


def test_gbxml_carries_shade_surfaces_and_validates(tmp_path):
    bem = _bem_from_model(_shaded())
    path = write_gbxml(bem, tmp_path / "shaded.xml")
    root = ET.parse(path).getroot()
    shades = [s for s in root.iter(f"{{{GBXML_NS}}}Surface") if s.get("surfaceType") == "Shade"]
    assert len(shades) == 2
    for s in shades:
        assert s.find("g:AdjacentSpaceId", NS) is None  # shading is not envelope
        assert len(s.findall(".//g:CartesianPoint", NS)) == 4
    ok, errs = validate_gbxml(path)
    assert ok, errs


def test_no_shading_writes_no_shade_surface(tmp_path):
    path = write_gbxml(_bem_from_model(make_clean_model()), tmp_path / "plain.xml")
    root = ET.parse(path).getroot()
    assert not [s for s in root.iter(f"{{{GBXML_NS}}}Surface") if s.get("surfaceType") == "Shade"]


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda m: setattr(m.shading[0], "host_wall_id", "L1-EW99"), "no host wall geometry"),
        (lambda m: setattr(m.shading[0], "z_m", None), "incomplete"),
    ],
)
def test_unplaceable_shades_are_skipped_with_a_note(mutate, reason):
    m = _shaded()
    mutate(m)
    bem = _bem_from_model(m)
    assert "SH-OVH-1" not in _by_id(bem)
    assert any("SH-OVH-1" in n and reason in n for n in bem.notes)


def test_wall_with_no_space_on_either_side_is_not_guessed():
    m = _shaded()
    shades, notes = shades_from_model(m, lambda p: (p[0], p[1]), [])
    assert shades == []
    assert all("cannot tell the outside" in n for n in notes) and len(notes) == 2
