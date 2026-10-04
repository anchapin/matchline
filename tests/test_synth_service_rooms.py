"""Synthetic closets/shafts/doors drive space_merge end to end."""

import pytest

from link._api import build_model
from synth.multidiscipline import generate_building

SEEDS = [0, 3, 7, 11]


def _first_with_service(seed, oo=False):
    b = generate_building(seed, open_office_span=oo, service_rooms=True)
    if not b["doors"]:
        pytest.skip("no room large enough to host a closet for this seed")
    return b


@pytest.mark.parametrize("seed", SEEDS)
def test_closet_and_shaft_merge_into_their_host(seed):
    b = _first_with_service(seed)
    services = {r["service"]: r for r in b["rooms"] if r.get("service")}
    assert set(services) == {"closet", "shaft"}
    host_no = b["doors"][0]["room_number"]
    host_gt = next(r for r in b["rooms"] if r["number"] == host_no)
    m, _ = build_model(b)
    host = m.spaces[f"L1-{host_no}"]
    assert len(host.merged_from) == 2
    assert not [s for s in m.spaces.values() if s.poly_type in ("closet", "shaft")]
    assert not [i for i in m.review_queue if i.kind == "space_merge"]
    expected = host_gt["area_m2"] + services["closet"]["area_m2"] + services["shaft"]["area_m2"]
    assert abs(host.area_m2 - expected) < 1e-2
    methods = [h.method for h in host.history]
    assert "space_merge:closet_door" in methods and "space_merge:shaft_shared_wall" in methods


def test_default_building_is_unchanged_by_the_option():
    plain = generate_building(5)
    assert plain["doors"] == [] and not any(r.get("service") for r in plain["rooms"])
    with_svc = generate_building(5, service_rooms=True)
    host = with_svc["doors"][0]["room_number"] if with_svc["doors"] else None
    a = {r["number"]: r["rect_m"] for r in plain["rooms"] if r["number"] != host}
    b = {r["number"]: r["rect_m"] for r in with_svc["rooms"] if r["number"] and r["number"] != host}
    assert a == b


def test_service_rooms_get_no_fixtures_diffusers_or_windows():
    b = _first_with_service(3)
    assert all(f["room_number"] for f in b["fixtures"])
    assert all(c["room_number"] != "" for c in b["components"])
    assert all(w["room_number"] for w in b["south_windows"])


def test_unnumbered_spaces_keep_their_own_classification():
    b = _first_with_service(7)
    m, _ = build_model(b)
    # both unnumbered polygons were classified separately (closet and shaft)
    # and merged; nothing was left as a lone closet or shaft
    host = m.spaces[f"L1-{b['doors'][0]['room_number']}"]
    notes = [h.note for h in host.history if h.method.startswith("space_merge")]
    assert any("closet" in n for n in notes) and any("shaft" in n for n in notes)
