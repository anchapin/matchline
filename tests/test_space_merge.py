"""space_merge: closets fold in through their door, shafts by shared wall share."""

from building_model import (
    BuildingModel,
    EnvelopeWall,
    Level,
    Space,
    SpaceOpening,
    Zone,
)
from space_merge import (
    METHOD_CLOSET,
    METHOD_SHAFT,
    merge_closets_and_shafts,
)

H = 3.0


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _space(sid, rect, poly_type="room", conf=None):
    x0, y0, x1, y1 = rect
    a = (x1 - x0) * (y1 - y0)
    return Space(
        id=sid,
        level_id="L1",
        polygon_m=_rect(*rect),
        area_m2=a,
        volume_m3=a * H,
        poly_type=poly_type,
        poly_type_confidence=conf,
    )


def _door(did, xy):
    return SpaceOpening(
        id=did, tag="D1", category="door", width_m=0.9, height_m=2.1, plan_center_m=list(xy)
    )


def _model(*spaces):
    m = BuildingModel(name="t", levels=[Level(id="L1", wall_height_m=H)])
    for s in spaces:
        m.spaces[s.id] = s
    return m


def test_closet_merges_through_its_door():
    room = _space("L1-101", (0, 0, 6, 4))
    other = _space("L1-102", (0, 4, 6, 8))
    clo = _space("L1-103", (6, 0, 8, 2), "closet", 0.85)
    clo.openings.append(_door("D7", (6.0, 1.0)))  # on the shared face with 101
    m = _model(room, other, clo)
    m.zones["Z1"] = Zone(id="Z1", level_id="L1", space_ids=["L1-103", "L1-101"])
    m.envelope.append(EnvelopeWall(id="E1", facade="east", space_id="L1-103"))
    res = merge_closets_and_shafts(m)
    assert [r.target_id for r in res.merged] == ["L1-101"]
    assert "L1-103" not in m.spaces
    r = m.spaces["L1-101"]
    assert abs(r.area_m2 - 28.0) < 1e-6
    assert abs(r.volume_m3 - 84.0) < 1e-6
    assert r.merged_from == ["L1-103"]
    assert any(o.id == "D7" for o in r.openings)
    assert m.zones["Z1"].space_ids == ["L1-101"]
    assert m.envelope[0].space_id == "L1-101"
    h = r.history[-1]
    assert h.method == METHOD_CLOSET and h.confidence == 0.85 and "D7" in h.note
    assert m.review_queue == []


def test_closet_without_located_door_is_kept_and_flagged():
    clo = _space("L1-103", (6, 0, 8, 2), "closet")
    clo.openings.append(SpaceOpening(id="D9", tag="D1", category="door", width_m=0.9, height_m=2.1))
    m = _model(_space("L1-101", (0, 0, 6, 4)), clo)
    res = merge_closets_and_shafts(m)
    assert res.merged == [] and "L1-103" in m.spaces
    assert res.kept[0].reason.startswith("no door")
    assert [i.kind for i in m.review_queue] == ["space_merge"]
    assert m.review_queue[0].target["kind"] == "space"  # #796


def test_door_at_corner_of_two_rooms_is_ambiguous():
    clo = _space("L1-103", (6, 0, 8, 2), "closet")
    m = _model(_space("L1-101", (0, 0, 6, 2)), _space("L1-102", (0, 2, 8, 6)), clo)
    res = merge_closets_and_shafts(
        m, doors=[{"id": "D3", "level_id": "L1", "plan_center_m": [6.0, 2.0]}]
    )
    assert res.merged == [] and "more than one room" in res.kept[0].reason


def test_closet_with_doors_to_two_rooms_is_kept():
    clo = _space("L1-103", (4, 0, 6, 2), "closet")
    clo.openings += [_door("Da", (4.0, 1.0)), _door("Db", (6.0, 1.0))]
    m = _model(_space("L1-101", (0, 0, 4, 2)), _space("L1-102", (6, 0, 10, 2)), clo)
    res = merge_closets_and_shafts(m)
    assert res.merged == [] and "more than one room" in res.kept[0].reason
    assert "L1-103" in m.spaces


