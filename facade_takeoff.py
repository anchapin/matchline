"""Facade area takeoffs from rectified elevation imagery (CMP Facade dataset).

Exercises the core requirement: "wall, window, and door area from elevation
drawings." CMP gives us 606 rectified facades with per-pixel semantic masks,
which is the closest public stand-in for dimensioned CAD elevations.

The central honesty point: rectified PHOTOS ARE SCALE-FREE. There is no
dimension on the drawing, so absolute m^2 is unknowable from the image alone.
The module therefore computes area FRACTIONS and RATIOS natively (WWR, door
fraction, opaque fraction) and only produces absolute m^2 when the caller
supplies at least one known real-world dimension (facade width and/or height)
-- exactly mirroring the real workflow of rectified elevation + scale bar.

Class mapping decisions (documented, see docs/facade_takeoff.md):
  - GLAZING = window (3) + blind (8) + shop (12). Blinds are closed roller
    shutters over window openings (verified visually); shops are storefront
    glazing. Both are glazed/occluded openings in the wall plane.
  - WALL_PLANE = {facade, window, door, blind, shop}. Cornice/sill/balcony/
    deco/molding/pillar are trim or projections, excluded from the wall-plane
    denominator -- the same way a quantity surveyor excludes them from gross
    wall area.
  - WWR = glazing / wall_plane; door_frac = door / wall_plane;
    opaque_frac = facade / wall_plane. These three sum to 1.0 by construction.

License note: CMP Facade is CC BY-SA (share-alike). Derived statistics and
takeoffs inherit share-alike obligations; the code in this file is ours.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from building_model import Provenance

DATASET_NAME = "CMP Facade Database (Tylecek & Sara)"
DATASET_LICENSE = "CC BY-SA (share-alike)"
DATASET_URL = "http://cmp.felk.cvut.cz/~tylecr1/facade/"

CLASS_NAMES = {
    1: "background",
    2: "facade",
    3: "window",
    4: "door",
    5: "cornice",
    6: "sill",
    7: "balcony",
    8: "blind",
    9: "deco",
    10: "molding",
    11: "pillar",
    12: "shop",
}

# --- class groupings -------------------------------------------------------
GLAZING = frozenset({3, 8, 12})  # window + shuttered window + storefront
WALL_PLANE = frozenset({2, 3, 4, 8, 12})  # the wall itself incl. its openings
OPAQUE = frozenset({2})  # plain wall
DOOR = frozenset({4})
TRIM = frozenset({5, 6, 9, 10, 11})  # cornice, sill, deco, molding, pillar
PROJECTION = frozenset({7})  # balcony

REVIEW_SCALE_DISAGREE = 0.05  # warn if width- and height-derived scales differ


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_mask(path: str | Path) -> np.ndarray:
    """Load a CMP palette PNG; returns int array of class ids."""
    return np.asarray(Image.open(path)).astype(np.int32)


def class_counts(mask: np.ndarray) -> dict[int, int]:
    vals, counts = np.unique(mask, return_counts=True)
    return {int(v): int(c) for v, c in zip(vals, counts)}


def facade_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box (x0,y0,x1,y1, exclusive) of the facade region (non-bg)."""
    ys, xs = np.nonzero(mask != 1)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ---------------------------------------------------------------------------
# Core takeoff
# ---------------------------------------------------------------------------


