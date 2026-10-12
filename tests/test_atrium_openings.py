"""Atrium wall openings on the storey band their sill sits in (#650)."""

from __future__ import annotations

from bem_multistorey import level_walls, space_levels
from building_model import BimElement, BimOpening, SpaceOpening
from ifc_export import _bem_from_model
from tests.test_atria import _atrium_model
from tests.test_multistorey_export import NS, _write


def _with_window(m, sill_m=1.0, host_z=3.0, oid="o1", sid="L1-ATR"):
    m.spaces[sid].openings.append(
        SpaceOpening(
            id=oid,
            tag="CL",
            category="window",
            width_m=1.5,
            height_m=1.0,
            sill_m=sill_m,
            host_facade="south",
        )
    )
    if host_z is not None:
        m.bim_elements.append(
            BimElement(
                global_id=f"W-{oid}",
                ifc_class="IfcWall",
                placement_m=[0.0, 10.0, host_z],
                openings=[BimOpening(id=oid, category="window", sill_m=sill_m)],
            )
        )
    return m


def _placed(bem):
    levels = sorted(bem.levels, key=lambda lv: lv.elevation_m)
    notes = []
    out = {}
    for lv, _edges, assign, _owner in level_walls(bem, levels, space_levels(bem, levels), notes):
        for units in assign.values() if isinstance(assign, dict) else assign:
            for u in units:
                out[u.tag] = lv.id
    return out, notes


def test_sill_height_from_host_wall_base_plus_stated_sill():
    bem = _bem_from_model(_with_window(_atrium_model()))
    (u,) = [u for u in bem.openings if u.tag == "CL"]
    assert u.sill_z_m == 4.0


def test_clerestory_goes_on_the_storey_its_sill_sits_in():
    placed, notes = _placed(_bem_from_model(_with_window(_atrium_model())))
    assert placed == {"CL": "L2"}
    assert any("placed on the storey their sill sits in" in n for n in notes)


def test_low_window_stays_on_the_base_storey():
    placed, notes = _placed(_bem_from_model(_with_window(_atrium_model(), host_z=0.0)))
    assert placed == {"CL": "L1"}
    assert not any("atrium opening" in n for n in notes)


def test_sill_on_the_storey_line_belongs_to_the_storey_above():
    placed, _ = _placed(_bem_from_model(_with_window(_atrium_model(), sill_m=0.0)))
    assert placed == {"CL": "L2"}


def test_no_stated_sill_keeps_base_storey_and_notes_it():
    placed, notes = _placed(_bem_from_model(_with_window(_atrium_model(), sill_m=None)))
    assert placed == {"CL": "L1"}
    assert any("no stated sill height kept on the base storey" in n for n in notes)
    placed, notes = _placed(_bem_from_model(_with_window(_atrium_model(), host_z=None)))
    assert placed == {"CL": "L1"}
    assert any("no stated sill height" in n for n in notes)


def test_sill_above_the_atrium_keeps_base_storey_and_notes_it():
    placed, notes = _placed(_bem_from_model(_with_window(_atrium_model(), host_z=6.0)))
    assert placed == {"CL": "L1"}
    assert any("sill outside the atrium's height" in n for n in notes)


def test_ordinary_room_ignores_the_sill_height():
    m = _with_window(_atrium_model(), host_z=6.0, sid="L1-101")
    placed, notes = _placed(_bem_from_model(m))
    assert placed == {"CL": "L1"}
    assert not any("atrium opening" in n for n in notes)


def test_gbxml_clerestory_hosted_on_an_upper_wall(tmp_path):
    _p, root = _write(_with_window(_atrium_model()), tmp_path)
    (op,) = list(root.iter(f"{{{NS['g']}}}Opening"))
    host = next(s for s in root.iter(f"{{{NS['g']}}}Surface") if op in list(s))
    assert host.get("id").startswith("wall-L2-")
