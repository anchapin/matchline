"""HVAC zoning tracer: mechanical plan -> zone graph.

Pipeline (the netlist-extraction analogy from the Jesse-Vision paper,
Sec. 9: schematics are component detection + trace following; ductwork
is the same problem class at building scale):

1. COMPONENT DETECTION -- normalized cross-correlation (NCC) template
   matching proposes symbol locations (localization is geometric), then
   the WiSARD classifier (jesse.py) re-classifies each proposal crop
   (classification is the WiSARD's job). Documented choice: pure NCC
   conflates lookalikes (grille vs diffuser); pure WiSARD has no
   localizer. The cascade uses each for what it is good at.
2. DUCT GRAPH -- ink mask -> morphological opening (keeps filled duct
   bars, drops walls/text/symbol outlines) -> Zhang-Suen skeleton
   (jesse.py) -> pixel connectivity graph.
3. ZONE EXTRACTION -- a VAV terminal unit is a cut vertex: remove its
   skeleton pixels, and the graph splits into inlet side (trunk, no
   diffusers) and outlet side (branch ducts + diffusers). The outlet
   side is identified as the VAV-adjacent component(s) containing
   supply diffusers -- no flow-direction arrows needed.
4. ASSIGNMENT -- diffusers -> rooms (point-in-polygon); rooms -> zone;
   sensors -> zone by room co-location. Duct length = outlet-component
   skeleton pixels -> meters. Every assignment cites its evidence.

Inputs: sheet grayscale array + room polygons (GT here; production
would use the room_labels module). Output: zone graph with audit trail.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_opening
from scipy.ndimage import label as clabel
from scipy.signal import fftconvolve

from building_model import Provenance
from jesse import WisardClassifier, zhang_suen
from synth.mech import (
    CONTEXT_BG,
    CONTEXT_PER_CLASS,
    MECH_CLASSES,
    PX_PER_M,
    render_template,
    training_crops_from_sheets,
)

NCC_THRESH = {"vav": 0.50, "ahu": 0.50, "diffuser": 0.50, "grille": 0.50, "sensor": 0.40}
# Measured per-class decision rules (NCC true/false score distributions on
# seeds 11/22/33/44; WiSARD train acc: vav .99 ahu 1.0 diffuser .805
# grille .71 sensor .435 bg .978):
# - NCC_PROPOSE: low recall-oriented proposal floor per class.
# - NCC_ACCEPT: classes where NCC alone separates (ahu true .63-.66 vs
#   false <=.58; sensor true .91-.95 vs false <=.48) skip the unreliable
#   WiSARD stage. AHU additionally requires WiSARD non-background.
NCC_PROPOSE = {"vav": 0.55, "ahu": 0.55, "diffuser": 0.45, "grille": 0.45, "sensor": 0.55}
NCC_ACCEPT = {"ahu": 0.55, "sensor": 0.60}
# A proposal that came from the grille template at or above this NCC keeps the
# grille label when WiSARD calls it a diffuser (#730): the two glyphs differ
# only in the diagonals, and a near-exact grille template match is stronger
# evidence than the context crop, which mislabels plenum-return grilles that
# sit off the end of a duct.
GRILLE_KEEP_NCC = 0.80
# Second grille pass with a stub-less, margin-free template (#730). Grilles
# drawn over a filled duct band score low against the stubbed template because
# the duct ink fills its white margin; the tight template matches the glyph
# box alone. A tight match at or above this NCC with no detection within
# ASSOC_PX is added as a grille, and a diffuser detection sitting on it
# (within GRILLE_TIGHT_SNAP_PX) is relabeled grille. None disables the pass.
GRILLE_TIGHT_NCC = 0.95
GRILLE_TIGHT_SNAP_PX = 3
WISARD_ONLY = {"vav", "diffuser", "grille"}
ASSOC_PX = 30  # diffuser/skeleton association radius (px)
VAV_DILATE = 12  # px around VAV bbox whose skeleton is removed
OPEN_PX = 13  # duct opening kernel (px) = 0.26 m
FLEX_OPEN_PX = 9  # disk opening (px) that also keeps diagonal flex runs (#721)


# ---------------------------------------------------------------------------
# 1. NCC template localization
# ---------------------------------------------------------------------------


def ncc_locate(gray: np.ndarray, tmpl: np.ndarray, thresh: float = NCC_THRESH):
    """Normalized cross-correlation -> [(y, x, score)] top-left, NMS'd."""
    t = tmpl - tmpl.mean()
    tss = (t**2).sum()
    xc = fftconvolve(gray, t[::-1, ::-1], mode="valid")
    H, W = gray.shape
    th, tw = tmpl.shape
    ii = np.zeros((H + 1, W + 1))
    ii[1:, 1:] = gray
    ii = ii.cumsum(0).cumsum(1)
    ii2 = np.zeros((H + 1, W + 1))
    ii2[1:, 1:] = gray**2
    ii2 = ii2.cumsum(0).cumsum(1)
    ys, xs = np.mgrid[0 : H - th + 1, 0 : W - tw + 1]
    s1 = ii[ys + th, xs + tw] - ii[ys, xs + tw] - ii[ys + th, xs] + ii[ys, xs]
    s2 = ii2[ys + th, xs + tw] - ii2[ys, xs + tw] - ii2[ys + th, xs] + ii2[ys, xs]
    n = th * tw
    denom = np.sqrt(np.maximum(s2 - s1**2 / n, 1e-9) * tss)
    ncc = xc / denom
    cand = np.argwhere(ncc >= thresh)
    if len(cand) == 0:
        return []
    order = np.argsort(-ncc[cand[:, 0], cand[:, 1]])
    md = max(th, tw)
    kept = []
    for idx in order:
        y, x = int(cand[idx][0]), int(cand[idx][1])
        if all((y - ky) ** 2 + (x - kx) ** 2 >= md * md for ky, kx, _ in kept):
            kept.append((y, x, float(ncc[y, x])))
    return kept


