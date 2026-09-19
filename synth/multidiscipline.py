"""Synthetic multi-discipline building generator.

Generates ONE building as FIVE coordinated sheets sharing a single room
layout and perfect cross-sheet ground truth:

  arch      architectural floor plan: rooms + names/numbers, column grids
            (A-D / 1-3), windows on exterior walls
  lighting  lighting plan: same rooms, fixture layout per room type
  mech      mechanical plan: same rooms, VAV zones as vertical strips,
            Manhattan duct runs, diffusers, temperature sensors
  elev_grid south elevation WITH grid bubbles
  elev_nogrid south elevation WITHOUT grids (exercises the geometric
            fallback registration path)

Layout (meters, y-down): rectangular footprint W x D; zones are vertical
strips; rooms tile each strip (or, with open_office_span, one full-width
OPEN OFFICE band spans two zones -- exercising many-to-many zone<->space).

Ground truth links: fixture->room, sensor->room, diffuser->room,
diffuser->zone, sensor->zone, window->room (south facade), zone->rooms.

All detector-style outputs are in SHEET PIXEL coordinates -- the linker
must register them into canonical meters itself. Nothing is pre-linked
by id across sheets (except GT, kept separate for scoring).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import sys as _sys
sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in _sys.path:
    _sys.path.insert(0, sys_path)

from synth.sheets import ROOM_NAMES, _font, SCHEDULE, SCHED_BY_TAG  # noqa: E402
from synth.lighting import (LIGHTING_GLYPHS, LIGHTING_SCHEDULE,  # noqa: E402
                            CLASS_TO_TAG)
from synth.lighting_sheets import ROOM_FIXTURE_PLAN, DEFAULT_PLAN  # noqa: E402
from synth.mech import _draw_vav, _draw_diffuser, _draw_sensor  # noqa: E402

PLAN_PX_PER_M = 50.0
ELEV_PX_PER_M = 40.0          # deliberately different: exercises scaling
WALL_H_M = 3.0
PARAPET_M = 0.6
SILL_M = 0.9                  # window sill height (synthetic convention)
MIN_ROOM_M = 3.2

FIXTURE_WIDTH_M = {
    "Troffer 2x4": 1.2, "Troffer 2x2": 0.6, "Downlight": 0.3,
    "Pendant": 0.4, "Wall Sconce": 0.3, "Exit Sign": 0.4,
}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _subdivide_strip(x0, x1, y0, y1, n, rng):
    """Split a vertical strip into n rooms by horizontal cuts."""
    n = max(1, min(n, int((y1 - y0) // MIN_ROOM_M)))
    if n == 1:
        return [(x0, y0, x1, y1)]
    cuts = sorted(rng.uniform(y0 + MIN_ROOM_M, y1 - MIN_ROOM_M, n - 1))
    bounds = [y0] + list(cuts) + [y1]
    # enforce min size (fall back to even splits)
    if any(bounds[k + 1] - bounds[k] < MIN_ROOM_M - 1e-6
           for k in range(n)):
        bounds = [y0 + k * (y1 - y0) / n for k in range(n + 1)]
    return [(x0, bounds[k], x1, bounds[k + 1]) for k in range(n)]


def _layout(rng, open_office_span: bool):
    W = float(rng.uniform(18, 26))
    D = float(rng.uniform(12, 18))
    n_zones = 2 if open_office_span else int(rng.integers(1, 4))
    strips = [(i * W / n_zones, (i + 1) * W / n_zones)
              for i in range(n_zones)]

    rooms = []  # (x0,y0,x1,y1, zone_idx or None)
    if open_office_span:
        yb0, yb1 = D * 0.35, D * 0.65
        rooms.append((0.0, yb0, W, yb1, None))  # open office, spans zones
        for zi, (xs0, xs1) in enumerate(strips):
            for r in _subdivide_strip(xs0, xs1, 0.0, yb0,
                                      int(rng.integers(2, 4)), rng):
                rooms.append((*r, zi))
            for r in _subdivide_strip(xs0, xs1, yb1, D,
                                      int(rng.integers(2, 4)), rng):
                rooms.append((*r, zi))
    else:
        for zi, (xs0, xs1) in enumerate(strips):
            for r in _subdivide_strip(xs0, xs1, 0.0, D,
                                      int(rng.integers(2, 4)), rng):
                rooms.append((*r, zi))

    # name + number: open office keeps its name; others shuffled
    names = list(ROOM_NAMES)
    rng.shuffle(names)
    named = []
    ni = 0
    for (x0, y0, x1, y1, zi) in rooms:
        if zi is None:
            name = "OPEN OFFICE"
        else:
            name = names[ni % len(names)]
            ni += 1
        named.append({"rect": (x0, y0, x1, y1), "zone": zi, "name": name})
    # numbers in reading order
    named.sort(key=lambda r: (r["rect"][1], r["rect"][0]))
    for i, r in enumerate(named):
        r["number"] = str(101 + i)
    return W, D, n_zones, strips, named


def _grids(W, D):
    return ({"A": 0.0, "B": W / 3, "C": 2 * W / 3, "D": W},
            {"1": 0.0, "2": D / 2, "3": D})


# ---------------------------------------------------------------------------
# Sheet canvases
# ---------------------------------------------------------------------------

class _Sheet:
    def __init__(self, w_px, h_px):
        self.img = Image.new("L", (w_px, h_px), 255)
        self.d = ImageDraw.Draw(self.img)

    def finalize(self):
        return np.asarray(self.img)


def _paste_lighting_glyph(img, glyph_fn, w_px, cpx, cpy, rng):
    tmp = Image.new("L", (128, 128), 255)
    td = ImageDraw.Draw(tmp)
    lw = int(rng.choice([3, 4, 5]))
    glyph_fn(td, 64, 64, 84.0, lw, rng=rng)
    if rng.random() < 0.5:
        tmp = tmp.transpose(rng.choice([Image.FLIP_LEFT_RIGHT,
                                        Image.ROTATE_90, Image.ROTATE_270]))
    T = max(8, int(round(w_px * 128 / 84)))
    tmp = tmp.resize((T, T), Image.LANCZOS)
    x0, y0 = int(round(cpx - T / 2)), int(round(cpy - T / 2))
    Wp, Hp = img.size
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = T - max(0, x0 + T - Wp), T - max(0, y0 + T - Hp)
    if sx1 > sx0 and sy1 > sy0:
        img.paste(tmp.crop((sx0, sy0, sx1, sy1)), (x0 + sx0, y0 + sy0))


# ---------------------------------------------------------------------------
# Arch plan
# ---------------------------------------------------------------------------

def _render_arch(rooms, grids_v, grids_h, south_windows, W, D, rng, seed):
    ppm = PLAN_PX_PER_M
    ox, oy = 120, 150
    sw = int(ox + W * ppm + 90)
    sh = int(oy + D * ppm + 90)
    sh_ = _Sheet(sw, sh)
    d = sh_.d
    X = lambda m: ox + m * ppm
    Y = lambda m: oy + m * ppm
    wt = 10

    # grid lines (thin, extend past footprint) + bubbles
    for lab, gx in grids_v.items():
        x = X(gx)
        d.line([x, oy - 110, x, Y(D) + 30], fill=120, width=1)
        d.ellipse([x - 22, oy - 110, x + 22, oy - 66], outline=0, width=2)
        f = _font(24)
        bb = d.textbbox((0, 0), lab, font=f)
        d.text((x - (bb[2] - bb[0]) / 2, oy - 108), lab, fill=0, font=f)
    for lab, gy in grids_h.items():
        y = Y(gy)
        d.line([ox - 90, y, X(W) + 30, y], fill=120, width=1)
        d.ellipse([ox - 90, y - 22, ox - 46, y + 22], outline=0, width=2)
        f = _font(24)
        bb = d.textbbox((0, 0), lab, font=f)
        d.text((ox - 88, y - (bb[3] - bb[1]) / 2), lab, fill=0, font=f)

    # walls
    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        d.rectangle([X(x0), Y(y0), X(x1), Y(y1)], outline=0, width=wt)

    # windows on south facade: gap in wall + triple line marker
    for w in south_windows:
        x0p, x1p, yp = X(w["s0_m"]), X(w["s1_m"]), Y(D)
        d.rectangle([x0p, yp - wt / 2 - 2, x1p, yp + wt / 2 + 2], fill=255)
        for yy in (yp - 4, yp, yp + 4):
            d.line([x0p, yy, x1p, yy], fill=0, width=2)

    # room labels
    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        text = f"{r['name']} {r['number']}"
        f = _font(40)
        bb = d.textbbox((0, 0), text, font=f)
        tw = bb[2] - bb[0]
        if tw > (x1 - x0) * ppm * 0.85:
            f = _font(26)
            bb = d.textbbox((0, 0), text, font=f)
            tw = bb[2] - bb[0]
        d.text((X((x0 + x1) / 2) - tw / 2, Y((y0 + y1) / 2) - 20),
               text, fill=0, font=f)

    d.text((ox, 40), f"ARCH PLAN A101 -- bldg_{seed:03d}", fill=0,
           font=_font(30))
    return sh_.finalize(), (ox, oy)


# ---------------------------------------------------------------------------
# Lighting plan
# ---------------------------------------------------------------------------

def _render_lighting(rooms, W, D, rng):
    ppm = PLAN_PX_PER_M
    ox, oy = 90, 110
    sw = int(ox + W * ppm + 90)
    sh = int(oy + D * ppm + 90)
    sh_ = _Sheet(sw, sh)
    d = sh_.d
    X = lambda m: ox + m * ppm
    Y = lambda m: oy + m * ppm

    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        d.rectangle([X(x0), Y(y0), X(x1), Y(y1)], outline=0, width=4)

    fixtures = []
    fi = 0
    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        fclass, m2_per, mn, mx = ROOM_FIXTURE_PLAN.get(r["name"], DEFAULT_PLAN)
        area = (x1 - x0) * (y1 - y0)
        n = min(mx, max(mn, int(round(area / m2_per))))
        cols = max(1, int(round(math.sqrt(n * (x1 - x0) / max(y1 - y0, 1e-6)))))
        rows = max(1, int(math.ceil(n / cols)))
        k = 0
        for ci in range(cols):
            for ri_ in range(rows):
                if k >= n:
                    break
                fx = x0 + (x1 - x0) * (ci + 0.5) / cols
                fy = y0 + (y1 - y0) * (ri_ + 0.5) / rows
                fi += 1
                fid = f"F{fi}"
                tag = CLASS_TO_TAG[fclass]
                fixtures.append({"id": fid, "class": fclass, "tag": tag,
                                 "x_m": fx, "y_m": fy,
                                 "x_px": X(fx), "y_px": Y(fy),
                                 "room_number": r["number"]})
                _paste_lighting_glyph(sh_.img, LIGHTING_GLYPHS[fclass],
                                      FIXTURE_WIDTH_M[fclass] * ppm,
                                      X(fx), Y(fy), rng)
                k += 1
    d.text((ox, 40), "LIGHTING PLAN E101", fill=0, font=_font(30))
    return sh_.finalize(), (ox, oy), fixtures


# ---------------------------------------------------------------------------
# Mechanical plan
# ---------------------------------------------------------------------------

def _render_mech(rooms, strips, n_zones, W, D, rng):
    ppm = PLAN_PX_PER_M
    ox, oy = 150, 100
    sw = int(ox + W * ppm + 90)
    sh = int(oy + D * ppm + 90)
    sh_ = _Sheet(sw, sh)
    d = sh_.d
    X = lambda m: ox + m * ppm
    Y = lambda m: oy + m * ppm

    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        d.rectangle([X(x0), Y(y0), X(x1), Y(y1)], outline=0, width=3)

    components = []   # {id,type,x_m,y_m,x_px,y_px,tag,room_number}
    zones = []
    di = si = 0
    duct_w = 0.40 * ppm

    def bar(x0m, y0m, x1m, y1m):
        d.rectangle([X(min(x0m, x1m)) - duct_w / 2,
                     Y(min(y0m, y1m)) - duct_w / 2,
                     X(max(x0m, x1m)) + duct_w / 2,
                     Y(max(y0m, y1m)) + duct_w / 2], fill=40)

    for zi in range(n_zones):
        xs0, xs1 = strips[zi]
        vav_x, vav_y = (xs0 + xs1) / 2, D / 2
        _draw_vav(d, X(vav_x), Y(vav_y))
        vav_id = f"VAV-{zi + 1}"
        components.append({"id": vav_id, "type": "vav", "x_m": vav_x,
                           "y_m": vav_y, "x_px": X(vav_x), "y_px": Y(vav_y),
                           "tag": vav_id, "room_number": None})
        z_dif, z_sen, z_rooms = [], [], []
        duct_len = 0.0
        # rooms served: strip rooms + the spanning open office (both zones)
        for r in rooms:
            x0, y0, x1, y1 = r["rect"]
            serves = (r["zone"] == zi) or (r["zone"] is None)
            if not serves:
                continue
            # diffuser(s): office gets one per zone, in that zone's half
            if r["zone"] is None:
                fx = (xs0 + xs1) / 2
                fys = [(y0 + y1) / 2]
            else:
                fx, fys = (x0 + x1) / 2, [(y0 + y1) / 2]
            for fy in fys:
                di += 1
                did = f"D{di}"
                _draw_diffuser(d, X(fx), Y(fy))
                # Manhattan duct: horizontal at vav_y, then vertical drop
                bar(vav_x, vav_y, fx, vav_y)
                bar(fx, vav_y, fx, fy)
                duct_len += abs(fx - vav_x) + abs(fy - vav_y)
                components.append({"id": did, "type": "diffuser",
                                   "x_m": fx, "y_m": fy,
                                   "x_px": X(fx), "y_px": Y(fy),
                                   "tag": did, "room_number": r["number"]})
                z_dif.append({"id": did, "x_px": X(fx), "y_px": Y(fy)})
            si += 1
            sid = f"T{si}"
            sx, sy = x0 + 1.2, y0 + 1.2
            _draw_sensor(d, X(sx), Y(sy))
            components.append({"id": sid, "type": "sensor", "x_m": sx,
                               "y_m": sy, "x_px": X(sx), "y_px": Y(sy),
                               "tag": sid, "room_number": r["number"]})
            z_sen.append({"id": sid, "x_px": X(sx), "y_px": Y(sy)})
            if r["number"] not in z_rooms:
                z_rooms.append(r["number"])
        # vav room assignment
        for r in rooms:
            x0, y0, x1, y1 = r["rect"]
            if x0 <= vav_x <= x1 and y0 <= vav_y <= y1:
                components[-len(z_dif) - len(z_sen) - 1]["room_number"] = \
                    r["number"]
                break
        zones.append({
            "zone_id": f"Z{zi + 1}",
            "vav": {"id": vav_id, "x_px": X(vav_x), "y_px": Y(vav_y)},
            "diffusers": z_dif, "sensors": z_sen,
            "duct_length_m": round(duct_len, 2),
            "room_numbers": sorted(z_rooms),   # GT only; linker must not use
            "audit": [f"synthetic zone {zi + 1}: VAV-{zi + 1} serves "
                      f"{len(z_rooms)} rooms"],
        })
    d.text((ox, 40), "MECH PLAN M101", fill=0, font=_font(30))
    return sh_.finalize(), (ox, oy), components, zones


# ---------------------------------------------------------------------------
# Elevations (south facade)
# ---------------------------------------------------------------------------

def _render_elevation(south_windows, grids_v, W, with_grids: bool,
                      px_per_m: float = ELEV_PX_PER_M):
    epm = px_per_m
    u0 = 60
    wall_w_px = W * epm
    v_ground = 150 + (WALL_H_M + PARAPET_M) * epm
    sh = int(v_ground + 70)
    sw = int(u0 + wall_w_px + 80)
    sh_ = _Sheet(sw, sh)
    d = sh_.d
    U = lambda s: u0 + s * epm
    V = lambda z: v_ground - z * epm

    # ground line + wall
    d.line([u0 - 30, v_ground, u0 + wall_w_px + 30, v_ground], fill=0, width=3)
    d.rectangle([U(0), V(WALL_H_M + PARAPET_M), U(W), V(0)],
                outline=0, width=4)
    # parapet cap line
    d.line([U(0), V(WALL_H_M + PARAPET_M), U(W), V(WALL_H_M + PARAPET_M)],
           fill=0, width=4)

    if with_grids:
        for lab, gx in grids_v.items():
            u = U(gx)
            d.line([u, V(WALL_H_M + PARAPET_M) - 60, u,
                    V(WALL_H_M + PARAPET_M)], fill=120, width=1)
            d.ellipse([u - 20, V(WALL_H_M + PARAPET_M) - 104,
                       u + 20, V(WALL_H_M + PARAPET_M) - 64],
                      outline=0, width=2)
            f = _font(22)
            bb = d.textbbox((0, 0), lab, font=f)
            d.text((u - (bb[2] - bb[0]) / 2,
                    V(WALL_H_M + PARAPET_M) - 102), lab, fill=0, font=f)

    win_dets = []
    for i, w in enumerate(south_windows):
        se = SCHED_BY_TAG[w["tag"]]
        h = se["height_m"]
        d.rectangle([U(w["s0_m"]), V(SILL_M + h), U(w["s1_m"]), V(SILL_M)],
                    outline=0, width=3)
        d.line([U(w["s0_m"]), V(SILL_M + h / 2),
                U(w["s1_m"]), V(SILL_M + h / 2)], fill=0, width=1)
        # dimension line above the window (width in mm) -- thin clutter
        # the detector must ignore
        dy = V(SILL_M + h + 0.28)
        d.line([U(w["s0_m"]), dy, U(w["s1_m"]), dy], fill=60, width=1)
        d.line([U(w["s0_m"]), dy - 4, U(w["s0_m"]), dy + 4], fill=60,
               width=1)
        d.line([U(w["s1_m"]), dy - 4, U(w["s1_m"]), dy + 4], fill=60,
               width=1)
        f = _font(14)
        txt = str(int(round((w["s1_m"] - w["s0_m"]) * 1000)))
        bb = d.textbbox((0, 0), txt, font=f)
        d.text((U((w["s0_m"] + w["s1_m"]) / 2) - (bb[2] - bb[0]) / 2,
                dy - 22), txt, fill=60, font=f)
        wid = f"W{i + 1}"
        win_dets.append({"id": wid, "tag": w["tag"],
                         "u0_px": U(w["s0_m"]), "u1_px": U(w["s1_m"]),
                         "v_sill_px": V(SILL_M), "v_head_px": V(SILL_M + h)})

    bubbles = ([{"label": lab, "u_px": U(gx)}
                for lab, gx in grids_v.items()] if with_grids else [])
    title = ("SOUTH ELEVATION A201 (grids)" if with_grids
             else "SOUTH ELEVATION A202 (no grids)")
    d.text((u0, 30), title, fill=0, font=_font(28))
    return (sh_.finalize(),
            {"windows": win_dets, "bubbles": bubbles,
             "wall_u0_px": float(u0), "v_ground_px": float(v_ground),
             "px_per_m": epm})


# ---------------------------------------------------------------------------
# South-facade windows (plan side)
# ---------------------------------------------------------------------------

def _south_windows(rooms, D, rng):
    wins = []
    south_rooms = []
    for r in rooms:
        x0, y0, x1, y1 = r["rect"]
        if abs(y1 - D) > 1e-6:
            continue
        south_rooms.append(r)
        wdt = x1 - x0
        n = 2 if wdt >= 6.0 else 1
        for k in range(n):
            tag = "A" if rng.random() < 0.6 else "B"
            ww = SCHED_BY_TAG[tag]["width_m"]
            c = x0 + wdt * (k + 1) / (n + 1)
            c = min(max(c, x0 + ww / 2 + 0.5), x1 - ww / 2 - 0.5)
            wins.append({"tag": tag, "s0_m": c - ww / 2, "s1_m": c + ww / 2,
                         "room_number": r["number"]})
    # corner window: one extra window hugging a room edge near a wall end,
    # exercising near-boundary along-wall positions
    if south_rooms and rng.random() < 0.8:
        r = south_rooms[int(rng.integers(len(south_rooms)))]
        x0, y0, x1, y1 = r["rect"]
        tag = "A"
        ww = SCHED_BY_TAG[tag]["width_m"]
        edge_gap = float(rng.uniform(0.35, 0.8))
        if rng.random() < 0.5:
            s0 = x0 + edge_gap
        else:
            s0 = x1 - edge_gap - ww
        s1 = s0 + ww
        if not any(not (s1 <= w["s0_m"] or s0 >= w["s1_m"])
                   for w in wins if w["room_number"] == r["number"]):
            wins.append({"tag": tag, "s0_m": s0, "s1_m": s1,
                         "room_number": r["number"]})
    return wins


# ---------------------------------------------------------------------------
# Top-level generator
# ---------------------------------------------------------------------------

def generate_building(seed: int, open_office_span: bool = False) -> dict:
    """Generate one multi-discipline building. Returns the building dict
    (images as uint8 arrays + GT)."""
    rng = np.random.default_rng(seed)
    W, D, n_zones, strips, rooms = _layout(rng, open_office_span)
    grids_v, grids_h = _grids(W, D)
    south_windows = _south_windows(rooms, D, rng)

    gt_rooms = []
    for i, r in enumerate(rooms):
        x0, y0, x1, y1 = r["rect"]
        gt_rooms.append({
            "idx": i, "name": r["name"], "number": r["number"],
            "rect_m": [x0, y0, x1, y1],
            "polygon_m": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            "area_m2": round((x1 - x0) * (y1 - y0), 3),
            "zone": r["zone"],
        })

    arch_img, arch_origin = _render_arch(rooms, grids_v, grids_h,
                                        south_windows, W, D, rng, seed)
    light_img, light_origin, fixtures = _render_lighting(rooms, W, D, rng)
    mech_img, mech_origin, components, zones = _render_mech(
        rooms, strips, n_zones, W, D, rng)
    elev_g_img, elev_g_data = _render_elevation(south_windows, grids_v, W,
                                               True, px_per_m=40.0)
    # second elevation deliberately at a different scale (34 px/m):
    # dedup must merge observations in meters, not pixels
    elev_n_img, elev_n_data = _render_elevation(south_windows, grids_v, W,
                                               False, px_per_m=34.0)

    gt_links = {
        "fixture_room": {f["id"]: f["room_number"] for f in fixtures},
        "sensor_room": {c["id"]: c["room_number"] for c in components
                        if c["type"] == "sensor"},
        "diffuser_room": {c["id"]: c["room_number"] for c in components
                          if c["type"] == "diffuser"},
        "diffuser_zone": {},
        "sensor_zone": {},
        "window_room": {},
        "zone_rooms": {},
    }
    for z in zones:
        zid = z["zone_id"]
        gt_links["zone_rooms"][zid] = z["room_numbers"]
        for d_ in z["diffusers"]:
            # each diffuser is created in exactly one zone's loop
            gt_links["diffuser_zone"][d_["id"]] = zid
        for s_ in z["sensors"]:
            # sensor's zone = its generating zone (for a spanning room the
            # room itself is in two zones; sensor->room is the stable link)
            gt_links["sensor_zone"][s_["id"]] = zid
    for i, w in enumerate(south_windows):
        gt_links["window_room"][f"W{i + 1}"] = w["room_number"]

    def sheet_meta(sheet_id, discipline, origin, ppm):
        return {"sheet_id": sheet_id, "discipline": discipline,
                "revision": 1, "px_per_m": ppm,
                "origin_px": list(origin)}

    bldg = {
        "building_id": f"bldg_{seed:03d}",
        "seed": seed,
        "W_m": W, "D_m": D,
        "wall_height_m": WALL_H_M,
        "level_id": "L1",
        "open_office_span": open_office_span,
        "rooms": gt_rooms,
        "grids_v": grids_v, "grids_h": grids_h,
        "zones": zones,
        "components": components,
        "fixtures": fixtures,
        "south_windows": south_windows,
        "gt_links": gt_links,
        "window_schedule": SCHEDULE,
        "lighting_schedule": LIGHTING_SCHEDULE,
        "sheets": {
            "arch": {"image": arch_img,
                     "meta": sheet_meta("arch_A101", "arch_plan",
                                        arch_origin, PLAN_PX_PER_M)},
            "lighting": {"image": light_img,
                         "meta": sheet_meta("light_E101", "lighting_plan",
                                            light_origin, PLAN_PX_PER_M)},
            "mech": {"image": mech_img,
                     "meta": sheet_meta("mech_M101", "mech_plan",
                                        mech_origin, PLAN_PX_PER_M)},
            "elev_grid": {"image": elev_g_img,
                          "meta": {**sheet_meta("elev_A201", "elevation",
                                                (0, 0), ELEV_PX_PER_M),
                                   "facade": "south",
                                   "facade_ref_corner_m": [0.0, D],
                                   "facade_length_m": W},
                          "data": elev_g_data},
            "elev_nogrid": {"image": elev_n_img,
                            "meta": {**sheet_meta("elev_A202", "elevation",
                                                  (0, 0), ELEV_PX_PER_M),
                                     "facade": "south",
                                     "facade_ref_corner_m": [0.0, D],
                                     "facade_length_m": W},
                            "data": elev_n_data},
        },
    }
    return bldg


def save_building(bldg: dict, outdir: str | Path) -> Path:
    """Save sheet PNGs + GT JSON (images excluded from JSON)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    gt = {k: v for k, v in bldg.items() if k != "sheets"}
    sheets_meta = {}
    for key, sh in bldg["sheets"].items():
        png = outdir / f"{bldg['building_id']}_{key}.png"
        Image.fromarray(sh["image"].astype(np.uint8)).save(png)
        sm = {"png": png.name, "meta": sh["meta"]}
        if "data" in sh:
            sm["data"] = sh["data"]
        sheets_meta[key] = sm
    gt["sheets"] = sheets_meta
    jp = outdir / f"{bldg['building_id']}.gt.json"
    with open(jp, "w") as f:
        json.dump(gt, f, indent=1, default=_json_default)
    return jp


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    raise TypeError(f"not serializable: {type(o)}")
