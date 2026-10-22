#!/usr/bin/env python3
"""Scan-degradation family for synthetic drawing sheets (issue #1241).

Real building drawings arrive as imperfect scans: skewed scanner feeds,
low-DPI copies, JPEG-compressed PDFs, faded third-generation copies, sheets
photocopied back-to-back (bleed-through), and soft optics. Training detection
and parsing only on clean synthetic pages leaves those conditions untested.

This module implements the family from #1241 as deterministic, composable
degradations applied per sheet:

  rotation_skew   scanner feed tilt (rotation plus slight shear)
  low_dpi         downsampling and back-resampling (150-200 DPI copies)
  jpeg            JPEG compression artifacts
  contrast_loss   faded-copy contrast and level loss
  bleed_through   faint mirrored ink showing through from the sheet behind
  blur            mild Gaussian blur, one member of the family, not the whole

Design rules from the issue:

  - Severity is SAMPLED per sheet across a realistic range, never one fixed
    level: ``apply_scan_family`` draws a member and a severity from one seeded
    rng. For evaluation, ``EVAL_SEVERITY`` pins a comparable fixed level.
  - Truth stays tied to the source geometry: ``transform_boxes`` and
    ``transform_gt`` apply the same forward transform the image received, so
    labels remain aligned after degradation. Only rotation_skew moves
    geometry; every other member leaves boxes untouched.
  - A clean, undegraded validation set is kept by construction: callers apply
    the family to the training split only (see convert_synth).
  - Robustness gain on degraded sheets must not cost clean accuracy: the
    comparison harness lives in eval_robustness (``--family``), which reports
    clean and degraded deltas side by side.

Everything is pure PIL/numpy (no scipy/torch), CPU-friendly, and
deterministic: the same (image bytes, member, severity, seed) always yields
the same pixels.

Usage:
    from detector.degrade_scans import FAMILY, apply_scan, apply_scan_family
    degraded, member, severity = apply_scan_family(img, seed=7)
    # or with truth boxes aligned:
    degraded, boxes, meta = apply_family_with_boxes(img, boxes, seed=7)
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

FAMILY = (
    "rotation_skew",
    "low_dpi",
    "jpeg",
    "contrast_loss",
    "bleed_through",
    "blur",
)

# Fixed severity for evaluation comparability: eval compares clean vs each
# member at the same level, while training samples across the full range.
EVAL_SEVERITY = 0.7

# Realistic severity ranges, member by member (0 = imperceptible, 1 = heavy
# but still human-readable). Training samples uniformly inside the range.
SEVERITY_RANGES = {
    "rotation_skew": (0.0, 1.0),
    "low_dpi": (0.0, 1.0),
    "jpeg": (0.0, 1.0),
    "contrast_loss": (0.0, 1.0),
    "bleed_through": (0.0, 1.0),
    "blur": (0.0, 1.0),
}

# rotation_skew: total angular deflection in degrees = TILT_LO + severity *
# (TILT_HI - TILT_LO), split between a rigid rotation and a horizontal shear
# (SKEW_FRACTION of the angle). Scanner feeds land within about 1.5 degrees.
_TILT_LO, _TILT_HI, _SKEW_FRACTION = 0.1, 1.5, 0.3
# low_dpi: back-resampling factor = 1 - DPI_LOSS * severity (a 300 DPI render
# copied at 150-200 DPI).
_DPI_LOSS = 0.5
# jpeg: JPEG quality = JPEG_Q_HI - (JPEG_Q_HI - JPEG_Q_LO) * severity.
_JPEG_Q_LO, _JPEG_Q_HI = 50, 95
# contrast_loss: contrast factor = 1 - CONTRAST_LOSS * severity; brightness
# lifted by BRIGHT_LIFT * severity (faded copies read lighter, not darker).
_CONTRAST_LOSS, _BRIGHT_LIFT = 0.4, 0.15
# bleed_through: mirrored-ink blend alpha = BT_LO + (BT_HI - BT_LO) * severity.
_BT_LO, _BT_HI = 0.05, 0.20
# blur: Gaussian radius in px = BLUR_LO + (BLUR_HI - BLUR_LO) * severity.
_BLUR_LO, _BLUR_HI = 0.3, 1.3


def variant_seed(base_seed: int, index: int) -> int:
    """Same per-image seed derivation as degrade.variant_seed."""
    return (base_seed * 1_000_003 + index * 9_176_439) % (2**31 - 1)


def sample_member(rng: np.random.Generator) -> str:
    """Draw one family member uniformly."""
    return FAMILY[int(rng.integers(len(FAMILY)))]


def sample_severity(member: str, rng: np.random.Generator) -> float:
    """Draw one severity uniformly across the member's realistic range."""
    if member not in SEVERITY_RANGES:
        raise ValueError(f"unknown degradation member: {member!r} (expected one of {FAMILY})")
    lo, hi = SEVERITY_RANGES[member]
    return float(rng.uniform(lo, hi))