def _logodds_scores(clf, images, alpha=1.0):
    """Per-class log-odds scores, (N, C). Same math as
    WisardClassifier.predict_logodds, but returns the full score matrix
    so we can measure top-1 margins."""
    addrs = clf._gather_addrs(images)
    n = len(images)
    k = clf.tuple_idx.shape[0]
    c = clf.n_classes
    kk = np.arange(k)
    log_ram = np.zeros((c, n))
    sum_ram = np.zeros((n, k))
    for cc in range(c):
        g = clf.ram[cc][kk[:, None], addrs.T].T
        sum_ram += g
        log_ram[cc] = np.log(g + alpha).sum(axis=1)
    bg = np.log(sum_ram / c + alpha).sum(axis=1)
    return (log_ram - bg[None, :]).T


def detect_components(gray: np.ndarray, templates: dict, clf: WisardClassifier):
    """Cascade: NCC proposals (cross-class NMS) -> per-class decision.
    vav/diffuser/grille go through WiSARD (background = reject); ahu and
    sensor are NCC-only because their template scores separate cleanly
    and WiSARD is unreliable (sensor) or unneeded (ahu).
    Returns list of dicts {label, cx, cy, w, h, ncc_cls, ncc, margin}."""
    proposals = []
    for cls, tmpl in templates.items():
        th, tw = tmpl.shape
        for y, x, s in ncc_locate(gray, tmpl, thresh=NCC_PROPOSE[cls]):
            proposals.append(
                {"cx": x + tw / 2, "cy": y + th / 2, "w": tw, "h": th, "ncc_cls": cls, "ncc": s}
            )
    proposals.sort(key=lambda p: -p["ncc"])
    merged = []
    for p in proposals:
        if all(math.hypot(p["cx"] - q["cx"], p["cy"] - q["cy"]) > ASSOC_PX for q in merged):
            merged.append(p)
    from synth.mech import detection_crop

    X = np.stack([detection_crop(gray, p["cx"], p["cy"], p["ncc_cls"]) for p in merged])
    scores = _logodds_scores(clf, X)
    top2 = np.sort(scores, axis=1)[:, -2:]
    margins = top2[:, 1] - top2[:, 0]
    wi = scores.argmax(axis=1)
    out = []
    n_bg = len(MECH_CLASSES)  # background index
    for p, wi_i, m in zip(merged, wi, margins):
        cls, s = p["ncc_cls"], p["ncc"]
        is_bg = int(wi_i) == n_bg
        if cls in WISARD_ONLY:
            if is_bg:
                continue
            label = MECH_CLASSES[int(wi_i)]
            if (
                cls == "grille"
                and label == "diffuser"
                and GRILLE_KEEP_NCC is not None
                and s >= GRILLE_KEEP_NCC
            ):
                label = "grille"
        elif cls == "ahu":
            if s < NCC_ACCEPT["ahu"] or is_bg:
                continue
            label = "ahu"
        else:  # sensor: NCC only
            if s < NCC_ACCEPT["sensor"]:
                continue
            label = "sensor"
        out.append(
            {
                "label": label,
                "cx": p["cx"],
                "cy": p["cy"],
                "w": p["w"],
                "h": p["h"],
                "ncc_cls": cls,
                "ncc": round(s, 3),
                "margin": round(float(m), 2),
            }
        )
    if GRILLE_TIGHT_NCC is not None:
        from synth.mech import render_template

        tt = render_template("grille", margin_px=0, stubs=False)
        th, tw = tt.shape
        for y, x, s in ncc_locate(gray, tt, thresh=GRILLE_TIGHT_NCC):
            cx, cy = x + tw / 2, y + th / 2
            near = [d for d in out if math.hypot(d["cx"] - cx, d["cy"] - cy) <= ASSOC_PX]
            if not near:
                out.append(
                    {
                        "label": "grille",
                        "cx": cx,
                        "cy": cy,
                        "w": tw,
                        "h": th,
                        "ncc_cls": "grille",
                        "ncc": round(s, 3),
                        "margin": 0.0,
                    }
                )
                continue
            for d in near:
                snap = math.hypot(d["cx"] - cx, d["cy"] - cy) <= GRILLE_TIGHT_SNAP_PX
                if d["label"] == "diffuser" and snap:
                    d["label"] = "grille"
    # One AHU per sheet in the generator (and typically one per real plan):
    # keep the top-NCC AHU proposal so a lowered accept threshold can't
    # spawn false AHUs on VAV boxes.
    ahus = [d for d in out if d["label"] == "ahu"]
    if len(ahus) > 1:
        best = max(ahus, key=lambda d: d["ncc"])
        out = [d for d in out if d["label"] != "ahu"] + [best]
    return out


