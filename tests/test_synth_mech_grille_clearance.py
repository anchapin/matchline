"""#725: return grilles stand clear of ducts and other symbols.

Real plans draw a return grille at the end of its own branch duct (BSI
Medical-Dental Clinic: 179 of 184 return terminals) with no other duct
running through it (TOSV mechanical plan, Z-Group Architects 2006). Before
#725, 150 of 239 synthetic grilles on seeds 1-40 sat on a duct bar.
"""

import numpy as np

import synth.mech as M


def _box(c):
    return M._comp_box(c)


def _grille_is_clear(net, comps, g):
    gb = _box(g)
    for pc in net.pieces:
        if pc["duct"] == f"R-DROP-{g['id']}":
            continue
        if M._overlap(gb, M._piece_box(pc)):
            return False
    return not any(o is not g and M._overlap(gb, _box(o)) for o in comps)


def test_grilles_clear_of_ducts_and_symbols():
    total = clear = 0
    for seed in range(1, 41):
        net, gt, _ = M._layout_mech(np.random.default_rng(seed))
        comps = gt["components"]
        for g in (c for c in comps if c["type"] == "grille"):
            total += 1
            clear += _grille_is_clear(net, comps, g)
    assert total > 200
    assert clear / total >= 0.95, f"{clear}/{total} grilles clear"


def test_grille_placement_is_deterministic():
    a = M._layout_mech(np.random.default_rng(7))[1]["components"]
    b = M._layout_mech(np.random.default_rng(7))[1]["components"]
    ga = [(c["x_m"], c["y_m"]) for c in a if c["type"] == "grille"]
    gb = [(c["x_m"], c["y_m"]) for c in b if c["type"] == "grille"]
    assert ga == gb and ga