@dataclass
class FacadeTakeoff:
    facade_id: str
    image_size_px: tuple[int, int]  # (W, H)
    class_px: dict[int, int]  # raw pixel counts per class id
    wall_plane_px: int
    facade_region_px: int  # all non-background px
    # fractions over the wall plane (sum of opaque+glazing+door == 1.0)
    frac_opaque: float | None
    frac_glazing: float | None  # == WWR
    frac_door: float | None
    frac_blind_of_glazing: float | None  # how much glazing is shuttered
    frac_shop_of_glazing: float | None
    wwr: float | None
    # absolute areas (None unless a real-world dimension was supplied)
    width_m: float | None = None
    height_m: float | None = None
    px_per_m: float | None = None
    area_wall_plane_m2: float | None = None
    area_glazing_m2: float | None = None
    area_door_m2: float | None = None
    area_opaque_m2: float | None = None
    scale_warnings: list[str] = field(default_factory=list)
    provenance: Provenance | None = None

    def to_envelope_dict(self) -> dict:
        """Handoff shape for the BuildingModel envelope layer.

        Deliberately a plain dict, not a BuildingModel edit: the envelope
        consumer decides how to attach it (facade -> wall segments).
        Absolute areas are present only when a scale was supplied.
        """
        return {
            "facade_id": self.facade_id,
            "gross_wall_plane_m2": self.area_wall_plane_m2,
            "window_glazing_m2": self.area_glazing_m2,
            "door_m2": self.area_door_m2,
            "opaque_wall_m2": self.area_opaque_m2,
            "wwr": self.wwr,
            "door_fraction": self.frac_door,
            "opaque_fraction": self.frac_opaque,
            "scale_supplied": self.px_per_m is not None,
            "provenance": asdict(self.provenance) if self.provenance else None,
        }


def _frac(num: int, den: int) -> float | None:
    return num / den if den > 0 else None


def facade_takeoff(
    mask: np.ndarray,
    facade_id: str,
    width_m: float | None = None,
    height_m: float | None = None,
    sheet_id: str | None = None,
) -> FacadeTakeoff:
    """Compute the takeoff for one facade mask.

    width_m / height_m: optional known real-world facade dimensions. Scale is
    derived from width (the reliable axis on rectified facades); if height is
    also given it is used as a consistency check, not averaged in.
    """
    counts = class_counts(mask)
    H, W = mask.shape
    wall_plane_px = sum(counts.get(c, 0) for c in WALL_PLANE)
    facade_region_px = int((mask != 1).sum())
    glazing_px = sum(counts.get(c, 0) for c in GLAZING)

    px_per_m = None
    warnings: list[str] = []
    if width_m is not None:
        bb = facade_bbox(mask)
        if bb is None or width_m <= 0:
            warnings.append("no facade region or non-positive width; absolute areas unavailable")
        else:
            x0, _, x1, _ = bb
            px_per_m = (x1 - x0) / width_m
            if height_m is not None and height_m > 0:
                _, y0, _, y1 = bb
                ppm_h = (y1 - y0) / height_m
                if abs(ppm_h - px_per_m) / px_per_m > REVIEW_SCALE_DISAGREE:
                    warnings.append(
                        f"width-derived scale ({px_per_m:.1f} px/m) and "
                        f"height-derived scale ({ppm_h:.1f} px/m) disagree "
                        f"by >{REVIEW_SCALE_DISAGREE:.0%}; photo may not be "
                        f"perfectly rectified or bbox includes projections"
                    )

    def abs_area(px: int) -> float | None:
        return px / px_per_m**2 if px_per_m else None

    opaque_px = counts.get(2, 0)
    door_px = counts.get(4, 0)
    prov = Provenance(
        sheet_id=sheet_id or facade_id,
        revision=1,
        method="mask_pixel_takeoff",
        confidence=0.90,
        note=(
            f"{DATASET_NAME}; license {DATASET_LICENSE}. "
            f"{'Scaled by supplied facade width' if width_m else 'SCALE-FREE: fractions only'}."
        ),
    )

    return FacadeTakeoff(
        facade_id=facade_id,
        image_size_px=(W, H),
        class_px=counts,
        wall_plane_px=wall_plane_px,
        facade_region_px=facade_region_px,
        frac_opaque=_frac(opaque_px, wall_plane_px),
        frac_glazing=_frac(glazing_px, wall_plane_px),
        frac_door=_frac(door_px, wall_plane_px),
        frac_blind_of_glazing=_frac(counts.get(8, 0), glazing_px),
        frac_shop_of_glazing=_frac(counts.get(12, 0), glazing_px),
        wwr=_frac(glazing_px, wall_plane_px),
        width_m=width_m,
        height_m=height_m,
        px_per_m=px_per_m,
        area_wall_plane_m2=abs_area(wall_plane_px),
        area_glazing_m2=abs_area(glazing_px),
        area_door_m2=abs_area(door_px),
        area_opaque_m2=abs_area(opaque_px),
        scale_warnings=warnings,
        provenance=prov,
    )