# ---------------------------------------------------------------------------
# 2. Duct skeleton graph
# ---------------------------------------------------------------------------


def duct_skeleton(gray: np.ndarray) -> np.ndarray:
    """Filled duct bars -> 1-px centerline skeleton (0/1 uint8).

    The square opening keeps axis-aligned bars at least OPEN_PX wide but
    erases a diagonal bar narrower than about OPEN_PX * sqrt(2), so a flex
    run into a diffuser vanished and the diffuser fell out of its zone
    (#721). A disk is the same width at every angle; its opening is OR-ed
    in, so everything the square kept is still kept.
    """
    ink = gray < 128
    duct = binary_opening(ink, structure=np.ones((OPEN_PX, OPEN_PX)))
    duct |= binary_opening(ink, structure=_disk(FLEX_OPEN_PX))
    return zhang_suen(duct).astype(np.uint8)


def _disk(d: int) -> np.ndarray:
    r = (d - 1) / 2
    yy, xx = np.mgrid[:d, :d]
    return (xx - r) ** 2 + (yy - r) ** 2 <= r * r + 0.5


# ---------------------------------------------------------------------------
# 3-4. Zone extraction + assignment
# ---------------------------------------------------------------------------


def _room_poly(room):
    """Room outline in metres: ``polygon_m`` when given, else the ``rect_m`` box (#697)."""
    poly = room.get("polygon_m")
    if poly and len(poly) >= 3:
        return [(float(x), float(y)) for x, y in poly]
    x0, y0, x1, y1 = room["rect_m"]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _poly_depth(x_m, y_m, poly):
    """Signed distance to a polygon boundary: + inside (or on it), - outside."""
    n = len(poly)
    dist = min(_seg_dist(x_m, y_m, *poly[i], *poly[(i + 1) % n]) for i in range(n))
    if dist == 0.0:
        return 0.0
    inside = False
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        if (ay > y_m) != (by > y_m):
            if x_m < ax + (y_m - ay) * (bx - ax) / (by - ay):
                inside = not inside
    return dist if inside else -dist


