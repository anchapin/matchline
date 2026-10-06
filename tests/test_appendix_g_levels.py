"""Appendix G room split on every storey of a multi-storey model (#648)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon

from bem_export import validate_gbxml, write_gbxml
from building_model import SpaceOpening
from ifc_export import _bem_from_model
from tests.test_atria import _atrium_model
from tests.test_multistorey_export import NS, _adj, _model, _zs
from thermal_zoning import appendix_g_split, appendix_g_split_levels


def _two_storey(window=False):
    m = _model(
        [("L1", 0.0, None), ("L2", 3.0, None)],
        [("L1-101", "L1", (0, 0, 30, 20)), ("L2-201", "L2", (0, 0, 30, 20))],
    )
    if window:
        m.spaces["L2-201"].openings.append(
            SpaceOpening(
                id="w1", tag="A", category="window", width_m=1.2, height_m=1.5, host_facade="south"
            )
        )
    return m


def _write(bem, tmp_path, name="m.xml"):
    p = write_gbxml(bem, tmp_path / name)
    ok, errs = validate_gbxml(p)
    assert ok, errs[:5]
    return ET.parse(p).getroot()


def _surfs(root, stype):
    return [s for s in root.iter(f"{{{NS['g']}}}Surface") if s.get("surfaceType") == stype]


def test_single_storey_goes_to_the_original_split():
    m = _model([("L1", 0.0, None)], [("L1-101", "L1", (0, 0, 30, 20))])
    bem = _bem_from_model(m)
    a, ra = appendix_g_split(bem)
    b, rb = appendix_g_split_levels(bem)
    assert [s.sid for s in a.spaces] == [s.sid for s in b.spaces]
    assert ra.zones == rb.zones


def test_each_storey_split_with_level_prefixed_blocks():
    bem = _bem_from_model(_two_storey())
    new, res = appendix_g_split_levels(bem)
    assert len(res.zones) == 10
    assert {z for z, _ in res.zones if z.startswith("L1-")} and all(
        z.startswith(("L1-", "L2-")) for z, _ in res.zones
    )
    for lv in ("L1", "L2"):
        pieces = [s for s in new.spaces if s.level_id == lv]
        assert len(pieces) == 5 and all(s.split_from for s in pieces)
        assert sum(s.area_m2 for s in pieces) == pytest.approx(600.0)
    assert all(aw.space_ids[0][:2] == aw.space_ids[1][:2] for aw in res.air_walls)
    assert any("Appendix G zoning on 2 storeys" in n for n in new.notes)
    assert bem.spaces[0].sid == "L1-101"  # input untouched


def test_floors_and_roofs_recut_per_piece(tmp_path):
    bem = _bem_from_model(_two_storey())
    new, _ = appendix_g_split_levels(bem)
    sids = {s.sid for s in new.spaces}
    for hz in new.horizontals:
        assert hz.lower_space_id in sids | {None} and hz.upper_space_id in sids | {None}
    by_type = {}
    for hz in new.horizontals:
        by_type[hz.surface_type] = by_type.get(hz.surface_type, 0.0) + sum(
            Polygon(lp).area for lp in hz.loops
        )
    assert by_type["InteriorFloor"] == pytest.approx(600.0)
    assert by_type["Roof"] == pytest.approx(600.0)
    assert by_type["SlabOnGrade"] == pytest.approx(600.0)
    # pieces stack on the same plate: each interior floor joins matching blocks
    for hz in new.horizontals:
        if hz.surface_type == "InteriorFloor":
            assert hz.lower_space_id.split("-", 3)[3] == hz.upper_space_id.split("-", 3)[3]
    root = _write(new, tmp_path)
    assert len(_surfs(root, "InteriorFloor")) == 5
    air = _surfs(root, "Air")
    assert air and all((min(_zs(s)), max(_zs(s))) in ((0.0, 3.0), (3.0, 6.0)) for s in air)


def test_opening_moves_to_the_piece_on_its_facade_and_level(tmp_path):
    bem = _bem_from_model(_two_storey(window=True))
    new, _ = appendix_g_split_levels(bem)
    (u,) = [u for u in new.openings if u.category == "window"]
    assert u.space_sid.startswith("L2-201-L2-perimeter-south")
    root = _write(new, tmp_path)
    (op,) = list(root.iter(f"{{{NS['g']}}}Opening"))
    host = next(s for s in root.iter(f"{{{NS['g']}}}Surface") if op in list(s))
    assert _adj(host) == [u.space_sid]


def test_atrium_pieces_keep_full_height_and_atrium_air_walls_recut(tmp_path):
    m = _atrium_model()
    for sid, box in (("L1-ATR", (0, 0, 8, 10)),):
        m.spaces[sid].polygon_m = [[0, 0], [8, 0], [8, 10], [0, 10]]
    bem = _bem_from_model(m)
    new, res = appendix_g_split_levels(bem, depth_m=2.0)
    atr = [s for s in new.spaces if s.split_from == "L1-ATR"]
    assert len(atr) > 1 and all(s.height_m == pytest.approx(6.0) for s in atr)
    assert all(s.spans == ["L2"] for s in atr)
    sids = {s.sid for s in new.spaces}
    assert all(set(aw.space_ids) <= sids for aw in new.air_walls)
    root = _write(new, tmp_path)
    between = [s for s in _surfs(root, "Air") if all(a.startswith("L1-ATR-") for a in _adj(s))]
    assert between and all(max(_zs(s)) == pytest.approx(6.0) for s in between)
    # the old atrium/upper-room air wall now names atrium pieces, same total length
    old = [aw for aw in bem.air_walls if "L1-ATR" in aw.space_ids]
    new_aw = [
        aw
        for aw in new.air_walls
        if any(x.startswith("L1-ATR-") for x in aw.space_ids)
        and any(x.startswith("L2-") for x in aw.space_ids)
    ]
    assert sum(a.length_m for a in new_aw) == pytest.approx(sum(a.length_m for a in old))
