"""Door-swing detection on plan images (door_detect.py)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from door_detect import METHOD, detect_door_swings

PPM = 50.0


def _canvas(width_m=0.9, *, label=False):
    """A vertical 20 px wall with one door, leaf east, arc toward +y."""
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    x = 150
    d.rectangle([x - 10, 40, x + 10, 360], fill=0)
    d.rectangle([x - 10, 40, 330, 50], fill=0)  # a cross wall, for clutter
    r = width_m * PPM
    yh = 150
    d.rectangle([x - 11, yh, x + 11, yh + r], fill=255)
    d.line([x, yh, x + r, yh], fill=0, width=3)
    d.arc([x - r, yh - r, x + r, yh + r], start=0, end=90, fill=0, width=2)
    d.line([0, 300, 399, 300], fill=120, width=1)  # grey grid line: not ink
    if label:
        d.text((x + 15, yh + 12), "CLOSET", fill=0)
    return np.asarray(img), (x, yh + r / 2), r


def _orient(img, pt, k, flip):
    """Rotate/flip the image and track where pt lands."""
    h, w = img.shape
    x, y = pt
    if flip:
        img, x = img[:, ::-1], w - 1 - x
    for _ in range(k):  # np.rot90: (x, y) -> (y, W-1-x)
        h, w = img.shape
        img, (x, y) = np.rot90(img), (y, w - 1 - x)
    return np.ascontiguousarray(img), (x, y)


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_finds_the_door_in_every_orientation(k, flip):
    img, centre, r = _canvas()
    img, (cx, cy) = _orient(img, centre, k, flip)
    (det,) = detect_door_swings(img, PPM)
    assert det["method"] == METHOD
    assert np.hypot(det["x_px"] - cx, det["y_px"] - cy) <= 3
    assert abs(det["width_px"] - r) <= 3


@pytest.mark.parametrize("width_m", [0.7, 0.9, 1.1])
def test_width_and_label_over_the_swing(width_m):
    img, (cx, cy), r = _canvas(width_m, label=True)
    (det,) = detect_door_swings(img, PPM)
    assert np.hypot(det["x_px"] - cx, det["y_px"] - cy) <= 3
    assert abs(det["width_px"] - r) <= 3


def _face_canvas(*, arc=True, side_m=3.6, thick_px=10):
    """A door hung beside a cross wall that meets its hinge jamb (#743).

    The open leaf lies on the cross wall's face, so the drafted leaf and the
    wall merge into one band of ink.
    """
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    x, yh, r = 150, 150, 0.9 * PPM
    d.rectangle([x - 10, 40, x + 10, 360], fill=0)
    d.rectangle([x - 11, yh, x + 11, yh + r], fill=255)
    d.rectangle([x + 10, yh - thick_px, x + 10 + side_m * PPM, yh + 1], fill=0)
    d.line([x, yh, x + r, yh], fill=0, width=3)
    if arc:
        d.arc([x - r, yh - r, x + r, yh + r], start=0, end=90, fill=0, width=2)
    return np.asarray(img), (x, yh + r / 2), r


@pytest.mark.parametrize("flip", [False, True])
@pytest.mark.parametrize("k", [0, 1, 2, 3])
@pytest.mark.parametrize(("side_m", "thick_px"), [(3.6, 10), (0.6, 10), (3.6, 5)])
def test_leaf_lying_on_a_wall_face(k, flip, side_m, thick_px):
    img, centre, r = _face_canvas(side_m=side_m, thick_px=thick_px)
    img, (cx, cy) = _orient(img, centre, k, flip)
    (det,) = detect_door_swings(img, PPM)
    assert np.hypot(det["x_px"] - cx, det["y_px"] - cy) <= 3
    assert abs(det["width_px"] - r) <= 3


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_wall_face_at_a_gap_without_an_arc_is_not_a_door(k):
    img, centre, _ = _face_canvas(arc=False)
    img, _ = _orient(img, centre, k, False)
    assert detect_door_swings(img, PPM) == []


def test_leaf_on_a_wall_face_needs_the_jamb_wall_past_the_opening():
    """With the leaf on a face only the far jamb places the hinge (#743)."""
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    x, yh, r = 150, 150, 45
    d.rectangle([x - 10, 40, x + 10, yh], fill=0)  # wall ends at the hinge
    d.rectangle([x + 10, yh - 10, x + 190, yh + 1], fill=0)
    d.arc([x - r, yh - r, x + r, yh + r], start=0, end=90, fill=0, width=2)
    assert detect_door_swings(np.asarray(img), PPM) == []


def test_wall_corners_without_swings_are_not_doors():
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([140, 40, 160, 360], fill=0)
    d.rectangle([160, 140, 360, 150], fill=0)  # T into the wall
    d.rectangle([40, 250, 140, 260], fill=0)  # and one from the other side
    d.rectangle([40, 40, 360, 50], fill=0)  # an L at the top
    assert detect_door_swings(np.asarray(img), PPM) == []


def test_wall_gap_without_a_swing_is_not_a_door():
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([140, 40, 160, 360], fill=0)
    d.rectangle([139, 150, 161, 195], fill=255)  # opening, no symbol drawn
    assert detect_door_swings(np.asarray(img), PPM) == []


def test_symbols_outside_the_width_range_are_ignored():
    img, _, _ = _canvas(0.4)  # 20 px: below MIN_WIDTH_M
    assert detect_door_swings(img, PPM) == []


def test_rejects_colour_images():
    with pytest.raises(ValueError):
        detect_door_swings(np.zeros((10, 10, 3), np.uint8), PPM)


def test_synthetic_buildings_recall_and_no_false_doors():
    from synth.multidiscipline import generate_building

    hit = total = false = 0
    for seed in range(8):
        b = generate_building(seed, service_rooms=True)
        arch = b["sheets"]["arch"]
        dets = detect_door_swings(arch["image"], arch["meta"]["px_per_m"])
        for g in arch["doors"]:
            total += 1
            near = [d for d in dets if np.hypot(d["x_px"] - g["x_px"], d["y_px"] - g["y_px"]) <= 6]
            hit += bool(near)
        false += sum(
            all(np.hypot(d["x_px"] - g["x_px"], d["y_px"] - g["y_px"]) > 6 for g in arch["doors"])
            for d in dets
        )
    assert total >= 6
    assert hit / total >= 0.8
    assert false == 0


def test_default_building_has_no_false_doors():
    from synth.multidiscipline import generate_building

    b = generate_building(5)
    arch = b["sheets"]["arch"]
    assert detect_door_swings(arch["image"], arch["meta"]["px_per_m"]) == []


def test_larger_scale_drawing():
    """Tolerances grow with the scale: a 3x NEAREST upscale still reads."""
    from synth.multidiscipline import generate_building

    b = generate_building(3, service_rooms=True)
    a = b["sheets"]["arch"]
    img = a["image"]
    big = np.asarray(
        Image.fromarray(img).resize((img.shape[1] * 3, img.shape[0] * 3), Image.NEAREST)
    )
    (det,) = detect_door_swings(big, 3 * a["meta"]["px_per_m"])
    (g,) = a["doors"]
    assert np.hypot(det["x_px"] / 3 - g["x_px"], det["y_px"] / 3 - g["y_px"]) <= 3
    assert abs(det["width_px"] / 3 - g["width_px"]) <= 3


def test_double_line_wall_and_outlined_leaf():
    """Thin-line drafting: wall as two lines, leaf as an outlined panel."""
    ppm = 164  # ~200 dpi at 1/4" = 1'-0"
    img = Image.new("L", (900, 900), 255)
    d = ImageDraw.Draw(img)
    x, t2 = 300, int(0.15 * ppm)
    for xx in (x - t2 // 2, x + t2 // 2):
        d.line([xx, 60, xx, 840], fill=0, width=2)
    r, yh = int(0.9 * ppm), 250
    d.rectangle([x - t2 // 2 - 2, yh, x + t2 // 2 + 2, yh + r], fill=255)
    d.line([x - t2 // 2, yh, x + t2 // 2, yh], fill=0, width=2)
    d.line([x - t2 // 2, yh + r, x + t2 // 2, yh + r], fill=0, width=2)
    d.rectangle([x, yh - 3, x + r, yh + 3], outline=0, width=2)
    d.arc([x - r, yh - r, x + r, yh + r], start=0, end=90, fill=0, width=2)
    (det,) = detect_door_swings(np.asarray(img), float(ppm))
    assert np.hypot(det["x_px"] - x, det["y_px"] - (yh + r / 2)) <= 4
    assert abs(det["width_px"] - r) <= 6
