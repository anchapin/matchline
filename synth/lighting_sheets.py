"""Synthetic lighting sheet generator for lighting-takeoff validation.

Lays out a floor plan (reusing synth.sheets' footprint subdivision), places
lighting fixtures per room according to a realistic lighting design
(offices -> 2x4 troffers, corridors/restrooms -> downlights, lobby ->
pendants, storage/mech -> 2x2 troffers, plus exit signs), draws a lighting
schedule table on the sheet, and emits perfect ground truth:

  rooms:    name, number, polygon_m/px, area_m2
  fixtures: type, schedule tag, bbox_px
  schedule: tag -> description, lamp_type, watts
  expected: total_w, per-room watts + LPD

Fixture glyphs are the SAME functions used for classifier training crops
(synth.lighting.LIGHTING_GLYPHS), pasted via the temp-canvas technique from
synth.sheets, so the sheet distribution matches the training distribution.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from synth.lighting import CLASS_TO_TAG, LIGHTING_GLYPHS, LIGHTING_SCHED_BY_TAG, LIGHTING_SCHEDULE
from synth.sheets import (
    MARGIN_M,
    PX_PER_M,
    ROOM_NAMES,
    WALL_T_M,
    _font,
    _layout_footprint,
    _rect_edges,
)

# fixture class -> nominal width in meters (plan size, for paste scale)
FIXTURE_WIDTH_M = {
    "Troffer 2x4": 1.2,
    "Troffer 2x2": 0.6,
    "Downlight": 0.3,
    "Pendant": 0.4,
    "Wall Sconce": 0.3,
    "Exit Sign": 0.4,
}

# room-name -> (fixture class, m2 per fixture, min, max)
ROOM_FIXTURE_PLAN = {
    "OPEN OFFICE": ("Troffer 2x4", 12.0, 2, 12),
    "CONF": ("Troffer 2x4", 10.0, 2, 8),
    "RECEPTION": ("Troffer 2x4", 12.0, 2, 8),
    "BREAK ROOM": ("Troffer 2x4", 12.0, 2, 6),
    "FILE": ("Troffer 2x4", 14.0, 1, 4),
    "CORRIDOR": ("Downlight", 8.0, 2, 10),
    "RESTROOM": ("Downlight", 6.0, 1, 4),
    "LOBBY": ("Pendant", 15.0, 2, 6),
    "STORAGE": ("Troffer 2x2", 15.0, 1, 4),
    "MECH": ("Troffer 2x2", 18.0, 1, 3),
    "ELEC": ("Troffer 2x2", 18.0, 1, 3),
    "KITCHEN": ("Troffer 2x2", 12.0, 1, 4),
}
DEFAULT_PLAN = ("Troffer 2x4", 12.0, 1, 6)


def _paste_glyph(img, glyph_fn, w_px, cpx, cpy, rng):
    """Render the glyph at full training scale (128px canvas, w=84) then
    downsample to the target paste size.

    This keeps relative stroke weights identical to the classifier training
    crops: drawing small glyphs directly with a fixed lw makes strokes far
    too thick relative to the glyph (a 15px downlight with lw=4 is a blob,
    while training sees a thin ring). Resampling preserves the distribution.
    """
    tmp = Image.new("L", (128, 128), 255)
    td = ImageDraw.Draw(tmp)
    lw = int(rng.choice([3, 4, 5]))
    glyph_fn(td, 64, 64, 84.0, lw, rng=rng)
    if rng.random() < 0.75:  # wall-mount orientations
        tmp = tmp.transpose(
            rng.choice(
                [Image.FLIP_LEFT_RIGHT, Image.FLIP_TOP_BOTTOM, Image.ROTATE_90, Image.ROTATE_270]
            )
        )
    T = max(10, int(round(w_px * 128 / 84)))
    small = tmp.resize((T, T), Image.LANCZOS)
    Wp, Hp = img.size
    x0, y0 = int(round(cpx - T / 2)), int(round(cpy - T / 2))
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = T - max(0, x0 + T - Wp), T - max(0, y0 + T - Hp)
    if sx1 > sx0 and sy1 > sy0:
        img.paste(small.crop((sx0, sy0, sx1, sy1)), (x0 + sx0, y0 + sy0))
    return [cpx - T / 2, cpy - T / 2, cpx + T / 2, cpy + T / 2]


def generate_lighting_sheet(seed: int):
    """Render one synthetic lighting sheet. Returns (image, gt dict)."""
    rng = np.random.default_rng(seed)
    S = PX_PER_M
    wt_px = int(round(WALL_T_M * S))
    rects, rooms = _layout_footprint(rng)
    fx1 = max(r[2] for r in rects)
    fy1 = max(r[3] for r in rects)
    plan_w_px = int(round((fx1 + 2 * MARGIN_M) * S))
    plan_h_px = int(round((fy1 + 2 * MARGIN_M) * S))
    ox, oy = int(MARGIN_M * S) + 40, 150

    def X(m):
        return ox + m * S

    def Y(m):
        return oy + m * S

    sheet_w = plan_w_px + 80 + 900
    sheet_h = oy + plan_h_px + 80
    img = Image.new("L", (sheet_w, sheet_h), 255)
    d = ImageDraw.Draw(img)

    # ---- walls ------------------------------------------------------------
    for r in rooms:
        for (ax, ay), (bx, by) in _rect_edges(r):
            x0, y0, x1, y1 = X(ax), Y(ay), X(bx), Y(by)
            if abs(y0 - y1) < 1e-9:
                d.rectangle([min(x0, x1), y0 - wt_px / 2, max(x0, x1), y0 + wt_px / 2], fill=0)
            else:
                d.rectangle([x0 - wt_px / 2, min(y0, y1), x0 + wt_px / 2, max(y0, y1)], fill=0)

    # ---- room labels first: fixture placement must avoid the text ------------
    # (label strokes inside a fixture crop corrupt it; sheets.py avoids this
    # by putting symbols on walls, but ceiling fixtures live mid-room).
    label_boxes = []
    for i, r in enumerate(rooms):
        x0, y0, x1, y1 = r
        name = ROOM_NAMES[i % len(ROOM_NAMES)]
        number = str(101 + i)
        text = f"{name} {number}"
        fnt = _font(40)
        bb = d.textbbox((0, 0), text, font=fnt)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        lx = X((x0 + x1) / 2) - tw / 2
        ly = Y((y0 + y1) / 2) - th / 2 - 30
        label_boxes.append((text, lx, ly, lx + tw, ly + th, fnt))

    def _clear_of_labels(px_m, py_m, rooms=rooms):
        cxp, cyp = X(px_m), Y(py_m)
        for _, lx, ly, lx1, ly1, _ in label_boxes:
            if lx - 40 <= cxp <= lx1 + 40 and ly - 40 <= cyp <= ly1 + 40:
                return False
        return True

    # ---- fixtures ----------------------------------------------------------
    fixtures = []
    gt_rooms = []
    for i, r in enumerate(rooms):
        x0, y0, x1, y1 = r
        name = ROOM_NAMES[i % len(ROOM_NAMES)]
        number = str(101 + i)
        area = (x1 - x0) * (y1 - y0)
        fclass, m2_per, fmin, fmax = ROOM_FIXTURE_PLAN.get(name, DEFAULT_PLAN)
        n = int(min(fmax, max(fmin, round(area / m2_per))))
        tag = CLASS_TO_TAG[fclass]
        w_px = FIXTURE_WIDTH_M[fclass] * S

        placed = []
        for _ in range(n):
            for _try in range(60):
                px = rng.uniform(x0 + 0.8, x1 - 0.8)
                py = rng.uniform(y0 + 0.8, y1 - 0.8)
                if all(
                    abs(px - qx) > 1.1 or abs(py - qy) > 1.1 for qx, qy in placed
                ) and _clear_of_labels(px, py):
                    placed.append((px, py))
                    break
        for px, py in placed:
            bbox = _paste_glyph(img, LIGHTING_GLYPHS[fclass], w_px, X(px), Y(py), rng)
            fixtures.append(
                {
                    "type": fclass,
                    "tag": tag,
                    "bbox_px": bbox,
                    "room_idx": i,
                    "drawing": "floor_plan",
                }
            )

        poly_m = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        gt_rooms.append(
            {
                "name": name,
                "number": number,
                "polygon_m": poly_m,
                "polygon_px": [[X(px), Y(py)] for px, py in poly_m],
                "area_m2": area,
            }
        )

    for text, lx, ly, _lx1, _ly1, fnt in label_boxes:
        d.text((lx, ly), text, fill=0, font=fnt)

    # ---- exit signs: ~1 per 3 rooms, near a random wall ----------------------
    for i in range(0, len(rooms), 3):
        r = rooms[i % len(rooms)]
        x0, y0, x1, y1 = r
        for _try in range(30):
            side = rng.integers(4)
            px = {
                0: x0 + 0.6,
                1: x1 - 0.6,
                2: rng.uniform(x0 + 1, x1 - 1),
                3: rng.uniform(x0 + 1, x1 - 1),
            }[side]
            py = {
                0: rng.uniform(y0 + 1, y1 - 1),
                1: rng.uniform(y0 + 1, y1 - 1),
                2: y0 + 0.6,
                3: y1 - 0.6,
            }[side]
            if _clear_of_labels(px, py):
                break
        w_px = FIXTURE_WIDTH_M["Exit Sign"] * S
        bbox = _paste_glyph(img, LIGHTING_GLYPHS["Exit Sign"], w_px, X(px), Y(py), rng)
        fixtures.append(
            {
                "type": "Exit Sign",
                "tag": "X",
                "bbox_px": bbox,
                "room_idx": i % len(rooms),
                "drawing": "floor_plan",
            }
        )

    # ---- lighting schedule table ---------------------------------------------
    tx = ox + plan_w_px + 70
    d.text((tx, 60), f"SYNTHETIC LIGHTING -- sheet_{seed:03d}", fill=0, font=_font(30))
    cw = [90, 520, 150]
    rh = 42
    fnt = _font(24)
    y = 150
    d.text((tx, y), "LIGHTING SCHEDULE", fill=0, font=_font(30))
    y += 44
    for ci, htxt in enumerate(["TAG", "DESCRIPTION", "WATTS"]):
        x0c = tx + sum(cw[:ci])
        d.rectangle([x0c, y, x0c + cw[ci], y + rh], outline=0, width=2)
        d.text((x0c + 10, y + 8), htxt, fill=0, font=fnt)
    y += rh
    for s in LIGHTING_SCHEDULE:
        for ci, txt in enumerate([s["tag"], s["description"], f"{s['watts']:.0f}"]):
            x0c = tx + sum(cw[:ci])
            d.rectangle([x0c, y, x0c + cw[ci], y + rh], outline=0, width=2)
            d.text((x0c + 10, y + 8), txt, fill=0, font=fnt)
        y += rh

    # ---- expected takeoff -------------------------------------------------------
    per_room = []
    total_w = 0.0
    for i, gr in enumerate(gt_rooms):
        w = sum(LIGHTING_SCHED_BY_TAG[f["tag"]]["watts"] for f in fixtures if f["room_idx"] == i)
        total_w += w
        per_room.append(
            {
                "name": gr["name"],
                "number": gr["number"],
                "area_m2": gr["area_m2"],
                "watts": w,
                "lpd_w_m2": w / gr["area_m2"],
            }
        )
    gt = {
        "sheet_id": f"lighting_{seed:03d}",
        "seed": seed,
        "scale_px_per_m": S,
        "sheet_size_px": [sheet_w, sheet_h],
        "rooms": gt_rooms,
        "fixtures": fixtures,
        "schedule": LIGHTING_SCHEDULE,
        "expected": {"total_w": total_w, "per_room": per_room, "n_fixtures": len(fixtures)},
    }
    return np.asarray(img), gt


def save_lighting_sheet(seed: int, outdir: str | Path):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    img, gt = generate_lighting_sheet(seed)
    png = outdir / f"{gt['sheet_id']}.png"
    js = outdir / f"{gt['sheet_id']}.gt.json"
    Image.fromarray(img.astype(np.uint8)).save(png)
    with open(js, "w") as f:
        json.dump(gt, f, indent=1)
    return png, js