def _room_of(x_m, y_m, rooms):
    for r in rooms:
        if _poly_depth(x_m, y_m, _room_poly(r)) >= 0:
            return r["id"]
    return None


# Diffuser -> room assignment (#684). Point-in-polygon alone puts a diffuser
# on or near a wall line in whichever room happens to be listed first, and
# drops one just outside a simplified room polygon. A diffuser deeper than
# ROOM_EDGE_MARGIN_M inside exactly one room is assigned outright; the rest
# go through _assign_diffuser_room.
ROOM_EDGE_MARGIN_M = 0.15  # closer than this to a room edge = on the boundary
NEAREST_ROOM_MARGIN_M = 0.5  # outside every room: nearest room within this
ROOM_TIE_M = 0.10  # depth gap below which two candidate rooms are a tie


def _room_depth(x_m, y_m, room):
    """Signed distance to a room outline: + inside (to nearest edge), - outside.

    ``room`` is a room dict (its ``polygon_m``, else ``rect_m``, #697) or a
    bare ``(x0, y0, x1, y1)`` rect.
    """
    if isinstance(room, dict):
        return _poly_depth(x_m, y_m, _room_poly(room))
    return _poly_depth(x_m, y_m, _room_poly({"rect_m": room}))


def _assign_diffuser_room(x_m, y_m, rooms, zone_rooms=()):
    """Room for one diffuser: ``(room_id or None, method, candidate ids)``.

    1. ``interior``: deeper than ROOM_EDGE_MARGIN_M inside exactly one room.
    2. Otherwise the candidates are rooms the point is inside or within
       NEAREST_ROOM_MARGIN_M of. None -> ``outside`` (review). One ->
       ``boundary`` (inside it, near an edge) or ``nearest`` (just outside).
    3. Several: duct connectivity first. The diffuser is already on this
       zone's outlet ducts, so if exactly one candidate is a room the zone
       serves through an interior diffuser, it wins (``duct``).
    4. Then depth: the candidate the point sits deepest in (or nearest to)
       wins when it leads the next by more than ROOM_TIE_M (``nearest``).
    5. Anything else is ``ambiguous``: no room, sent to review, never a guess.
    """
    depth = {r["id"]: _room_depth(x_m, y_m, r) for r in rooms}
    deep = [rid for rid, d in depth.items() if d >= ROOM_EDGE_MARGIN_M]
    if len(deep) == 1:
        return deep[0], "interior", deep
    cands = sorted(
        (rid for rid, d in depth.items() if d > -NEAREST_ROOM_MARGIN_M),
        key=lambda rid: -depth[rid],
    )
    if not cands:
        return None, "outside", []
    if len(cands) == 1:
        return cands[0], ("boundary" if depth[cands[0]] >= 0 else "nearest"), cands
    fed = [rid for rid in cands if rid in zone_rooms]
    if len(fed) == 1:
        return fed[0], "duct", cands
    pool = fed or cands
    if depth[pool[0]] - depth[pool[1]] > ROOM_TIE_M:
        return pool[0], "nearest", cands
    return None, "ambiguous", cands


