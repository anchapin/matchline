"""Synthetic mechanical floor plans for the HVAC zoning spike.

Generates clean vector-style mechanical plans: rectangular footprint,
guillotine-subdivided rooms, one AHU feeding a supply trunk, 1-3 VAV
terminal units each serving a contiguous zone of rooms via branch ducts
to supply diffusers, a return-air system (grilles + return main, drawn to
*overlap* supply ducts at gapped crossings), and one temperature sensor
(circle + "T") per room.

The duct network is built as an explicit graph in layout space FIRST
(nodes = AHU / VAVs / junctions / diffusers, edges = duct runs), then
rendered. Ground truth JSON carries the full graph plus the zone
roll-up, so the tracer (hvac_trace.py) can be scored without ambiguity.

Rendering choices (documented, spike-scoped):
- Ducts are rendered as SOLID FILLED bars, not the double-line outlines
  real CAD uses. This isolates the graph-extraction proof from the
  wall-pairing problem; pairing double lines into centerlines is a known
  v2 step (flagged in `docs/hvac_trace.md`).
- Crossings between independent nets are rendered with a GAP in exactly
  one duct (return ducts and the supply trunk may be gapped; taps,
  spines and drops never are, so every supply zone subgraph stays
  connected in the raster).
- All duct runs are axis-aligned (Manhattan), as in real commercial
  ductwork.

Coordinate frames: layout in meters, y growing downward (matches image
coords). PX_PER_M converts to sheet pixels.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .sheets import MIN_ROOM_M, ROOM_NAMES, _font

PX_PER_M = 50.0
MARGIN_M = 2.0
GAP_HALF_M = 0.18  # half-length of the rendered crossing gap

# Duct widths (m)
W_TRUNK, W_TAP, W_SPINE, W_DROP = 0.80, 0.50, 0.40, 0.30
W_RMAIN, W_RDROP = 0.50, 0.30

# Symbol sizes (m)
VAV_W, VAV_H = 1.20, 0.60
AHU_W, AHU_H = 2.00, 1.20
DIF_S = 0.60  # supply diffuser square
GRI_S = 0.40  # return grille square
SEN_R = 0.15  # temperature sensor circle radius

MECH_CLASSES = ["vav", "ahu", "diffuser", "grille", "sensor"]

# Which classes get duct stubs baked into their NCC template. Diffuser /
# grille templates stay stub-less: a stubbed vertical-bar template is
# bar-dominated and fires all along bare duct runs. VAV / AHU need stubs:
# without them the sheet's attached duct ink fills template-white regions
# and true locations score < 0.5.
TEMPLATE_STUBS = {"vav": True, "ahu": True, "diffuser": False, "grille": False, "sensor": False}


# ---------------------------------------------------------------------------
# Glyph drawing (sheet-pixel units). Shared by the sheet renderer, the NCC
# templates, and the WiSARD training-crop renderer so all three see the
# same symbol distribution.
# ---------------------------------------------------------------------------


def _draw_vav(d: ImageDraw.ImageDraw, cx, cy, lw: int = 3):
    w, h = VAV_W * PX_PER_M, VAV_H * PX_PER_M
    d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=0, width=lw)
    d.line([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=0, width=2)


def _draw_ahu(d: ImageDraw.ImageDraw, cx, cy, lw: int = 3):
    w, h = AHU_W * PX_PER_M, AHU_H * PX_PER_M
    d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=0, width=lw)
    d.rectangle(
        [cx - w / 2 + 6, cy - h / 2 + 6, cx + w / 2 - 6, cy + h / 2 - 6], outline=0, width=2
    )
    r = h / 2 - 10
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=2)


def _draw_diffuser(d: ImageDraw.ImageDraw, cx, cy, lw: int = 3):
    s = DIF_S * PX_PER_M
    d.rectangle([cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2], outline=0, width=lw)
    d.line([cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2], fill=0, width=2)
    d.line([cx - s / 2, cy + s / 2, cx + s / 2, cy - s / 2], fill=0, width=2)


def _draw_grille(d: ImageDraw.ImageDraw, cx, cy, lw: int = 3):
    s = GRI_S * PX_PER_M
    d.rectangle([cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2], outline=0, width=lw)
    d.line([cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2], fill=0, width=2)


def _draw_sensor(d: ImageDraw.ImageDraw, cx, cy, lw: int = 2):
    r = SEN_R * PX_PER_M
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=lw)
    f = _font(16)
    t = "T"
    bb = d.textbbox((0, 0), t, font=f)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), t, fill=0, font=f)


def _draw_tag(d: ImageDraw.ImageDraw, cx, cy, w_m, h_m, text: str):
    """Equipment tag text drawn just below the symbol box."""
    f = _font(15)
    bb = d.textbbox((0, 0), text, font=f)
    tx = cx - (bb[2] - bb[0]) / 2 - bb[0]
    ty = cy + h_m * PX_PER_M / 2 + 5
    d.text((tx, ty), text, fill=0, font=f)


_GLYPH_FN = {
    "vav": _draw_vav,
    "ahu": _draw_ahu,
    "diffuser": _draw_diffuser,
    "grille": _draw_grille,
    "sensor": _draw_sensor,
}
# Tight symbol extents in meters (template windows / GT bboxes).
_GLYPH_EXT = {
    "vav": (VAV_W, VAV_H),
    "ahu": (AHU_W, AHU_H),
    "diffuser": (DIF_S, DIF_S),
    "grille": (GRI_S, GRI_S),
    "sensor": (2 * SEN_R + 0.10, 2 * SEN_R + 0.10),
}


# ---------------------------------------------------------------------------
# Duct network collector with crossing gaps
# ---------------------------------------------------------------------------


class _Net:
    """Collects axis-aligned duct pieces, declared junctions, and applies
    crossing gaps. A piece is ((x1,y1),(x2,y2),width_m,duct_id)."""

    def __init__(self):
        self.pieces = []
        self.junctions = []  # [(x, y)] declared intended connections
        self.crossings = []  # GT: [{x_m, y_m, gapped, kept}]

    def add_piece(self, x1, y1, x2, y2, width_m, duct_id):
        assert x1 == x2 or y1 == y2, "ducts must be axis-aligned"
        self.pieces.append(
            {
                "p1": (float(x1), float(y1)),
                "p2": (float(x2), float(y2)),
                "w": width_m,
                "duct": duct_id,
            }
        )

    def add_junction(self, x, y):
        self.junctions.append((float(x), float(y)))

    @staticmethod
    def _cross(p, q):
        """Interior crossing point of axis-aligned segments p, q, or None.
        Endpoint touches (within 2 mm) count as junctions, not crossings."""
        (x1, _y1), (x2, _y2) = p["p1"], p["p2"]
        (x3, _y3), (x4, _y4) = q["p1"], q["p2"]
        vert_p = x1 == x2
        vert_q = x3 == x4
        if vert_p == vert_q:
            return None
        v, h = (p, q) if vert_p else (q, p)
        (vx1, vy1), (_vx2, vy2) = v["p1"], v["p2"]
        (hx1, hy1), (hx2, _hy2) = h["p1"], h["p2"]
        cx, cy = vx1, hy1
        in_v = min(vy1, vy2) + 1e-3 < cy < max(vy1, vy2) - 1e-3
        in_h = min(hx1, hx2) + 1e-3 < cx < max(hx1, hx2) - 1e-3
        return (cx, cy) if (in_v and in_h) else None

    def _near_junction(self, pt):
        return any(math.hypot(pt[0] - jx, pt[1] - jy) < 0.05 for jx, jy in self.junctions)

    def apply_crossings(self):
        """Split gapped pieces around each true crossing. Gap priority:
        return ducts first, then the supply trunk; taps/spines/drops are
        never gapped so zone subgraphs stay connected. All gaps on one
        piece are applied in a single pass so multi-crossing pieces get
        disjoint sub-segments."""
        gaps: dict[int, list[tuple[float, float]]] = {}
        for i in range(len(self.pieces)):
            for j in range(i + 1, len(self.pieces)):
                a, b = self.pieces[i], self.pieces[j]
                if a["duct"] == b["duct"]:
                    continue
                pt = self._cross(a, b)
                if pt is None or self._near_junction(pt):
                    continue
                da, db = a["duct"], b["duct"]
                if da.startswith("R-"):
                    gapped, kept = a, b
                elif db.startswith("R-"):
                    gapped, kept = b, a
                elif "TRUNK" in da:
                    gapped, kept = a, b
                elif "TRUNK" in db:
                    gapped, kept = b, a
                else:  # excluded by contiguous-x zone layout
                    raise AssertionError(f"unexpected supply-supply crossing {da} x {db}")
                self.crossings.append(
                    {
                        "x_m": round(pt[0], 3),
                        "y_m": round(pt[1], 3),
                        "gapped": gapped["duct"],
                        "kept": kept["duct"],
                    }
                )
                # gap half-width clears the KEPT duct plus 0.1 m (5 px)
                # so the skeleton cannot bridge through the kept duct
                gaps.setdefault(id(gapped), []).append((pt, kept["w"] / 2 + 0.10))
        for p in self.pieces:
            pts = gaps.get(id(p))
            if not pts:
                continue
            (x1, y1), (x2, y2) = p["p1"], p["p2"]
            vertical = x1 == x2
            lo, hi = (min(y1, y2), max(y1, y2)) if vertical else (min(x1, x2), max(x1, x2))
            # (center, half_width) along the piece axis
            centers = sorted(set((round((pt[1] if vertical else pt[0]), 3), hw) for pt, hw in pts))
            # merge gap intervals that would overlap
            cuts, cur = [], None
            for c, hw in centers:
                a0, a1 = c - hw, c + hw
                if cur is not None and a0 <= cur[1]:
                    cur = (cur[0], max(cur[1], a1))
                else:
                    if cur is not None:
                        cuts.append(cur)
                    cur = (a0, a1)
            if cur is not None:
                cuts.append(cur)
            segs, edge = [], lo
            for a0, a1 in cuts:
                if a0 - edge > 0.05:
                    segs.append((edge, a0))
                edge = a1
            if hi - edge > 0.05:
                segs.append((edge, hi))
            p["sub"] = [
                ((x1, s0), (x1, s1)) if vertical else ((s0, y1), (s1, y1)) for s0, s1 in segs
            ]

    def iter_draw(self):
        """Yield (x1,y1,x2,y2,width_m) render rects in meters."""
        for p in self.pieces:
            for (x1, y1), (x2, y2) in p.get("sub") or [(p["p1"], p["p2"])]:
                yield x1, y1, x2, y2, p["w"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _layout_mech(rng):
    """Full plan description in meters. All randomness flows from rng."""
    W = float(rng.uniform(18, 26))
    D = float(rng.uniform(12, 18))
    # Column-strip layout: each VAV zone owns a vertical strip, so zone
    # x-ranges are disjoint by construction and supply-supply duct
    # crossings (spine x foreign drop) cannot occur. Rooms tile each
    # strip; 4-8 rooms total.
    n_vav = int(rng.integers(1, 4))
    while True:
        if n_vav == 1:
            per_strip = [int(rng.integers(4, 9))]
        else:
            per_strip = [int(rng.integers(2, 4)) for _ in range(n_vav)]
        if 4 <= sum(per_strip) <= 8:
            break
    names = list(ROOM_NAMES)
    rng.shuffle(names)
    room_info, groups = [], []
    ri = 0
    for zi in range(n_vav):
        x0s, x1s = zi * W / n_vav, (zi + 1) * W / n_vav
        n_r = per_strip[zi]
        cuts = sorted(float(c) for c in rng.uniform(0.5, D - 0.5, n_r - 1))
        bounds = [0.0] + cuts + [float(D)]
        if not all(bounds[k + 1] - bounds[k] >= MIN_ROOM_M for k in range(n_r)):
            bounds = [k * D / n_r for k in range(n_r + 1)]  # even fallback
        grp = []
        for k in range(n_r):
            y0, y1 = bounds[k], bounds[k + 1]
            room_info.append(
                {
                    "id": f"R{ri + 1}",
                    "name": names[ri % len(names)],
                    "number": str(201 + ri),
                    "rect_m": [x0s, y0, x1s, y1],
                    "area_m2": round((x1s - x0s) * (y1 - y0), 2),
                    "cx": (x0s + x1s) / 2,
                    "cy": (y0 + y1) / 2,
                }
            )
            grp.append(ri)
            ri += 1
        groups.append(grp)

    y_trunk = D / 2
    ahu = {
        "id": "AHU-1",
        "type": "ahu",
        "x_m": 1.5,
        "y_m": y_trunk,
        "w_m": AHU_W,
        "h_m": AHU_H,
        "tag": "AHU-1",
        "room_id": next(
            r["id"]
            for r in room_info
            if r["rect_m"][0] <= 1.5 <= r["rect_m"][2]
            and r["rect_m"][1] <= y_trunk <= r["rect_m"][3]
        ),
    }

    net = _Net()
    vavs, diffusers, grilles, sensors = [], [], [], []
    dif_i = gri_i = sen_i = 0

    for zi, grp in enumerate(groups):
        zrooms = [room_info[i] for i in grp]
        # diffusers first (VAV is nudged clear of their drops below)
        dxs = []
        for r in zrooms:
            x0, y0, x1, y1 = r["rect_m"]
            n_d = 2 if r["area_m2"] >= 28 else 1
            offs = [-0.22 * (x1 - x0), 0.22 * (x1 - x0)] if n_d == 2 else [0.0]
            for ox in offs:
                dif_i += 1
                dx, dy = r["cx"] + ox, r["cy"]
                dxs.append(dx)
                diffusers.append(
                    {
                        "id": f"D{dif_i}",
                        "type": "diffuser",
                        "x_m": dx,
                        "y_m": dy,
                        "w_m": DIF_S,
                        "h_m": DIF_S,
                        "tag": f"D{dif_i}",
                        "room_id": r["id"],
                        "vav": f"VAV-{zi + 1}",
                    }
                )
        # VAV at zone centroid, nudged so no drop passes through its box
        vav_x = float(np.mean([r["cx"] for r in zrooms]))
        sign = 1.0 if zi % 2 == 0 else -1.0
        vav_y = y_trunk + sign * float(rng.uniform(1.8, 3.2))
        if dxs and min(abs(dx - vav_x) for dx in dxs) < 1.0:
            vav_x += 2.0 if vav_x < W / 2 else -2.0
            vav_x = min(max(vav_x, 1.5), W - 1.5)
        vav = {
            "id": f"VAV-{zi + 1}",
            "type": "vav",
            "x_m": vav_x,
            "y_m": vav_y,
            "w_m": VAV_W,
            "h_m": VAV_H,
            "tag": f"VAV-{zi + 1}",
            "room_id": None,
        }
        vav["room_id"] = next(
            r["id"]
            for r in room_info
            if r["rect_m"][0] <= vav_x <= r["rect_m"][2]
            and r["rect_m"][1] <= vav_y <= r["rect_m"][3]
        )
        vavs.append(vav)
        net.add_junction(vav_x, y_trunk)  # tap meets trunk
        net.add_junction(vav_x, vav_y)  # tap meets spine (in VAV box)
        # tap, spine, drops
        net.add_piece(vav_x, y_trunk, vav_x, vav_y, W_TAP, f"S-TAP-{zi + 1}")
        sx0, sx1 = min(dxs) - 0.4, max(dxs) + 0.4
        net.add_piece(sx0, vav_y, sx1, vav_y, W_SPINE, f"S-SPINE-{zi + 1}")
        for dx, d in zip(dxs, [d for d in diffusers if d["vav"] == vav["id"]]):
            net.add_junction(dx, vav_y)  # drop meets spine
            net.add_piece(dx, vav_y, dx, d["y_m"], W_DROP, f"S-DROP-{d['id']}")

    # supply trunk after taps exist (junctions declared above)
    trunk_x2 = max(v["x_m"] for v in vavs) + 1.0
    net.add_piece(ahu["x_m"], y_trunk, trunk_x2, y_trunk, W_TRUNK, "S-TRUNK")

    # -- return system ------------------------------------------------------
    y_ret = D - 1.5
    net.add_piece(1.0, y_ret, W - 1.0, y_ret, W_RMAIN, "R-MAIN")
    net.add_piece(1.0, y_ret, 1.0, y_trunk, W_RDROP, "R-CONN")
    net.add_piece(1.0, y_trunk, 0.6, y_trunk, W_RDROP, "R-CONN")
    for r in room_info:
        gri_i += 1
        x0, y0, x1, y1 = r["rect_m"]
        # near the left wall: clear of diffuser drops (which sit at
        # cx +/- 0.22w or cx) so return and supply bars never merge
        gx = x0 + 0.15 * (x1 - x0)
        gy = r["cy"] + 0.25 * (y1 - y0)
        grilles.append(
            {
                "id": f"G{gri_i}",
                "type": "grille",
                "x_m": gx,
                "y_m": gy,
                "w_m": GRI_S,
                "h_m": GRI_S,
                "tag": f"G{gri_i}",
                "room_id": r["id"],
            }
        )
        net.add_junction(gx, y_ret)
        net.add_piece(gx, gy, gx, y_ret, W_RDROP, f"R-DROP-G{gri_i}")

    # -- temperature sensors (one per room, on the top wall) ----------------
    for r in room_info:
        sen_i += 1
        x0, y0, x1, y1 = r["rect_m"]
        sensors.append(
            {
                "id": f"T{sen_i}",
                "type": "sensor",
                "x_m": (x0 + x1) / 2,
                "y_m": y0 + 0.35,
                "w_m": 2 * SEN_R + 0.10,
                "h_m": 2 * SEN_R + 0.10,
                "tag": f"T{sen_i}",
                "room_id": r["id"],
                "vav": next(
                    v["id"]
                    for zi, v in enumerate(vavs)
                    if r["id"] in [room_info[i]["id"] for i in groups[zi]]
                ),
            }
        )

    net.apply_crossings()

    # -- GT graph (supply only) ---------------------------------------------
    nodes, edges = [{"id": "AHU-1", "kind": "ahu", "x_m": ahu["x_m"], "y_m": ahu["y_m"]}], []

    def _add_edge(a, b, duct, pts):
        length = sum(
            math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
            for k in range(len(pts) - 1)
        )
        edges.append({"a": a, "b": b, "duct": duct, "length_m": round(length, 3)})

    jn = 0
    for vav in vavs:
        nodes.append({"id": vav["id"], "kind": "vav", "x_m": vav["x_m"], "y_m": vav["y_m"]})
    prev, px = "AHU-1", ahu["x_m"]
    for tx, tid in sorted((v["x_m"], v["id"]) for v in vavs):
        jn += 1
        jid = f"J{jn}"
        nodes.append({"id": jid, "kind": "junction", "x_m": tx, "y_m": y_trunk})
        _add_edge(prev, jid, "S-TRUNK", [(px, y_trunk), (tx, y_trunk)])
        _add_edge(
            jid,
            tid,
            f"S-TAP-{tid.split('-')[1]}",
            [(tx, y_trunk), (tx, vavs[[v["id"] for v in vavs].index(tid)]["y_m"])],
        )
        prev, px = jid, tx
    for zi, vav in enumerate(vavs):
        vid, vx, vy = vav["id"], vav["x_m"], vav["y_m"]
        zdis = [d for d in diffusers if d["vav"] == vid]
        tees = sorted(set(d["x_m"] for d in zdis))
        prev_n, px = vid, vx
        for tx in tees:
            jn += 1
            jid = f"J{jn}"
            nodes.append({"id": jid, "kind": "junction", "x_m": tx, "y_m": vy})
            _add_edge(prev_n, jid, f"S-SPINE-{zi + 1}", [(px, vy), (tx, vy)])
            prev_n, px = jid, tx
        # spine end stub
        sx1 = max(d["x_m"] for d in zdis) + 0.4
        _add_edge(prev_n, f"{vid}-END", f"S-SPINE-{zi + 1}", [(px, vy), (sx1, vy)])
        nodes.append({"id": f"{vid}-END", "kind": "spine_end", "x_m": sx1, "y_m": vy})
        for d in zdis:
            jid = next(
                n["id"]
                for n in nodes
                if n["kind"] == "junction" and n["x_m"] == d["x_m"] and abs(n["y_m"] - vy) < 1e-9
            )
            nodes.append({"id": d["id"], "kind": "diffuser", "x_m": d["x_m"], "y_m": d["y_m"]})
            _add_edge(jid, d["id"], f"S-DROP-{d['id']}", [(d["x_m"], vy), (d["x_m"], d["y_m"])])
    for s in sensors:
        nodes.append({"id": s["id"], "kind": "sensor", "x_m": s["x_m"], "y_m": s["y_m"]})

    zones = []
    for zi, vav in enumerate(vavs):
        zrooms = [room_info[i]["id"] for i in groups[zi]]
        zdis = [d["id"] for d in diffusers if d["vav"] == vav["id"]]
        zsen = [s["id"] for s in sensors if s["vav"] == vav["id"]]
        duct_len = sum(
            e["length_m"]
            for e in edges
            if e["duct"].startswith(f"S-SPINE-{zi + 1}")
            or e["duct"].startswith("S-DROP-")
            and e["b"] in zdis
        )
        zones.append(
            {
                "zone_id": f"Z{zi + 1}",
                "vav_id": vav["id"],
                "room_ids": sorted(zrooms),
                "sensor_ids": sorted(zsen),
                "diffuser_ids": sorted(zdis),
                "duct_length_m": round(duct_len, 3),
            }
        )

    gt = {
        "seed": None,
        "px_per_m": PX_PER_M,
        "W_m": W,
        "D_m": D,
        "rooms": [
            {k: r[k] for k in ("id", "name", "number", "rect_m", "area_m2")} for r in room_info
        ],
        "components": [ahu] + vavs + diffusers + grilles + sensors,
        "ducts": [
            {
                "id": p["duct"],
                "width_m": p["w"],
                "pieces_m": [[list(s[0]), list(s[1])] for s in p.get("sub", [(p["p1"], p["p2"])])],
            }
            for p in net.pieces
        ],
        "graph": {"nodes": nodes, "edges": edges},
        "crossings": net.crossings,
        "zones": zones,
    }
    return net, gt, room_info


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _m2px(x_m, y_m):
    return ((x_m + MARGIN_M) * PX_PER_M, (y_m + MARGIN_M) * PX_PER_M)


def render_mech_sheet(net, gt, room_info):
    W, D = gt["W_m"], gt["D_m"]
    img = Image.new(
        "L", (int((W + 2 * MARGIN_M) * PX_PER_M), int((D + 2 * MARGIN_M) * PX_PER_M)), 255
    )
    d = ImageDraw.Draw(img)
    # walls + room labels (thin: removed by the tracer's duct opening)
    for r in room_info:
        x0, y0, x1, y1 = r["rect_m"]
        (px0, py0), (px1, py1) = _m2px(x0, y0), _m2px(x1, y1)
        d.rectangle([px0, py0, px1, py1], outline=0, width=3)
        f = _font(18)
        d.text((px0 + 8, py0 + 6), f"{r['number']} {r['name']}", fill=0, font=f)
    # ducts: solid filled bars. Pad ONLY perpendicular to the duct axis:
    # padding along the axis would fill the crossing gaps that keep the
    # duct graph topologically honest.
    for x1, y1, x2, y2, w in net.iter_draw():
        (px1, py1), (px2, py2) = _m2px(x1, y1), _m2px(x2, y2)
        hw = w * PX_PER_M / 2
        if abs(px1 - px2) >= abs(py1 - py2):  # horizontal
            d.rectangle(
                [min(px1, px2), min(py1, py2) - hw, max(px1, px2), max(py1, py2) + hw], fill=0
            )
        else:  # vertical
            d.rectangle(
                [min(px1, px2) - hw, min(py1, py2), max(px1, px2) + hw, max(py1, py2)], fill=0
            )
    # symbols (drawn over ducts, as in real plans)
    for c in gt["components"]:
        cx, cy = _m2px(c["x_m"], c["y_m"])
        _GLYPH_FN[c["type"]](d, cx, cy)
        if c["type"] in ("vav", "ahu"):
            _draw_tag(d, cx, cy, c["w_m"], c["h_m"], c["tag"])
    return img


def generate_mech_sheet(seed: int):
    rng = np.random.default_rng(seed)
    net, gt, room_info = _layout_mech(rng)
    gt["seed"] = seed
    return render_mech_sheet(net, gt, room_info), gt


def save_mech_sheet(seed: int, outdir: str | Path) -> tuple[Path, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    img, gt = generate_mech_sheet(seed)
    pp, jp = outdir / f"mech_{seed:03d}.png", outdir / f"mech_{seed:03d}.json"
    img.save(pp)
    jp.write_text(json.dumps(gt, indent=1))
    return pp, jp


# ---------------------------------------------------------------------------
# Symbol templates (NCC) and WiSARD training crops
# ---------------------------------------------------------------------------


def render_template(cls: str, margin_px: int = 4, stubs: bool | None = None) -> np.ndarray:
    """Grayscale template of one symbol at sheet scale.

    With stubs=True (default per TEMPLATE_STUBS) the template includes the
    duct stubs that attach to the symbol on real sheets (spine through the
    VAV, tap above/below, trunk into the AHU, drops into diffusers/grilles).
    Stub-less templates score <0.5 at true VAV/AHU locations because the
    sheet's duct ink fills template-white regions; stubbed templates
    are what make NCC localization work on connected equipment.
    """
    if stubs is None:
        stubs = TEMPLATE_STUBS[cls]
    w_m, h_m = _GLYPH_EXT[cls]
    sw, sh = w_m * PX_PER_M, h_m * PX_PER_M
    # extra canvas for stub bars applies only to stubbed templates;
    # stub-less templates stay tight so attached ducts fall outside
    # the correlation window instead of filling template-white.
    ex, ey = (
        {"vav": (40, 40), "ahu": (48, 8), "diffuser": (8, 30), "grille": (8, 30), "sensor": (0, 0)}[
            cls
        ]
        if stubs
        else (0, 0)
    )
    W = int(sw + 2 * (margin_px + ex))
    H = int(sh + 2 * (margin_px + ey))
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2
    if stubs:
        if cls == "vav":
            d.rectangle([0, cy - 10, W, cy + 10], fill=0)  # spine
            d.rectangle([cx - 12.5, 0, cx + 12.5, H], fill=0)  # tap
            # equipment tag blob: every real VAV carries a "VAV-N" tag
            # below its box; the grille/diffuser confusers never do.
            # Solid-blob-vs-text still correlates positively at true
            # sites and penalizes tag-less false alarms.
            d.rectangle([cx - 24, cy + 20, cx + 24, cy + 35], fill=0)
        elif cls == "ahu":
            d.rectangle([cx, cy - 20, W, cy + 20], fill=0)  # trunk
            d.rectangle([0, cy - 7.5, cx, cy + 7.5], fill=0)  # return
        elif cls in ("diffuser", "grille"):
            d.rectangle([cx - 7.5, 0, cx + 7.5, H], fill=0)  # drop
    _GLYPH_FN[cls](d, cx, cy)
    return np.asarray(img).astype(np.float64)


def detection_crop(gray: np.ndarray, cx_px: float, cy_px: float, cls: str) -> np.ndarray:
    """Cut the exact crop the detector feeds the classifier.

    Shared by training (cut at GT positions) and inference (cut at NCC
    proposals) so the WiSARD classifier never sees a distribution shift
    between train and test crops.
    """
    from datasets_adapter import normalize_crop

    tmpl = render_template(cls)
    th, tw = tmpl.shape
    # tight crop: symbol ink is sparse, and every extra white pixel is a
    # tuple that votes for the majority class. +16 keeps stub context
    # without drowning the glyph.
    side = int(max(tw, th) + 16)
    pad = side
    gp = np.pad(gray, pad)
    cx, cy = cx_px + pad, cy_px + pad + 6
    x0, y0 = int(cx - side / 2), int(cy - side / 2)
    crop = gp[y0 : y0 + side, x0 : x0 + side]
    return normalize_crop(crop.astype(np.float64))


def training_crops_from_sheets(n_per_class: int = 200, seed: int = 0, bg_per_sheet: int = 50):
    """WiSARD training set cut from synthetic sheets at GT positions.

    ±6 px jitter on the crop center mirrors NCC proposal error so the
    classifier is robust to the localization noise it will see at
    inference. A 6th "background" class is cut at random sheet positions
    (>= 60 px from any symbol) with randomly chosen symbol crop sizes;
    without it the forced 5-way choice confidently mislabels duct
    junctions and wall corners as symbols. Returns (X, y, classes).
    """
    rng = np.random.default_rng(seed)
    X, y = [], []
    counts = {c: 0 for c in MECH_CLASSES}
    s = 0
    while min(counts.values()) < n_per_class:
        img, gt = generate_mech_sheet(1000 + s)
        s += 1
        W, H = img.size
        gray = np.asarray(img).astype(np.float64)
        comps = gt["components"]
        for c in comps:
            cls = c["type"]
            if counts[cls] >= n_per_class:
                continue
            jx, jy = rng.uniform(-6, 6, 2)
            cx = (c["x_m"] + MARGIN_M) * PX_PER_M + jx
            cy = (c["y_m"] + MARGIN_M) * PX_PER_M + jy
            X.append(detection_crop(gray, cx, cy, cls))
            y.append(MECH_CLASSES.index(cls))
            counts[cls] += 1
        # background crops only from the first sheets: AHU (1/sheet)
        # forces ~200 sheets, which would otherwise drown the symbols 10:1
        if s <= 40:
            comp_px = [
                ((c["x_m"] + MARGIN_M) * PX_PER_M, (c["y_m"] + MARGIN_M) * PX_PER_M) for c in comps
            ]
            # targeted hard negatives moved to the template (tag blob)
            # after they collapsed recall via class imbalance; random bg
            # only here.
            made = 0
            tries = 0
            while made < bg_per_sheet and tries < 400:
                tries += 1
                bx, by = rng.uniform(100, W - 100), rng.uniform(100, H - 100)
                if min(math.hypot(bx - qx, by - qy) for qx, qy in comp_px) < 60:
                    continue
                size_cls = MECH_CLASSES[rng.integers(len(MECH_CLASSES))]
                X.append(detection_crop(gray, bx, by, size_cls))
                y.append(len(MECH_CLASSES))  # background index
                made += 1
    X = np.stack(X)
    y = np.asarray(y, dtype=np.int64)
    perm = rng.permutation(len(y))
    classes = MECH_CLASSES + ["background"]
    return X[perm], y[perm], classes


if __name__ == "__main__":
    import sys as _s

    seed = int(_s.argv[1]) if len(_s.argv) > 1 else 11
    pp, jp = save_mech_sheet(seed, Path(__file__).parent / "out")
    print("wrote", pp, jp)
