"""Compositional synthetic floor-plan sheet generator.

Procedurally lays out a complete floor plan -- rectangular or L-shaped
footprint, guillotine-subdivided into 4-8 rooms, doors/windows placed in
walls with carved gaps, room name+number labels, and a window/door schedule
table drawn on the same sheet -- rendered at drawing-like resolution.

Output per sheet: the sheet PNG plus perfect ground-truth JSON:
  rooms:    name, number, polygon (m and px), area_m2
  symbols:  type, schedule tag, bbox_px, drawing type
  labels:   room-label text + bbox_px (for the labeling stage)
  schedule: tag -> category + dimensions (m)
  expected: floor/window/door area totals + per-tag counts

Door/window glyphs reuse ``synth.symbols`` glyph functions so the sheet
distribution matches the classifier training distribution.

Coordinate frames: layout in meters, y growing downward (matches image
coords). PX_PER_M converts to sheet pixels.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import symbols as sym_mod
from .symbols import glyph_double_door, glyph_single_door, glyph_window_plan

PX_PER_M = 50.0
WALL_T_M = 0.30
MARGIN_M = 2.0
MIN_ROOM_M = 3.2

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

ROOM_NAMES = [
    "OPEN OFFICE",
    "CONF",
    "LOBBY",
    "RESTROOM",
    "KITCHEN",
    "STORAGE",
    "CORRIDOR",
    "MECH",
    "ELEC",
    "BREAK ROOM",
    "RECEPTION",
    "FILE",
]

# Fixed schedule for every synthetic sheet (realistic commercial dims).
SCHEDULE = [
    {"tag": "A", "category": "window", "width_m": 1.2, "height_m": 1.5},
    {"tag": "B", "category": "window", "width_m": 1.8, "height_m": 1.5},
    {"tag": "D1", "category": "door", "width_m": 0.9, "height_m": 2.1},
    {"tag": "D2", "category": "door", "width_m": 1.6, "height_m": 2.1},
]
SCHED_BY_TAG = {s["tag"]: s for s in SCHEDULE}


def _font(size: int):
    return ImageFont.truetype(FONT_PATH, size)


# ---------------------------------------------------------------------------
# Layout: footprint + guillotine room subdivision (meters, y-down)
# ---------------------------------------------------------------------------


def _subdivide(rect, target: int, rng, min_size: float = MIN_ROOM_M):
    """Guillotine-split rect (x0,y0,x1,y1) into ~target room rects."""
    rooms = [rect]
    guard = 0
    while len(rooms) < target and guard < 60:
        guard += 1
        cands = [r for r in rooms if max(r[2] - r[0], r[3] - r[1]) >= 2 * min_size]
        if not cands:
            break
        r = cands[rng.integers(len(cands))]
        rooms.remove(r)
        x0, y0, x1, y1 = r
        w, h = x1 - x0, y1 - y0
        if w >= h:
            cut = rng.uniform(x0 + min_size, x1 - min_size)
            rooms += [(x0, y0, cut, y1), (cut, y0, x1, y1)]
        else:
            cut = rng.uniform(y0 + min_size, y1 - min_size)
            rooms += [(x0, y0, x1, cut), (x0, cut, x1, y1)]
    return rooms


def _layout_footprint(rng):
    """Return (footprint_rects, rooms) in meters."""
    if rng.random() < 0.65:  # rectangular
        W = rng.uniform(18, 26)
        D = rng.uniform(12, 18)
        rects = [(0, 0, W, D)]
    else:  # L-shape: main + leg
        W = rng.uniform(18, 24)
        D = rng.uniform(10, 14)
        W2 = rng.uniform(6, 9)
        D2 = rng.uniform(6, 10)
        lx = 0.0 if rng.random() < 0.5 else W - W2
        rects = [(0, 0, W, D), (lx, D, lx + W2, D + D2)]
    target = int(rng.integers(4, 9))
    rooms = []
    total_area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
    for r in rects:
        area = (r[2] - r[0]) * (r[3] - r[1])
        t = max(2, round(target * area / total_area))
        rooms += _subdivide(r, t, rng)
    return rects, rooms


def _rect_edges(r):
    x0, y0, x1, y1 = r
    return [
        ((x0, y0), (x1, y0)),  # top
        ((x1, y0), (x1, y1)),  # right
        ((x1, y1), (x0, y1)),  # bottom
        ((x0, y1), (x0, y0)),
    ]  # left


def _point_in_rooms(px, py, rooms, eps=1e-6):
    for x0, y0, x1, y1 in rooms:
        if x0 - eps <= px <= x1 + eps and y0 - eps <= py <= y1 + eps:
            return True
    return False


def _classify_edges(rooms):
    """Per room-rect edge: ('interior'|'exterior', line_key, s_range, rooms)."""
    out = []
    for ri, r in enumerate(rooms):
        for ei, ((ax, ay), (bx, by)) in enumerate(_rect_edges(r)):
            mx, my = (ax + bx) / 2, (ay + by) / 2
            # outward normal for y-down clockwise rect: top(0,-1) right(1,0)
            # bottom(0,1) left(-1,0)
            n = [(0, -1), (1, 0), (0, 1), (-1, 0)][ei]
            tx, ty = mx + n[0] * 0.05, my + n[1] * 0.05
            kind = "exterior" if not _point_in_rooms(tx, ty, rooms) else "interior"
            if abs(ay - by) < 1e-9:
                key = ("h", round(ay, 6))
                s_range = (min(ax, bx), max(ax, bx))
            else:
                key = ("v", round(ax, 6))
                s_range = (min(ay, by), max(ay, by))
            out.append(
                {
                    "room": ri,
                    "edge": ei,
                    "kind": kind,
                    "key": key,
                    "s_range": s_range,
                    "a": (ax, ay),
                    "b": (bx, by),
                    "normal": n,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Opening placement (doors/windows) with conflict tracking on wall lines
# ---------------------------------------------------------------------------


class OpeningPlacer:
    """Tracks occupied intervals per wall line ("h"/"v", offset) in meters."""

    def __init__(self):
        self.used: dict = {}  # key -> list of (s0, s1)

    def try_place_rng(self, key, s_lo, s_hi, w, rng, margin=0.4):
        lo = s_lo + w / 2 + margin
        hi = s_hi - w / 2 - margin
        if hi <= lo:
            return None
        occ = self.used.get(key, [])
        for _ in range(40):
            c = float(rng.uniform(lo, hi))
            if all(c + w / 2 + margin <= o0 or c - w / 2 - margin >= o1 for (o0, o1) in occ):
                occ.append((c - w / 2, c + w / 2))
                self.used[key] = occ
                return c
        return None


# ---------------------------------------------------------------------------
# Sheet rendering
# ---------------------------------------------------------------------------


def _draw_double_door_glyph(d, cpx, cpy, w_px, orient, side, lw):
    """Double door: two leaves swinging outward, mirroring the training
    glyph (synth.symbols.glyph_double_door): left/top leaf swings away
    from center on one side, right/bottom leaf on the other."""
    a = math.radians(58)
    L = w_px / 2
    if orient == "h":
        for sgn, hx in ((-1, cpx - w_px / 2), (1, cpx + w_px / 2)):
            ex, ey = hx + sgn * L * math.cos(a), cpy + side * L * math.sin(a)
            d.line([hx, cpy, ex, ey], fill=0, width=lw)
            if side > 0:
                start, end = (0, 58) if sgn > 0 else (122, 180)
            else:
                start, end = (302, 360) if sgn > 0 else (180, 238)
            d.arc([hx - L, cpy - L, hx + L, cpy + L], start=start, end=end, fill=0, width=lw)
    else:
        for sgn, hy in ((-1, cpy - w_px / 2), (1, cpy + w_px / 2)):
            ex, ey = cpx + side * L * math.sin(a), hy + sgn * L * math.cos(a)
            d.line([cpx, hy, ex, ey], fill=0, width=lw)
            if side > 0:
                start, end = (270, 328) if sgn < 0 else (32, 90)
            else:
                start, end = (212, 270) if sgn < 0 else (90, 148)
            d.arc([cpx - L, hy - L, cpx + L, hy + L], start=start, end=end, fill=0, width=lw)


def _draw_door_glyph(d, hinge_px, orient, side, L_px, lw):
    """Leaf + swing arc. orient 'h'/'v'; side +1/-1 = room side along the
    wall normal (for 'h': +1 is +y; for 'v': +1 is +x)."""
    hx, hy = hinge_px
    a = math.radians(58)
    if orient == "h":
        ex, ey = hx + L_px * math.cos(a), hy + side * L_px * math.sin(a)
        d.line([hx, hy, ex, ey], fill=0, width=lw)
        start, end = (0, 58) if side > 0 else (302, 360)
        d.arc([hx - L_px, hy - L_px, hx + L_px, hy + L_px], start=start, end=end, fill=0, width=lw)
    else:
        ex, ey = hx + side * L_px * math.sin(a), hy + L_px * math.cos(a)
        d.line([hx, hy, ex, ey], fill=0, width=lw)
        start, end = (32, 90) if side > 0 else (90, 148)
        d.arc([hx - L_px, hy - L_px, hx + L_px, hy + L_px], start=start, end=end, fill=0, width=lw)
    return (ex, ey)


def _draw_window_glyph(d, c_px, w_px, wt_px, lw, orient):
    cx, cy = c_px
    if orient == "h":
        for yy in (cy - wt_px / 2, cy, cy + wt_px / 2):
            d.line([cx - w_px / 2, yy, cx + w_px / 2, yy], fill=0, width=2)
        for xx in (cx - w_px / 2, cx + w_px / 2):
            d.line([xx, cy - wt_px / 2, xx, cy + wt_px / 2], fill=0, width=lw)
    else:
        for xx in (cx - wt_px / 2, cx, cx + wt_px / 2):
            d.line([xx, cy - w_px / 2, xx, cy + w_px / 2], fill=0, width=2)
        for yy in (cy - w_px / 2, cy + w_px / 2):
            d.line([cx - wt_px / 2, yy, cx + wt_px / 2, yy], fill=0, width=lw)


def generate_sheet(seed: int):
    """Render one synthetic sheet. Returns (image uint8 (H,W), gt dict)."""
    rng = np.random.default_rng(seed)
    S = PX_PER_M
    wt_px = int(round(WALL_T_M * S))
    rects, rooms = _layout_footprint(rng)
    edges = _classify_edges(rooms)
    placer = OpeningPlacer()

    # ---- plan pixel frame -------------------------------------------------
    fx1 = max(r[2] for r in rects)
    fy1 = max(r[3] for r in rects)
    plan_w_px = int(round((fx1 + 2 * MARGIN_M) * S))
    plan_h_px = int(round((fy1 + 2 * MARGIN_M) * S))
    ox, oy = int(MARGIN_M * S) + 40, 150  # plan origin in sheet

    def X(m):
        return ox + m * S

    def Y(m):
        return oy + m * S

    sheet_w = plan_w_px + 80 + 720
    sheet_h = oy + plan_h_px + 80
    img = Image.new("L", (sheet_w, sheet_h), 255)
    d = ImageDraw.Draw(img)

    # ---- walls --------------------------------------------------------------
    for r in rooms:
        for (ax, ay), (bx, by) in _rect_edges(r):
            x0, y0, x1, y1 = X(ax), Y(ay), X(bx), Y(by)
            if abs(y0 - y1) < 1e-9:  # horizontal
                d.rectangle([min(x0, x1), y0 - wt_px / 2, max(x0, x1), y0 + wt_px / 2], fill=0)
            else:  # vertical
                d.rectangle([x0 - wt_px / 2, min(y0, y1), x0 + wt_px / 2, max(y0, y1)], fill=0)

    symbols = []
    int_edges = [e for e in edges if e["kind"] == "interior"]
    ext_edges = [
        e
        for e in edges
        if e["kind"] == "exterior" and abs(e["s_range"][1] - e["s_range"][0]) >= 2.0
    ]
    rng.shuffle(int_edges)
    rng.shuffle(ext_edges)

    # ---- symbols reuse the training glyphs --------------------------------
    # Sheet doors/windows are rendered by calling the SAME glyph functions
    # used for training crops (synth.symbols), on a temp canvas sized
    # proportionally (T = 1.25 * glyph width), oriented to the wall, then
    # pasted onto the sheet. The crop distribution therefore matches the
    # training distribution by construction (up to train-time augmentation).

    def render_and_paste(glyph_fn, w_px, lw, cpx, cpy, orient, side, kind):
        T = int(round(w_px * 128 / 84))  # training w/CANVAS ratio
        old_canvas = sym_mod.CANVAS
        sym_mod.CANVAS = T
        try:
            tmp = Image.new("L", (T, T), 255)
            td = ImageDraw.Draw(tmp)
            glyph_fn(td, T / 2, T / 2, w_px, lw, rng=None)
        finally:
            sym_mod.CANVAS = old_canvas
        if kind == "door_single":
            if orient == "h":
                if side < 0:
                    tmp = tmp.transpose(Image.FLIP_TOP_BOTTOM)
            elif side > 0:
                tmp = tmp.transpose(Image.ROTATE_90)
            else:
                tmp = tmp.transpose(Image.ROTATE_270)
        elif orient == "v":  # double door / window: symmetric
            tmp = tmp.transpose(Image.ROTATE_90)
        Wp, Hp = img.size
        x0, y0 = int(round(cpx - T / 2)), int(round(cpy - T / 2))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = T - max(0, x0 + T - Wp), T - max(0, y0 + T - Hp)
        if sx1 > sx0 and sy1 > sy0:
            img.paste(tmp.crop((sx0, sy0, sx1, sy1)), (x0 + sx0, y0 + sy0))
        return [cpx - T / 2, cpy - T / 2, cpx + T / 2, cpy + T / 2]

    def draw_door_symbol(e, c_m, tag, rng, swing_side):
        w_px = SCHED_BY_TAG[tag]["width_m"] * S
        orient = "h" if e["key"][0] == "h" else "v"
        cpx, cpy = (X(c_m), Y(e["a"][1])) if orient == "h" else (X(e["a"][0]), Y(c_m))
        double = tag == "D2"
        glyph = glyph_double_door if double else glyph_single_door
        kind = "door_double" if double else "door_single"
        bbox = render_and_paste(glyph, w_px, 4, cpx, cpy, orient, swing_side, kind)
        return {
            "type": ("Double Swing Door" if double else "Single Swing Door"),
            "tag": tag,
            "bbox_px": bbox,
            "drawing": "floor_plan",
        }

    def draw_window_symbol(e, c_m, tag, rng):
        w_px = SCHED_BY_TAG[tag]["width_m"] * S
        orient = "h" if e["key"][0] == "h" else "v"
        cpx, cpy = (X(c_m), Y(e["a"][1])) if orient == "h" else (X(e["a"][0]), Y(c_m))
        bbox = render_and_paste(glyph_window_plan, w_px, 4, cpx, cpy, orient, 1, "window")
        return {"type": "Window", "tag": tag, "bbox_px": bbox, "drawing": "floor_plan"}

    # ---- doors ---------------------------------------------------------------
    n_doors = int(rng.integers(3, 7))
    placed_doors = 0
    for e in int_edges:
        if placed_doors >= n_doors:
            break
        tag = "D2" if rng.random() < 0.25 else "D1"
        w_m = SCHED_BY_TAG[tag]["width_m"]
        c = placer.try_place_rng(e["key"], *e["s_range"], w_m * 1.6, rng)
        if c is None:
            continue
        side = 1 if rng.random() < 0.5 else -1  # swing into either room
        symbols.append(draw_door_symbol(e, c, tag, rng, side))
        placed_doors += 1
    # one exterior entrance door
    for e in ext_edges:
        tag = "D2" if rng.random() < 0.5 else "D1"
        w_m = SCHED_BY_TAG[tag]["width_m"]
        c = placer.try_place_rng(e["key"], *e["s_range"], w_m * 1.6, rng)
        if c is None:
            continue
        orient = "h" if e["key"][0] == "h" else "v"
        nx, ny = e["normal"]
        side = -ny if orient == "h" else -nx  # swing inward
        side = 1 if side > 0 else -1
        symbols.append(draw_door_symbol(e, c, tag, rng, side))
        break

    # ---- windows (exterior walls only) ----------------------------------------
    n_win = int(rng.integers(4, 9))
    placed_win = 0
    for e in ext_edges:
        if placed_win >= n_win:
            break
        tag = "A" if rng.random() < 0.6 else "B"
        w_m = SCHED_BY_TAG[tag]["width_m"]
        c = placer.try_place_rng(e["key"], *e["s_range"], w_m * 1.6, rng, margin=0.6)
        if c is None:
            continue
        symbols.append(draw_window_symbol(e, c, tag, rng))
        placed_win += 1

    # ---- room labels ------------------------------------------------------------
    labels = []
    gt_rooms = []
    for i, r in enumerate(rooms):
        x0, y0, x1, y1 = r
        name = ROOM_NAMES[i % len(ROOM_NAMES)]
        number = str(101 + i)
        text = f"{name} {number}"
        fs = 44
        fnt = _font(fs)
        bb = d.textbbox((0, 0), text, font=fnt)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        rw_px = (x1 - x0) * S
        if tw > rw_px * 0.82 and tw > 0:
            fs = max(18, int(fs * rw_px * 0.82 / tw))
            fnt = _font(fs)
            bb = d.textbbox((0, 0), text, font=fnt)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
        cxp = X((x0 + x1) / 2) - tw / 2
        cyp = Y((y0 + y1) / 2) - th / 2
        d.text((cxp, cyp), text, fill=0, font=fnt)
        labels.append({"text": text, "bbox_px": [cxp, cyp, cxp + tw, cyp + th], "room_idx": i})
        poly_m = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        gt_rooms.append(
            {
                "name": name,
                "number": number,
                "polygon_m": poly_m,
                "polygon_px": [[X(px), Y(py)] for px, py in poly_m],
                "area_m2": (x1 - x0) * (y1 - y0),
            }
        )

    # ---- schedule table -----------------------------------------------------------
    tx = ox + plan_w_px + 70
    ty = 150
    d.text((tx, 60), f"SYNTHETIC PLAN -- sheet_{seed:03d}", fill=0, font=_font(30))

    def draw_table(x, y, title, rows):
        cw = [90, 150, 150]
        rh = 42
        fnt = _font(26)
        d.text((x, y), title, fill=0, font=_font(30))
        y += 44
        header = ["TAG", "WIDTH", "HEIGHT"]
        for ci, htxt in enumerate(header):
            x0 = x + sum(cw[:ci])
            d.rectangle([x0, y, x0 + cw[ci], y + rh], outline=0, width=2)
            d.text((x0 + 10, y + 6), htxt, fill=0, font=fnt)
        y += rh
        for tag, wm, hm in rows:
            cells = [tag, f"{int(wm * 1000)}", f"{int(hm * 1000)}"]
            for ci, txt in enumerate(cells):
                x0 = x + sum(cw[:ci])
                d.rectangle([x0, y, x0 + cw[ci], y + rh], outline=0, width=2)
                d.text((x0 + 10, y + 6), txt, fill=0, font=fnt)
            y += rh
        return y

    y = draw_table(tx, ty, "WINDOW SCHEDULE", [("A", 1.2, 1.5), ("B", 1.8, 1.5)])
    draw_table(tx, y + 40, "DOOR SCHEDULE", [("D1", 0.9, 2.1), ("D2", 1.6, 2.1)])

    # ---- expected takeoff ----------------------------------------------------------
    exp = {
        "floor_area_m2": sum(r["area_m2"] for r in gt_rooms),
        "window_area_m2": 0.0,
        "door_area_m2": 0.0,
        "counts": {},
    }
    for s in symbols:
        se = SCHED_BY_TAG[s["tag"]]
        a = se["width_m"] * se["height_m"]
        cat = "window_area_m2" if se["category"] == "window" else "door_area_m2"
        exp[cat] += a
        exp["counts"][s["tag"]] = exp["counts"].get(s["tag"], 0) + 1

    gt = {
        "sheet_id": f"sheet_{seed:03d}",
        "seed": seed,
        "scale_px_per_m": S,
        "sheet_size_px": [sheet_w, sheet_h],
        "footprint": "L" if len(rects) == 2 else "rect",
        "rooms": gt_rooms,
        "symbols": symbols,
        "labels": labels,
        "schedule": SCHEDULE,
        "expected": exp,
    }
    return np.asarray(img), gt


def save_sheet(seed: int, outdir: str | Path):
    """Render one sheet: PNG + GT JSON. Returns (png_path, json_path)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    img, gt = generate_sheet(seed)
    png = outdir / f"{gt['sheet_id']}.png"
    js = outdir / f"{gt['sheet_id']}.gt.json"
    Image.fromarray(img.astype(np.uint8)).save(png)
    with open(js, "w") as f:
        json.dump(gt, f, indent=1)
    return png, js