def extract_zones(skel: np.ndarray, detections: list, rooms: list, px_per_m: float = PX_PER_M):
    """VAV cut-vertex zoning. Returns (zones, debug)."""
    mask = skel.astype(bool).copy()
    vavs = [d for d in detections if d["label"] == "vav"]
    difs = [d for d in detections if d["label"] == "diffuser"]
    sens = [d for d in detections if d["label"] == "sensor"]
    for b in vavs:
        # cut AT the VAV box (physical 60x30px + dilate), not the
        # 148x118 template window: severs tap + spine while keeping the
        # spine length accounting honest.
        x0 = max(0, int(b["cx"] - _VAV_BOX_W / 2) - _VAV_CUT_DILATE)
        x1 = int(b["cx"] + _VAV_BOX_W / 2) + _VAV_CUT_DILATE
        y0 = max(0, int(b["cy"] - _VAV_BOX_H / 2) - _VAV_CUT_DILATE)
        y1 = int(b["cy"] + _VAV_BOX_H / 2) + _VAV_CUT_DILATE
        mask[y0:y1, x0:x1] = False
    comp, ncomp = clabel(mask, structure=np.ones((3, 3)))

    zones = []
    for vi, b in enumerate(vavs):
        audit = [
            f"VAV-{vi + 1}: detected at ({b['cx']:.0f},{b['cy']:.0f})px "
            f"NCC={b['ncc']:.2f}({b['ncc_cls']})/WiSARD margin={b['margin']:.1f}"
        ]
        # ring around the VAV box -> adjacent skeleton components
        ox0 = max(0, int(b["cx"] - _VAV_BOX_W / 2) - 30)
        ox1 = int(b["cx"] + _VAV_BOX_W / 2) + 30
        oy0 = max(0, int(b["cy"] - _VAV_BOX_H / 2) - 30)
        oy1 = int(b["cy"] + _VAV_BOX_H / 2) + 30
        ring = np.zeros_like(mask)
        ring[oy0:oy1, ox0:ox1] = True
        ix0 = max(0, int(b["cx"] - _VAV_BOX_W / 2) - 4)
        ix1 = int(b["cx"] + _VAV_BOX_W / 2) + 4
        iy0 = max(0, int(b["cy"] - _VAV_BOX_H / 2) - 4)
        iy1 = int(b["cy"] + _VAV_BOX_H / 2) + 4
        ring[iy0:iy1, ix0:ix1] = False
        adj = [c for c in np.unique(comp[ring]) if c != 0]
        audit.append(f"{len(adj)} skeleton components touch the VAV box")
        # outlet side = adjacent components containing supply diffusers
        outlet, served = [], []
        for c in adj:
            ys, xs = np.nonzero(comp == c)
            has_dif = False
            for dfi, d in enumerate(difs):
                dist = np.hypot(xs - d["cx"], ys - d["cy"]).min()
                if dist <= ASSOC_PX:
                    has_dif = True
                    if dfi not in served:
                        served.append(dfi)
            if has_dif:
                outlet.append(c)
        npix = sum((comp == c).sum() for c in outlet)
        duct_m = npix / px_per_m
        audit.append(f"outlet skeleton: {len(outlet)} components, {npix}px = {duct_m:.2f} m duct")
        z_dis = [difs[dfi] for dfi in served]
        z_rooms, z_dif_rooms, z_review = [], [None] * len(z_dis), []
        # pass 1: interior diffusers fix the rooms this zone's ducts feed;
        # pass 2: boundary / outside diffusers lean on that (#684)
        pending = []
        for k, d in enumerate(z_dis):
            xm, ym = d["cx"] / px_per_m - _MARGIN, d["cy"] / px_per_m - _MARGIN
            depth = [_room_depth(xm, ym, r) for r in rooms]
            if sum(dd >= ROOM_EDGE_MARGIN_M for dd in depth) == 1:
                rid, how, _ = _assign_diffuser_room(xm, ym, rooms)
                z_dif_rooms[k] = rid
                if rid not in z_rooms:
                    z_rooms.append(rid)
                audit.append(
                    f"diffuser at ({d['cx']:.0f},{d['cy']:.0f})px "
                    f"-> room {rid} ({how}; skeleton dist <= {ASSOC_PX}px)"
                )
            else:
                pending.append((k, d, xm, ym))
        fed = tuple(z_rooms)
        for k, d, xm, ym in pending:
            rid, how, cands = _assign_diffuser_room(xm, ym, rooms, fed)
            z_dif_rooms[k] = rid
            if rid is None:
                z_review.append(
                    {
                        "kind": "diffuser_room_ambiguous",
                        "diffuser": (round(d["cx"], 1), round(d["cy"], 1)),
                        "reason": how,
                        "candidates": cands,
                    }
                )
                audit.append(
                    f"diffuser at ({d['cx']:.0f},{d['cy']:.0f})px -> no room "
                    f"({how}; candidates {cands or 'none'}) -> review"
                )
                continue
            if rid not in z_rooms:
                z_rooms.append(rid)
            audit.append(
                f"diffuser at ({d['cx']:.0f},{d['cy']:.0f})px "
                f"-> room {rid} ({how}; candidates {cands})"
            )
        z_sen = []
        for s in sens:
            rid = _room_of(s["cx"] / px_per_m - _MARGIN, s["cy"] / px_per_m - _MARGIN, rooms)
            if rid in z_rooms:
                z_sen.append(s)
                audit.append(
                    f"sensor at ({s['cx']:.0f},{s['cy']:.0f})px "
                    f"in room {rid} -> this zone (co-location)"
                )
        zones.append(
            {
                "zone_id": f"Z{vi + 1}",
                "vav_det": b,
                "rooms_served": sorted(z_rooms),
                "diffusers": [(round(d["cx"], 1), round(d["cy"], 1)) for d in z_dis],
                "diffuser_rooms": z_dif_rooms,
                "review": z_review,
                "sensors": [(round(s["cx"], 1), round(s["cy"], 1)) for s in z_sen],
                "duct_length_m": round(float(duct_m), 3),
                "audit": audit,
            }
        )
    return zones, {"n_skel_px": int(skel.sum()), "n_components": ncomp}


