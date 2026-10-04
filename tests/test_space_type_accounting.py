"""space_type_accounting check and polygon_classify provenance (roadmap item 4)."""

from __future__ import annotations

from tests.model_factory import make_clean_model
from validate import run_checks
from validate.conservation import VALID_POLY_TYPES, _check_space_type_accounting


class _Ctx:
    def __init__(self, model):
        self.model = model


def _res(m):
    return _check_space_type_accounting(_Ctx(m))


def test_all_rooms_pass():
    r = _res(make_clean_model())
    assert r.severity == "pass" and "are rooms" in r.message


def test_non_room_area_share_is_reported():
    m = make_clean_model()
    sp = next(iter(m.spaces.values()))
    sp.poly_type = "closet"
    total = sum(abs(s.area_m2 or 0) for s in m.spaces.values())
    r = _res(m)
    assert r.severity == "pass"
    assert f"closet {sp.area_m2:.1f} m2 ({100 * sp.area_m2 / total:.1f}%)" in r.message
    assert r.entities == [sp.id]


def test_types_listed_in_known_order():
    m = make_clean_model()
    a, b = list(m.spaces.values())[:2]
    a.poly_type, b.poly_type = "shaft", "closet"
    msg = _res(m).message
    assert msg.index("shaft") < msg.index("closet")


def test_unknown_type_is_error():
    m = make_clean_model()
    next(iter(m.spaces.values())).poly_type = "garage"
    r = _res(m)
    assert r.severity == "error" and "'garage'" in r.message


def test_unassigned_is_warn_with_breakdown():
    m = make_clean_model()
    sp = next(iter(m.spaces.values()))
    sp.poly_type = "unassigned"
    r = _res(m)
    assert r.severity == "warn" and sp.id in r.entities and "unassigned" in r.message


def test_no_spaces_skip():
    m = make_clean_model()
    m.spaces.clear()
    assert _res(m).severity == "skip"


def test_check_is_in_battery():
    rep = run_checks(make_clean_model())
    ids = (
        [c.id for c in rep.checks] if hasattr(rep, "checks") else [c.check_id for c in rep.results]
    )
    assert "space_type_accounting" in ids


def test_known_types_match_space_comment():
    assert set(VALID_POLY_TYPES) == {"room", "shaft", "closet", "elevator_core", "unassigned"}


def test_build_spaces_records_classifier_provenance():
    from building_model import BuildingModel
    from link._spaces import _build_spaces

    bldg = {
        "sheets": {"arch": {"meta": {"sheet_id": "arch_A101", "revision": 1}}},
        "rooms": [
            {
                "number": "101",
                "name": "OFFICE",
                "rect_m": [0, 0, 5, 6],
                "area_m2": 30.0,
                "polygon_m": [[0, 0], [5, 0], [5, 6], [0, 6]],
            },
            {
                "number": "",
                "name": "",
                "rect_m": [5, 0, 6, 1],
                "area_m2": 1.0,
                "polygon_m": [[5, 0], [6, 0], [6, 1], [5, 1]],
            },
        ],
    }
    m = BuildingModel(name="t")
    sp = {s.number or "chase": s for s in _build_spaces(bldg, m, "L1", 3.0)}
    assert sp["101"].poly_type == "room" and sp["101"].poly_type_confidence == 0.95
    chase = sp["chase"]
    assert chase.poly_type == "shaft" and chase.poly_type_confidence == 0.95
    (h,) = chase.history
    assert h.method == "polygon_classify" and h.note.startswith("shaft:")
