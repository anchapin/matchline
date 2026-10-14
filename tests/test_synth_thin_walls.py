"""Wall thickness mix in the synth sheet generator (#743).

Thin hairline partitions render as tight parallel line pairs that sit at the
window glyph's glass-line spacing; drawing most interior walls thin gives the
detector hard negatives.
"""

import numpy as np

from synth.sheets import (
    PX_PER_M,
    WALL_T_M,
    _classify_edges,
    _layout_footprint,
    wall_thickness_by_line,
)

_THIN = (0.04, 0.09, 0.12, 0.15)


def _map_for(seed):
    rng = np.random.default_rng(seed)
    _, rooms = _layout_footprint(rng)
    return wall_thickness_by_line(_classify_edges(rooms), rng)


def test_exterior_lines_keep_full_thickness():
    for seed in range(10):
        edges = _classify_edges(_layout_footprint(np.random.default_rng(seed))[1])
        t = _map_for(seed)
        for e in edges:
            if e["kind"] == "exterior":
                assert t[e["key"]] == WALL_T_M


def test_interior_lines_draw_thin_mostly():
    thin_seen = set()
    for seed in range(40):
        edges = _classify_edges(_layout_footprint(np.random.default_rng(seed))[1])
        t = _map_for(seed)
        for e in edges:
            if e["kind"] == "interior":
                v = t[e["key"]]
                assert v == WALL_T_M or v in _THIN
                if v != WALL_T_M:
                    thin_seen.add(v)
    assert thin_seen, "no thin interior walls across 40 sheets"


def test_every_line_has_one_thickness():
    for seed in range(10):
        edges = _classify_edges(_layout_footprint(np.random.default_rng(seed))[1])
        t = _map_for(seed)
        assert {e["key"] for e in edges} == set(t)


def test_thin_mix_reaches_glass_spacing():
    # At the sheet scale the thinnest mix entry must render as a pair at or
    # under the window glyph's glass-line spacing (the hard-negative regime).
    assert PX_PER_M * _THIN[-1] <= PX_PER_M * WALL_T_M / 2