# ---------------------------------------------------------------------------
# XML cross-check: coarse rectangles vs precise mask
# ---------------------------------------------------------------------------


def parse_xml_boxes(xml_path: str | Path) -> list[tuple[int, float, float, float, float]]:
    """Parse CMP XML objects -> [(label, x0, y0, x1, y1)] normalized coords."""
    txt = Path(xml_path).read_text(encoding="utf-8", errors="replace")
    boxes = []
    for m in re.finditer(r"<object>.*?</object>", txt, re.S):
        o = m.group(0)
        lab = re.search(r"<label>\s*(\d+)\s*</label>", o)
        if not lab:
            continue
        xs = [float(v) for v in re.findall(r"<x>\s*([\d.eE+-]+)\s*</x>", o)]
        ys = [float(v) for v in re.findall(r"<y>\s*([\d.eE+-]+)\s*</y>", o)]
        if len(xs) >= 2 and len(ys) >= 2:
            boxes.append((int(lab.group(1)), min(xs), min(ys), max(xs), max(ys)))
    return boxes


def rasterize_boxes(
    boxes: list[tuple[int, float, float, float, float]], H: int, W: int
) -> dict[int, np.ndarray]:
    """Union-rasterize boxes per class label -> {label: bool array}."""
    out: dict[int, np.ndarray] = {}
    for lab, x0, y0, x1, y1 in boxes:
        arr = out.setdefault(lab, np.zeros((H, W), dtype=bool))
        arr[int(y0 * H) : int(math.ceil(y1 * H)), int(x0 * W) : int(math.ceil(x1 * W))] = True
    return out


def _dilate(arr: np.ndarray, iters: int) -> np.ndarray:
    """Pure-numpy 3x3 binary dilation (avoids a scipy dependency)."""
    out = arr.copy()
    for _ in range(iters):
        pad = np.pad(out, 1)
        out = (
            pad[1:-1, 1:-1]
            | pad[:-2, :-2]
            | pad[:-2, 1:-1]
            | pad[:-2, 2:]
            | pad[1:-1, :-2]
            | pad[1:-1, 2:]
            | pad[2:, :-2]
            | pad[2:, 1:-1]
            | pad[2:, 2:]
        )
    return out