def test_shaft_goes_to_largest_shared_wall_share():
    # shaft 1x2: shares 2 m of wall with 101 (west), 1 m with 102 (south)
    shaft = _space("L1-S1", (4, 0, 5, 2), "shaft", 0.9)
    m = _model(_space("L1-101", (0, 0, 4, 4)), _space("L1-102", (4, 2, 8, 4)), shaft)
    res = merge_closets_and_shafts(m)
    (rec,) = res.merged
    assert rec.target_id == "L1-101" and rec.rule == METHOD_SHAFT
    assert abs(rec.shared_wall_frac - 2 / 6) < 1e-6
    assert abs(rec.shared_wall_m2 - 2 * H) < 1e-6
    assert abs(m.spaces["L1-101"].area_m2 - 18.0) < 1e-6


def test_shaft_tie_is_kept():
    shaft = _space("L1-S1", (4, 0, 5, 1), "shaft")
    m = _model(_space("L1-101", (0, 0, 4, 1)), _space("L1-102", (5, 0, 9, 1)), shaft)
    res = merge_closets_and_shafts(m)
    assert res.merged == [] and res.kept[0].reason.startswith("tie")


def test_wall_gap_is_closed_and_recorded():
    # interior faces 0.2 m apart (partition thickness)
    clo = _space("L1-103", (6.2, 0, 8.2, 2), "closet")
    clo.openings.append(_door("D7", (6.2, 1.0)))
    m = _model(_space("L1-101", (0, 0, 6, 2)), clo)
    (rec,) = merge_closets_and_shafts(m).merged
    r = m.spaces["L1-101"]
    assert abs(rec.wall_strip_m2 - 0.4) < 1e-6
    assert abs(r.area_m2 - (12 + 4 + 0.4)) < 1e-6
    assert len(r.polygon_m) == 4


def test_shaft_next_to_merged_closet_sees_the_room():
    clo = _space("L1-103", (4, 0, 6, 3), "closet")
    clo.openings.append(_door("D7", (4.0, 1.5)))
    shaft = _space("L1-S1", (6, 0, 7, 3), "shaft")  # only touches the closet
    m = _model(_space("L1-101", (0, 0, 4, 3)), clo, shaft)
    res = merge_closets_and_shafts(m)
    assert [(r.source_id, r.target_id) for r in res.merged] == [
        ("L1-103", "L1-101"),
        ("L1-S1", "L1-101"),
    ]
    assert m.spaces["L1-101"].merged_from == ["L1-103", "L1-S1"]
    assert abs(m.spaces["L1-101"].area_m2 - 21.0) < 1e-6


def test_second_run_changes_nothing():
    clo = _space("L1-103", (6, 0, 8, 2), "closet")
    m = _model(_space("L1-101", (0, 0, 6, 4)), clo, _space("L1-S1", (0, 4, 1, 5), "shaft"))
    merge_closets_and_shafts(m)
    n_review = len(m.review_queue)
    res = merge_closets_and_shafts(m)
    assert res.merged == [] and res.kept == []
    assert len(m.review_queue) == n_review


def test_toplit_area_recomputed_after_merge():
    from building_model import DaylitZone

    clo = _space("L1-103", (6, 0, 8, 2), "closet")
    clo.openings.append(_door("D7", (6.0, 1.0)))
    clo.daylight.toplit.append(
        DaylitZone(
            id="T1",
            zone_class="under_skylight",
            window_id="SK1",
            polygon_m=_rect(6, 0, 8, 2),
            area_m2=4.0,
        )
    )
    clo.daylight.toplit_m2 = 4.0
    room = _space("L1-101", (0, 0, 6, 4))
    room.daylight.toplit.append(
        DaylitZone(
            id="T2",
            zone_class="under_skylight",
            window_id="SK2",
            polygon_m=_rect(5, 0, 7, 2),
            area_m2=2.0,
        )
    )
    room.daylight.toplit_m2 = 2.0
    m = _model(room, clo)
    merge_closets_and_shafts(m)
    # union of [5,7]x[0,2] and [6,8]x[0,2] = 6 m2, not 4+2 double counted at overlap
    assert abs(m.spaces["L1-101"].daylight.toplit_m2 - 6.0) < 1e-6