# ---------------------------------------------------------------------------
# Sheet margin note
# ---------------------------------------------------------------------------
# render_mech_sheet draws with MARGIN_M offset: px = (m + MARGIN_M)*PX_PER_M.
# Detections are in sheet px; convert with (px / PX_PER_M - MARGIN_M).
_MARGIN = 2.0

# Physical VAV box size (px) for the cut mask: the detection w/h are
# template-window sizes (148x118 incl. duct stubs), but the cut must sever
# ducts AT the box, not carve a 3m hole out of the spine.
_VAV_BOX_W = int(1.2 * PX_PER_M)  # VAV_W
_VAV_BOX_H = int(0.6 * PX_PER_M)  # VAV_H
_VAV_CUT_DILATE = 10


def suppress_vav_near_terminals(detections):
    """Drop VAV detections that are really a nearby diffuser or grille.

    The VAV template fires on grilles (box+diagonal on the return main) and
    diffusers (drop+spine behind the square). Distinct equipment never
    overlaps, so a VAV proposal whose nearest confirmed diffuser/grille is
    25-60 px away is that small symbol, not a VAV. (Cross-class NMS at 30 px
    already merged the closest duplicates; a terminal INSIDE the VAV box,
    under 25 px, does not suppress.)

    Sensors are not counted (#721): a thermostat 25-60 px from its VAV box
    is ordinary, and counting it removed true VAVs on synthetic seeds 11 and
    33 and left their rooms unzoned. Returns (kept, n_suppressed).
    """
    smalls = [d for d in detections if d["label"] in ("diffuser", "grille")]
    kept = []
    n_suppressed = 0
    for d in detections:
        if d["label"] == "vav" and smalls:
            min_dist = min(math.hypot(d["cx"] - s["cx"], d["cy"] - s["cy"]) for s in smalls)
            if 25 < min_dist < 60:
                n_suppressed += 1
                continue
        kept.append(d)
    return kept, n_suppressed