def _affine_forward(angle_deg: float, skew: float, w: int, h: int) -> tuple:
    """Forward affine matrix (A 2x2, t 2) mapping input px to output px.

    Shear is applied first (x' = x + skew * (y - cy)), then rotation about
    the sheet center. Returned as (a, b, c, d, e, f) meaning
    in_out = A @ (x, y) + t column-major-friendly form for reuse by
    transform_points.
    """
    th = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    shear = np.array([[1.0, skew], [0.0, 1.0]])
    center = np.array([w / 2.0, h / 2.0])
    A = rot @ shear
    # p_out = A @ p_in + (center - A @ center)
    t = center - A @ center
    return A, t


def _pil_coeffs(A: np.ndarray, t: np.ndarray, w: int, h: int) -> tuple:
    """PIL Image.transform AFFINE coefficients: OUTPUT -> INPUT (M^-1)."""
    Ainv = np.linalg.inv(A)
    tinv = -Ainv @ t
    a, b = Ainv[0, 0], Ainv[0, 1]
    d, e = Ainv[1, 0], Ainv[1, 1]
    c, f = tinv[0], tinv[1]
    return (a, b, c, d, e, f)


def transform_points(points, member: str, severity: float, seed: int, size: tuple):
    """Map source-geometry points through the degradation's forward transform.

    ``points`` is any iterable of (x, y) pairs in source px; returns the same
    shape transformed. Identity for every non-geometric member, so callers can
    hand truth through unchanged.
    """
    pts = np.asarray(points, dtype=np.float64)
    if member != "rotation_skew":
        return pts
    w, h = size
    rng = np.random.default_rng(seed)
    tilt = _TILT_LO + (_TILT_HI - _TILT_LO) * float(severity)
    sign = 1.0 if rng.integers(2) == 0 else -1.0
    angle = sign * tilt
    skew = _SKEW_FRACTION * np.deg2rad(tilt) * sign
    A, t = _affine_forward(angle, skew, w, h)
    return pts @ A.T + t


def transform_boxes(boxes, member: str, severity: float, seed: int, size: tuple) -> list:
    """Keep truth boxes aligned after degradation.

    ``boxes`` is a list of {"x0","y0","x1","y1"} dicts or [x0,y0,x1,y1] lists
    in source px. Non-geometric members return the boxes unchanged. For
    rotation_skew, each box's four corners go through the same forward affine
    the image received and the result is the axis-aligned bounding box of the
    rotated corners, clipped to the sheet. Boxes that degenerate below 1 px
    on either axis after clipping are dropped (counted, not hidden).
    """
    if member != "rotation_skew":
        return boxes
    w, h = size
    rng = np.random.default_rng(seed)
    tilt = _TILT_LO + (_TILT_HI - _TILT_LO) * float(severity)
    sign = 1.0 if rng.integers(2) == 0 else -1.0
    angle = sign * tilt
    skew = _SKEW_FRACTION * np.deg2rad(tilt) * sign
    A, t = _affine_forward(angle, skew, w, h)
    out = []
    for bx in boxes:
        if isinstance(bx, dict):
            x0, y0, x1, y1 = bx["x0"], bx["y0"], bx["x1"], bx["y1"]
        else:
            x0, y0, x1, y1 = bx
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
        tc = corners @ A.T + t
        nx0 = max(0.0, float(tc[:, 0].min()))
        ny0 = max(0.0, float(tc[:, 1].min()))
        nx1 = min(float(w), float(tc[:, 0].max()))
        ny1 = min(float(h), float(tc[:, 1].max()))
        if nx1 - nx0 < 1.0 or ny1 - ny0 < 1.0:
            continue
        if isinstance(bx, dict):
            out.append({**bx, "x0": nx0, "y0": ny0, "x1": nx1, "y1": ny1})
        else:
            out.append([nx0, ny0, nx1, ny1])
    return out


