"""Parity of IFC import against Pascal-style cleanup defects (#578).

A wall exported as fragments, fragments of different height, and a window
doubled on itself, each compared with the whole-wall fixture. Fragments
already conserve wall area and opening counts, so no fragment merge is
added; a doubled window did not, so duplicates are dropped per host wall.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

from building_model import BimOpening  # noqa: E402
from ifc_import import _drop_duplicate_openings, import_ifc  # noqa: E402
from tests.ifc_defects import double_a_window, fixture, fragment_south_wall  # noqa: E402


def _area(m):
    return sum(w.area_m2 for w in m.envelope)


def _openings(m):
    return sorted(
        (o.category, o.tag, o.width_m, o.height_m) for e in m.bim_elements for o in e.openings
    )


def _attached(m):
    return sum(len(sp.openings) for sp in m.spaces.values())


@pytest.fixture()
def whole(tmp_path):
    return import_ifc(fixture(tmp_path / "whole.ifc"))


def test_three_fragments_conserve_area_and_openings(tmp_path, whole):
    m = import_ifc(fragment_south_wall(fixture(tmp_path / "f.ifc"), [0, 7, 14, 20]))
    assert _area(m) == pytest.approx(_area(whole), abs=1e-6)
    south = [w for w in m.envelope if w.facade == "south"]
    assert sum(w.length_m for w in south) == pytest.approx(19.8)
    assert _openings(m) == _openings(whole)
    assert _attached(m) == _attached(whole)
    s = m.opening_attachment_summary
    assert (s.no_envelope_edge, s.ambiguous_tie, s.no_ref_direction) == (0, 0, 0)


def test_fragments_of_different_height_stay_apart(tmp_path, whole):
    m = import_ifc(fragment_south_wall(fixture(tmp_path / "h.ifc"), [0, 7, 14, 20], [3, 2, 3]))
    south = sorted(
        (w for w in m.envelope if w.facade == "south"), key=lambda w: min(w.from_m[0], w.to_m[0])
    )
    assert [w.height_m for w in south] == [3.0, 2.0, 3.0]
    # the 2 m fragment is 7 m long: 7 m2 less wall than the whole version
    assert _area(m) == pytest.approx(_area(whole) - 7.0, abs=1e-6)
    assert _openings(m) == _openings(whole)


def test_doubled_window_counts_once(tmp_path, whole):
    m = import_ifc(double_a_window(fixture(tmp_path / "d.ifc"), shift=0.02))
    assert _openings(m) == _openings(whole)
    assert _attached(m) == _attached(whole)
    assert "duplicate openings: 1 doubled copies dropped" in m.revision_log[-1].note


def test_two_windows_half_a_metre_apart_are_both_kept(tmp_path, whole):
    m = import_ifc(double_a_window(fixture(tmp_path / "a.ifc"), shift=0.5))
    assert len(_openings(m)) == len(_openings(whole)) + 1
    assert "duplicate openings" not in m.revision_log[-1].note


def test_clean_fixture_drops_nothing(whole):
    assert "duplicate openings" not in whole.revision_log[-1].note


def _op(gid, s=1.0, tag="A", cat="window", w=1.5, sill=0.9):
    return BimOpening(
        id=gid, category=cat, tag=tag, width_m=w, height_m=1.2, sill_m=sill, s_center_m=s
    )


def test_duplicate_rule_keeps_first_gid_and_file_order():
    ops = [_op("z", 1.0), _op("b", 4.0), _op("a", 1.04)]
    kept, dropped = _drop_duplicate_openings(ops)
    assert [o.id for o in kept] == ["b", "a"]
    assert [o.id for o in dropped] == ["z"]
    assert _drop_duplicate_openings(list(reversed(ops)))[1][0].id == "z"


def test_duplicate_rule_needs_matching_kind_and_known_values():
    assert not _drop_duplicate_openings([_op("a"), _op("b", tag="B")])[1]
    assert not _drop_duplicate_openings([_op("a"), _op("b", cat="door")])[1]
    assert not _drop_duplicate_openings([_op("a"), _op("b", w=1.6)])[1]
    assert not _drop_duplicate_openings([_op("a", sill=None), _op("b", sill=None)])[1]