def trace_sheet(
    gray: np.ndarray,
    gt: dict,
    clf: WisardClassifier,
    templates: dict,
    provenance: Provenance | None = None,
):
    detections = detect_components(gray, templates, clf)
    detections, n_suppressed = suppress_vav_near_terminals(detections)
    skel = duct_skeleton(gray)
    # detections are sheet px; extract_zones converts to meters with the
    # margin offset before the diffuser -> room assignment.
    zones, dbg = extract_zones(skel, detections, gt["rooms"], PX_PER_M)
    # report detections in meters for readability
    for z in zones:
        z["diffusers"] = [
            (round(x / PX_PER_M - _MARGIN, 2), round(y / PX_PER_M - _MARGIN, 2))
            for x, y in z["diffusers"]
        ]
        z["sensors"] = [
            (round(x / PX_PER_M - _MARGIN, 2), round(y / PX_PER_M - _MARGIN, 2))
            for x, y in z["sensors"]
        ]
        for item in z["review"]:
            x, y = item["diffuser"]
            item["diffuser"] = (round(x / PX_PER_M - _MARGIN, 2), round(y / PX_PER_M - _MARGIN, 2))
    agree = sum(1 for d in detections if d["label"] == d["ncc_cls"])
    det_m = [
        dict(d, cx=d["cx"] / PX_PER_M - _MARGIN, cy=d["cy"] / PX_PER_M - _MARGIN)
        for d in detections
    ]
    return {
        "zones": zones,
        "review": [dict(item, zone_id=z["zone_id"]) for z in zones for item in z["review"]],
        "detections": det_m,
        "skel_px": dbg["n_skel_px"],
        "ncc_wisard_agreement": f"{agree}/{len(detections)}",
        "n_vav_suppressed": n_suppressed,
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _match_detections(pred, gt_comps, cls, tol_px=25):
    """Greedy matching. pred detections are already in meters
    (trace_sheet converts); gt_comps carry x_m/y_m meters."""
    pp = [(d["cx"], d["cy"]) for d in pred if d["label"] == cls]
    gg = [(c["x_m"], c["y_m"]) for c in gt_comps if c["type"] == cls]
    used = set()
    tp = 0
    for px, py in pp:
        best, bi = 1e9, -1
        for i, (gx, gy) in enumerate(gg):
            if i in used:
                continue
            dd = math.hypot(px - gx, py - gy)
            if dd < best:
                best, bi = dd, i
        if bi >= 0 and best * PX_PER_M <= tol_px:
            tp += 1
            used.add(bi)
    return tp, len(pp) - tp, len(gg) - tp


def validate_hvac_solution(seeds, clf, templates, realistic: bool = False):
    from synth.mech import generate_mech_sheet

    rows = []
    for seed in seeds:
        img, gt = generate_mech_sheet(seed, realistic=realistic)
        gray = np.asarray(img).astype(np.float64)
        res = trace_sheet(gray, gt, clf, templates)
        # detection P/R
        det = {}
        for cls in MECH_CLASSES:
            tp, fp, fn = _match_detections(res["detections"], gt["components"], cls)
            det[cls] = {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "p": tp / max(1, tp + fp),
                "r": tp / max(1, tp + fn),
            }
        # zone mapping: pred zone -> GT zone by room overlap
        gt_zones = {z["zone_id"]: z for z in gt["zones"]}
        zmap = {}
        for z in res["zones"]:
            best, bi = -1, None
            for gz in gt["zones"]:
                ov = len(set(z["rooms_served"]) & set(gz["room_ids"]))
                if ov > best:
                    best, bi = ov, gz["zone_id"]
            zmap[z["zone_id"]] = bi if best > 0 else None
        # room-pair Rand index (no id matching needed)
        rooms = [r["id"] for r in gt["rooms"]]
        gt_of = {r: next(z["zone_id"] for z in gt["zones"] if r in z["room_ids"]) for r in rooms}
        pr_of = {}
        for z in res["zones"]:
            for r in z["rooms_served"]:
                pr_of[r] = z["zone_id"]
        agree = tot = 0
        for i in range(len(rooms)):
            for j in range(i + 1, len(rooms)):
                a, b = rooms[i], rooms[j]
                if a in pr_of and b in pr_of:
                    tot += 1
                    same_gt = gt_of[a] == gt_of[b]
                    same_pr = (
                        zmap.get(pr_of[a]) == zmap.get(pr_of[b]) and zmap.get(pr_of[a]) is not None
                    )
                    agree += same_gt == same_pr
        rand = agree / max(1, tot)
        # sensor accuracy (matched by nearest GT sensor)
        gt_sen = [c for c in gt["components"] if c["type"] == "sensor"]
        pred_sen = [d for d in res["detections"] if d["label"] == "sensor"]
        sen_ok = sen_tot = 0
        for ps in pred_sen:
            px, py = ps["cx"], ps["cy"]
            best, bg = 1e9, None
            for g in gt_sen:
                dd = math.hypot(px - g["x_m"], py - g["y_m"])
                if dd < best:
                    best, bg = dd, g
            if bg is not None and best * PX_PER_M <= 30:
                sen_tot += 1
                # pred zone containing ps's room vs GT zone of bg's room
                pz = next(
                    (z["zone_id"] for z in res["zones"] if bg["room_id"] in z["rooms_served"]), None
                )
                gz = next(z["zone_id"] for z in gt["zones"] if bg["room_id"] in z["room_ids"])
                if pz is not None and zmap.get(pz) == gz:
                    sen_ok += 1
        # duct length error per mapped zone
        lerrs = []
        for z in res["zones"]:
            gz = zmap.get(z["zone_id"])
            if gz:
                true = gt_zones[gz]["duct_length_m"]
                lerrs.append(abs(z["duct_length_m"] - true) / max(true, 1e-6))
        rows.append(
            {
                "seed": seed,
                "det": det,
                "room_rand": round(rand, 4),
                "sensor_acc": (round(sen_ok / max(1, sen_tot), 4), f"{sen_ok}/{sen_tot}"),
                "duct_len_rel_err": round(float(np.mean(lerrs)), 4) if lerrs else None,
                "ncc_wisard_agreement": res["ncc_wisard_agreement"],
                "zones": [
                    {k: z[k] for k in ("zone_id", "rooms_served", "duct_length_m")}
                    for z in res["zones"]
                ],
            }
        )
    return rows


def main():
    print("training WiSARD on sheet-cut synthetic mech crops ...", flush=True)
    X, y, names = training_crops_from_sheets(
        n_per_class=200,
        seed=0,
        bg_per_sheet=8,
        context_per_class=CONTEXT_PER_CLASS,
        context_bg=CONTEXT_BG,
    )
    clf = WisardClassifier(len(names), seed=42)
    clf.fit(X, y)
    tr = clf.predict_logodds(X)
    print(f"train acc: {(tr == y).mean():.4f} ({len(y)} crops)")
    templates = {c: render_template(c) for c in MECH_CLASSES}
    seeds = [11, 22, 33, 44]
    rows = validate_hvac_solution(seeds, clf, templates)
    Path("synth/out").mkdir(exist_ok=True)
    with open("synth/out/mech_trace_results.json", "w") as f:
        json.dump(rows, f, indent=1)
    for r in rows:
        print(f"--- seed {r['seed']} ---")
        dp = {
            c: f"{v['tp']}/{v['tp'] + v['fp'] + v['fn']} P={v['p']:.2f} R={v['r']:.2f}"
            for c, v in r["det"].items()
        }
        print("det:", dp)
        print(
            f"room Rand: {r['room_rand']}  sensor acc: {r['sensor_acc']}  "
            f"duct len rel err: {r['duct_len_rel_err']}  "
            f"ncc/wisard agree: {r['ncc_wisard_agreement']}"
        )
        print("zones:", r["zones"])
    print("wrote synth/out/mech_trace_results.json")


if __name__ == "__main__":
    main()
