"""VAV-vs-terminal suppression in trace_sheet (#721)."""

from hvac_trace import suppress_vav_near_terminals


def _d(label, cx, cy):
    return {"label": label, "cx": cx, "cy": cy}


def test_vav_next_to_diffuser_is_suppressed():
    kept, n = suppress_vav_near_terminals([_d("vav", 100, 100), _d("diffuser", 140, 100)])
    assert n == 1 and [d["label"] for d in kept] == ["diffuser"]


def test_vav_next_to_sensor_is_kept():
    # seed 11: thermostat 42 px from its VAV box used to delete the VAV
    kept, n = suppress_vav_near_terminals([_d("vav", 338, 575), _d("sensor", 338, 617)])
    assert n == 0 and len(kept) == 2


def test_terminal_inside_vav_box_or_far_away_does_not_suppress():
    dets = [
        _d("vav", 100, 100),
        _d("grille", 110, 100),
        _d("vav", 500, 500),
        _d("diffuser", 600, 500),
    ]
    kept, n = suppress_vav_near_terminals(dets)
    assert n == 0 and len(kept) == 4