def apply_scan(img: Image.Image, member: str, severity: float, seed: int) -> Image.Image:
    """Apply one family member at a given severity. Deterministic in seed.

    Severity 0 is the identity for members whose range starts at 0 effect
    (jpeg at q95, tilt 0.1 degrees is the floor for rotation_skew).
    """
    if member not in FAMILY:
        raise ValueError(f"unknown degradation member: {member!r} (expected one of {FAMILY})")
    sev = float(severity)
    rgb = img.convert("RGB")
    if member == "rotation_skew":
        rng = np.random.default_rng(seed)
        tilt = _TILT_LO + (_TILT_HI - _TILT_LO) * sev
        sign = 1.0 if rng.integers(2) == 0 else -1.0
        A, t = _affine_forward(sign * tilt, _SKEW_FRACTION * np.deg2rad(tilt) * sign, *rgb.size)
        return rgb.transform(
            rgb.size,
            Image.AFFINE,
            _pil_coeffs(A, t, *rgb.size),
            resample=Image.BILINEAR,
            fillcolor="white",
        )
    if member == "low_dpi":
        factor = 1.0 - _DPI_LOSS * sev
        small = rgb.resize(
            (max(1, int(rgb.width * factor)), max(1, int(rgb.height * factor))), Image.BOX
        )
        return small.resize(rgb.size, Image.BILINEAR)
    if member == "jpeg":
        quality = _JPEG_Q_HI - (_JPEG_Q_HI - _JPEG_Q_LO) * sev
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=int(round(quality)))
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    if member == "contrast_loss":
        out = ImageEnhance.Contrast(rgb).enhance(1.0 - _CONTRAST_LOSS * sev)
        return ImageEnhance.Brightness(out).enhance(1.0 + _BRIGHT_LIFT * sev)
    if member == "bleed_through":
        base = np.asarray(rgb.convert("L"), dtype=np.float32)
        flipped = rgb.transpose(Image.FLIP_LEFT_RIGHT).convert("L")
        back_dark = np.asarray(ImageOps.invert(flipped), dtype=np.float32)
        alpha = _BT_LO + (_BT_HI - _BT_LO) * sev
        out = np.clip(base - alpha * back_dark, 0.0, 255.0).astype(np.uint8)
        return Image.fromarray(out).convert("RGB")
    if member == "blur":
        return rgb.filter(ImageFilter.GaussianBlur(_BLUR_LO + (_BLUR_HI - _BLUR_LO) * sev))
    raise AssertionError("unreachable member dispatch")


def apply_scan_family(img: Image.Image, seed: int) -> tuple:
    """Sample one member + severity per sheet and apply it.

    Returns (degraded_image, member, severity). The member and severity come
    from one seeded rng, so the same seed always reproduces the same sheet.
    """
    rng = np.random.default_rng(seed)
    member = sample_member(rng)
    severity = sample_severity(member, rng)
    return apply_scan(img, member, severity, seed), member, severity


def apply_family_with_boxes(img: Image.Image, boxes, seed: int) -> tuple:
    """apply_scan_family plus aligned truth boxes.

    Returns (degraded_image, aligned_boxes, meta) where meta carries the
    sampled member, severity, and how many boxes were dropped as degenerate.
    """
    rng = np.random.default_rng(seed)
    member = sample_member(rng)
    severity = sample_severity(member, rng)
    n_in = len(boxes)
    aligned = transform_boxes(boxes, member, severity, seed, img.size)
    degraded = apply_scan(img, member, severity, seed)
    meta = {"member": member, "severity": severity, "boxes_dropped": n_in - len(aligned)}
    return degraded, aligned, meta


def transform_gt(gt: dict, member: str, severity: float, seed: int, size: tuple) -> dict:
    """Transform a synth generate_sheet() ground-truth dict in place-ish.

    Moves every geometry the sheet carries: symbols bbox_px, labels bbox_px,
    walls a_px/b_px (t_px unchanged: at <=1.5 degrees the thickness change is
    sub-pixel), rooms polygon_px. Room areas, schedule entries, and the
    expected rollups are untouched: rotation preserves area and the shear
    changes it by <=0.03%, below the rollup tolerance.
    """
    if member != "rotation_skew":
        return gt
    w, h = size

    def _pt(p):
        q = transform_points([p], member, severity, seed, (w, h))[0]
        return [float(q[0]), float(q[1])]

    def _bbox(bb):
        q = transform_points(
            [[bb[0], bb[1]], [bb[2], bb[3]], [bb[2], bb[1]], [bb[0], bb[3]]],
            member,
            severity,
            seed,
            (w, h),
        )
        xs, ys = q[:, 0], q[:, 1]
        return [
            max(0.0, float(xs.min())),
            max(0.0, float(ys.min())),
            min(float(w), float(xs.max())),
            min(float(h), float(ys.max())),
        ]

    for sym in gt.get("symbols", []):
        if "bbox_px" in sym:
            sym["bbox_px"] = _bbox(sym["bbox_px"])
    for lab in gt.get("labels", []):
        if "bbox_px" in lab:
            lab["bbox_px"] = _bbox(lab["bbox_px"])
    for wall in gt.get("walls", []):
        if "a_px" in wall:
            wall["a_px"] = _pt(wall["a_px"])
        if "b_px" in wall:
            wall["b_px"] = _pt(wall["b_px"])
    for room in gt.get("rooms", []):
        if "polygon_px" in room:
            room["polygon_px"] = [_pt(p) for p in room["polygon_px"]]
    return gt