def _components(arr: np.ndarray) -> int:
    """Count 4-connected components (scipy if available, else flood fill)."""
    try:
        from scipy.ndimage import label

        _, n = label(arr, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
        return int(n)
    except ImportError:
        pass
    seen = np.zeros_like(arr, dtype=bool)
    n = 0
    H, W = arr.shape
    for y, x in zip(*np.nonzero(arr & ~seen)):
        n += 1
        stack = [(y, x)]
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < H and 0 <= nx < W and arr[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
    return n


def xml_agreement(
    mask: np.ndarray, boxes: list[tuple[int, float, float, float, float]], slop_frac: float = 0.02
) -> dict:
    """Compare XML coarse boxes against the precise mask, per class.

    Because the XML boxes are loosely drawn (verified: they are offset from
    the true window pixels by varying amounts per image), raw IoU understates
    agreement. We therefore report three views per class:
      - ratio_xml_over_mask: size bias (robust to misalignment)
      - iou: strict pixel agreement (lower bound)
      - coverage_mask_in_xml: fraction of mask pixels inside XML boxes
        dilated by slop_frac of image width (slop-tolerant recall)
      - coverage_xml_in_mask: fraction of XML box area inside the dilated
        mask (slop-tolerant precision)
    Plus component counts: XML box count vs mask connected-component count.
    Also a combined 'glazing' entry (mask {3,8,12} vs XML {3,8,12}).
    """
    H, W = mask.shape
    slop = max(1, int(round(slop_frac * W)))
    xml_maps = rasterize_boxes(boxes, H, W)
    result: dict = {"per_class": {}, "slop_px": slop}

    def entry(m: np.ndarray, x: np.ndarray, n_boxes: int) -> dict:
        inter = int((m & x).sum())
        union = int((m | x).sum())
        xd = _dilate(x, slop)
        md = _dilate(m, slop)
        return {
            "mask_px": int(m.sum()),
            "xml_px": int(x.sum()),
            "ratio_xml_over_mask": (x.sum() / m.sum() if m.sum() > 0 else None),
            "iou": inter / union if union > 0 else None,
            "coverage_mask_in_xml": ((m & xd).sum() / m.sum() if m.sum() > 0 else None),
            "coverage_xml_in_mask": ((x & md).sum() / x.sum() if x.sum() > 0 else None),
            "n_xml_boxes": n_boxes,
            "n_mask_components": _components(m),
        }

    for lab in (3, 4, 8, 12):
        m = mask == lab
        x = xml_maps.get(lab, np.zeros((H, W), dtype=bool))
        n_boxes = sum(1 for b in boxes if b[0] == lab)
        result["per_class"][lab] = {"name": CLASS_NAMES[lab], **entry(m, x, n_boxes)}
    mg = np.isin(mask, [3, 8, 12])
    xg = np.zeros((H, W), dtype=bool)
    n_boxes = 0
    for lab in (3, 8, 12):
        xg |= xml_maps.get(lab, np.zeros((H, W), dtype=bool))
        n_boxes += sum(1 for b in boxes if b[0] == lab)
    result["glazing_combined"] = entry(mg, xg, n_boxes)
    return result


# ---------------------------------------------------------------------------
# Dataset sweep + priors
# ---------------------------------------------------------------------------


def iter_facades(root: str | Path):
    """Yield (facade_id, mask_path, xml_path) for all 606 facades."""
    root = Path(root)
    for sub in ("base/base", "extended/extended"):
        d = root / sub
        for png in sorted(d.glob("*.png")):
            fid = png.stem
            xml = d / f"{fid}.xml"
            yield fid, png, (xml if xml.exists() else None)


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def dataset_priors(takeoffs: list[FacadeTakeoff]) -> dict:
    """Distribution stats over WWR, door fraction, opaque fraction, etc.

    These are the plausibility priors the validation layer's sanity guards
    can be calibrated against.
    """

    def col(fn):
        return [v for v in (fn(t) for t in takeoffs) if v is not None]

    def stats(xs: list[float]) -> dict:
        return {
            "n": len(xs),
            "mean": float(np.mean(xs)) if xs else None,
            "median": _pct(xs, 50),
            "p10": _pct(xs, 10),
            "p90": _pct(xs, 90),
            "min": min(xs) if xs else None,
            "max": max(xs) if xs else None,
        }

    return {
        "n_facades": len(takeoffs),
        "wwr": stats(col(lambda t: t.wwr)),
        "door_fraction": stats(col(lambda t: t.frac_door)),
        "opaque_fraction": stats(col(lambda t: t.frac_opaque)),
        "blind_of_glazing": stats(col(lambda t: t.frac_blind_of_glazing)),
        "shop_of_glazing": stats(col(lambda t: t.frac_shop_of_glazing)),
    }


def sweep(
    root: str | Path, with_xml: bool = True
) -> tuple[list[FacadeTakeoff], list[dict], list[str]]:
    """Run takeoffs (+ optional XML agreement) over the whole dataset.

    Returns (takeoffs, agreements, skipped_ids). Never raises on a bad file;
    records the skip instead.
    """
    takeoffs, agreements, skipped = [], [], []
    for fid, png, xml in iter_facades(root):
        try:
            mask = load_mask(png)
            t = facade_takeoff(mask, fid, sheet_id=f"cmp_{fid}")
            takeoffs.append(t)
            if with_xml and xml is not None:
                boxes = parse_xml_boxes(xml)
                ag = xml_agreement(mask, boxes)
                ag["facade_id"] = fid
                agreements.append(ag)
        except Exception as e:  # noqa: BLE001 -- per-file robustness
            skipped.append(f"{fid}: {type(e).__name__}: {e}")
    return takeoffs, agreements, skipped
