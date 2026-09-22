"""Exact window positioning from building elevations.

Motivation: count x schedule gives window AREAS, but two things need
exact window placement on walls:

  1. Visual verification that the BEM matches the elevations.
  2. Daylight-responsive lighting controls: ASHRAE 90.1 sidelighted
     zones derive from window head height and the host wall.

Pipeline:

  detect (elevation raster, sheet px)
    -> register (grid bubbles | geometric facade-corner fallback)
    -> FacadeWindow list in facade meters
    -> DEDUP across elevations of the same facade (interval-overlap
       merge; position conflicts flagged, never silently averaged)
    -> attach to spaces via wall-segment interval matching, WITH exact
       placement: along-wall position s_center_m, sill_m, head_m
    -> RECONCILE elevation instances vs arch-plan tag counts
       (mismatches flagged in the review queue, never dropped)
    -> DAYLIGHTING: per-space primary/secondary sidelighted polygons
       per ASHRAE 90.1 from each window's head height + host wall.

Detection backends: `register_backend(name, backend)` lets a learned
(YOLO) detector replace the classical contour backend later; the rest
of the pipeline only sees ElevationWindowObs in sheet px.

Coordinate note: elevations map (u_px, v_px) -> (s_m along facade,
z_m height above grade) via registration.FacadeRegistration. The
facade origin is the reference corner (west end for south/north,
north end for east/west); see registration.py.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

import link
from building_model import REVIEW_CONFIDENCE, BuildingModel, DaylitZone, Provenance, SpaceOpening
from detector.classes import CLASS_NAMES
from detector.sahi_infer import infer_sheet
from registration import (
    Facade,
    FacadeRegistration,
    interval_overlap,
    match_interval_to_segments,
    register_elevation_geometric,
    register_elevation_grid,
)

# ---------------------------------------------------------------------------
# Detection: sheet-px observations
# ---------------------------------------------------------------------------


@dataclass
class ElevationWindowObs:
    """One detected window rectangle on an elevation raster (sheet px)."""

    id: str
    u0_px: float
    u1_px: float
    v_head_px: float  # smaller v (v grows downward)
    v_sill_px: float  # larger v
    confidence: float
    method: str
    bbox_px: list = field(default_factory=list)  # [u0, v_head, u1, v_sill]


class WindowDetectorBackend:
    """Interface for detection backends. detect() returns observations
    in sheet px; everything downstream is backend-agnostic."""

    def detect(self, image, px_per_m: float, sheet_id: str, revision: int) -> list:
        raise NotImplementedError


class ContourWindowDetector(WindowDetectorBackend):
    """Classical CV backend for clean digital drawings.

    Finds solid 4-vertex convex rectangles of plausible window size.
    Uses the full contour TREE: windows sit *inside* the wall outline's
    hollow interior, so RETR_EXTERNAL would suppress them (a window's
    parent is the wall's inner edge). Even hierarchy depth = ink
    object (wall outer, window, glyph...), odd depth = hole boundary
    (wall inner edge, glyph counters) -- only even depths are
    candidates.

    Rejects: wall outlines (hollow -> low fill ratio; inner edge is
    odd-depth anyway; max-area cap as backstop), grid bubbles
    (ellipses -> not 4-vertex), title/dimension text (too small or not
    rectangular), dimension lines (extreme aspect ratio), mid-window
    mullion lines (interior -> merged into the window component).
    """

    def __init__(
        self,
        min_area_m2: float = 0.4,
        max_area_m2: float = 12.0,
        aspect_range: tuple = (0.3, 4.0),
        min_fill_ratio: float = 0.85,
        confidence: float = 0.9,
    ):
        self.min_area_m2 = min_area_m2
        self.max_area_m2 = max_area_m2
        self.aspect_range = aspect_range
        self.min_fill_ratio = min_fill_ratio
        self.confidence = confidence

    @staticmethod
    def _depths(hier):
        depth = {}

        def d(i):
            if i < 0:
                return -1
            if i not in depth:
                depth[i] = d(hier[i][3]) + 1
            return depth[i]

        for i in range(len(hier)):
            d(i)
        return depth

    def detect(self, image, px_per_m: float, sheet_id: str, revision: int) -> list:
        cv2 = _require_cv2()
        ink = np.ascontiguousarray((image < 128).astype("uint8") * 255)
        contours, hier = cv2.findContours(ink, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        depths = self._depths(hier[0]) if hier is not None else {}
        min_area = self.min_area_m2 * px_per_m**2
        max_area = self.max_area_m2 * px_per_m**2
        obs = []
        for i, c in enumerate(contours):
            if depths.get(i, 0) % 2 == 1:
                continue  # hole boundary, not an ink object
            area = float(cv2.contourArea(c))
            if not (min_area <= area <= max_area):
                continue
            peri = cv2.arcLength(c, True)
            if peri <= 0:
                continue
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            x, y, w, h = cv2.boundingRect(approx)
            if w < 3 or h < 3:
                continue
            ar = w / h
            if not (self.aspect_range[0] <= ar <= self.aspect_range[1]):
                continue
            if area / (w * h) < self.min_fill_ratio:
                continue
            obs.append(
                ElevationWindowObs(
                    id="",
                    u0_px=float(x),
                    u1_px=float(x + w),
                    v_head_px=float(y),
                    v_sill_px=float(y + h),
                    confidence=self.confidence,
                    method="contour_rect",
                    bbox_px=[float(x), float(y), float(x + w), float(y + h)],
                )
            )
        obs.sort(key=lambda o: o.u0_px)
        for i, o in enumerate(obs):
            o.id = f"EW-{i + 1}"
        return obs


class YOLOWindowDetectorBackend(WindowDetectorBackend):
    """Learned YOLO backend using sahi-style tiled inference.

    Calls detector/sahi_infer.py:infer_sheet() internally. Returns
    ElevationWindowObs in sheet px, same contract as ContourWindowDetector.
    """

    def __init__(
        self,
        weights: str | Path = "detector/best.pt",
        conf: float = 0.25,
        iou_thr: float = 0.5,
        tile: int = 1024,
        overlap: float = 0.2,
        imgsz: int = 1024,
        device: str = "cpu",
    ):
        self.weights = Path(weights)
        self.conf = conf
        self.iou_thr = iou_thr
        self.tile = tile
        self.overlap = overlap
        self.imgsz = imgsz
        self.device = device

    def detect(self, image, px_per_m: float, sheet_id: str, revision: int) -> list:
        # image: np.ndarray (H, W) in 0-255 uint8
        # Write temp PNG, run infer_sheet, delete temp
        from PIL import Image

        # Convert grayscale to RGB for YOLO
        if len(image.shape) == 2:
            img_rgb = np.stack([image, image, image], axis=-1)
        else:
            img_rgb = image

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            Image.fromarray(img_rgb).save(tmp.name)
            preds, (W, H) = infer_sheet(
                str(self.weights),
                tmp.name,
                tile=self.tile,
                overlap=self.overlap,
                conf=self.conf,
                iou_thr=self.iou_thr,
                imgsz=self.imgsz,
                device=self.device,
            )
        finally:
            os.unlink(tmp.name)

        # Filter to windows only (cls==1), build ElevationWindowObs
        obs = []
        for i, p in enumerate(preds):
            if CLASS_NAMES[p["cls"]] != "window":
                continue
            x0, y0, x1, y1 = p["x0"], p["y0"], p["x1"], p["y1"]
            obs.append(
                ElevationWindowObs(
                    id=f"EW-YOLO-{i + 1}",
                    u0_px=float(x0),
                    u1_px=float(x1),
                    v_head_px=float(y0),  # smaller v = head
                    v_sill_px=float(y1),  # larger v = sill
                    confidence=float(p["conf"]),
                    method="yolo_sahi",
                    bbox_px=[float(x0), float(y0), float(x1), float(y1)],
                )
            )
        obs.sort(key=lambda o: o.u0_px)
        for i, o in enumerate(obs):
            o.id = f"EW-YOLO-{i + 1}"
        return obs


_BACKENDS = {"contour": ContourWindowDetector(), "yolo": YOLOWindowDetectorBackend()}


def register_backend(name: str, backend: WindowDetectorBackend) -> None:
    """Register a learned (or otherwise new) detector backend."""
    _BACKENDS[name] = backend


def detect_windows(
    image, px_per_m: float, sheet_id: str, revision: int, backend: str = "contour"
) -> list:
    """Run a named detector backend on an elevation raster."""
    if backend not in _BACKENDS:
        raise ValueError(
            f"unknown window detector backend {backend!r}; registered: {sorted(_BACKENDS)}"
        )
    return _BACKENDS[backend].detect(image, px_per_m, sheet_id, revision)


def _require_cv2():
    try:
        import cv2

        return cv2
    except ImportError as e:
        raise RuntimeError(
            "window detection needs OpenCV (cv2); e.g. run with ~/workspace/.venv-ocr/bin/python"
        ) from e


# ---------------------------------------------------------------------------
# Facade-meter mapping
# ---------------------------------------------------------------------------


@dataclass
class FacadeWindow:
    """One window instance located on a facade, in facade meters."""

    id: str  # f"{sheet_id}:{obs_id}"
    sheet_id: str
    facade: str
    s0_m: float
    s1_m: float
    s_center_m: float
    width_m: float
    sill_m: float
    head_m: float
    height_m: float
    category_hint: str  # "window" | "door" (sill ~ 0 -> door)
    confidence: float
    provenance: Provenance = None


def observations_to_facade(obs_list: list, reg: FacadeRegistration, facade: str) -> list:
    """Map sheet-px observations to facade meters via a registration."""
    out = []
    for o in obs_list:
        s0, _ = reg.to_facade(o.u0_px, 0)
        s1, _ = reg.to_facade(o.u1_px, 0)
        _, sill = reg.to_facade(0, o.v_sill_px)
        _, head = reg.to_facade(0, o.v_head_px)
        # guard against inverted detections
        s0, s1 = min(s0, s1), max(s0, s1)
        sill, head = min(sill, head), max(sill, head)
        prov = Provenance(
            sheet_id=reg.sheet_id,
            revision=reg.provenance.revision,
            method=reg.method + "_registration",
            confidence=reg.confidence,
            bbox=o.bbox_px,
            note=(
                f"detected {o.method} {o.id}: facade interval "
                f"[{s0:.2f}, {s1:.2f}] m, sill {sill:.2f} m, "
                f"head {head:.2f} m"
            ),
        )
        out.append(
            FacadeWindow(
                id=f"{reg.sheet_id}:{o.id}",
                sheet_id=reg.sheet_id,
                facade=facade,
                s0_m=s0,
                s1_m=s1,
                s_center_m=(s0 + s1) / 2,
                width_m=s1 - s0,
                sill_m=sill,
                head_m=head,
                height_m=head - sill,
                category_hint="door" if sill < 0.15 else "window",
                confidence=reg.confidence * o.confidence,
                provenance=prov,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Schedule tag association: candidates + prefer strategy
# ---------------------------------------------------------------------------


@dataclass
class WindowTagCandidate:
    """One schedule tag that matches a measured window instance."""

    tag: str
    distance_m: float  # size distance (hypot of width and height delta)
    registration_method: str  # "grid" | "geometric" | "unknown"


def assign_window_tag_candidates(
    width_m: float,
    height_m: float,
    win_sched: dict,
    tol_m: float = 0.15,
    registration_method: str = "unknown",
) -> list[WindowTagCandidate]:
    """Return all schedule tags matching measured size within tol_m.

    Returns candidates sorted by distance_m ascending. Empty list means no
    match within tolerance -- the instance is kept as UNTAGGED with
    measured dims, flagged for review at attach time, never dropped.
    """
    candidates = []
    for tag, e in win_sched.items():
        if e.width_m is None or e.height_m is None:
            continue
        d = math.hypot(width_m - e.width_m, height_m - e.height_m)
        if d <= tol_m:
            candidates.append(
                WindowTagCandidate(tag=tag, distance_m=d, registration_method=registration_method)
            )
    candidates.sort(key=lambda c: c.distance_m)
    return candidates


def same_object(a: WindowTagCandidate, b: WindowTagCandidate) -> bool:
    """Return True if two candidates refer to the same schedule entry.

    Uses tag identity: same tag means same object.
    """
    return a.tag == b.tag


def prefer_grid(a: WindowTagCandidate, b: WindowTagCandidate) -> WindowTagCandidate:
    """Prefer grid-registered observation over geometric."""
    if a.registration_method == "grid" and b.registration_method != "grid":
        return a
    if b.registration_method == "grid" and a.registration_method != "grid":
        return b
    if a.distance_m < b.distance_m:
        return a
    return b


def prefer_geometric(a: WindowTagCandidate, b: WindowTagCandidate) -> WindowTagCandidate:
    """Prefer geometric-registered observation over grid."""
    if a.registration_method == "geometric" and b.registration_method != "geometric":
        return a
    if b.registration_method == "geometric" and a.registration_method != "geometric":
        return b
    if a.distance_m < b.distance_m:
        return a
    return b


def keep_both(
    a: WindowTagCandidate, b: WindowTagCandidate
) -> tuple[WindowTagCandidate, WindowTagCandidate] | WindowTagCandidate:
    """Return both candidates when they refer to different objects."""
    if not same_object(a, b):
        return (a, b)
    return prefer_grid(a, b)


def adjudicate_tag(candidates: list[WindowTagCandidate], prefer: str = "grid") -> Optional[str]:
    """Choose a single tag from candidates using the prefer strategy.

    prefer: "grid" | "geometric" | "keep_both" | "nearest"
      grid       -- prefer grid-registered; ties go to nearest size
      geometric  -- prefer geometric-registered; ties go to nearest size
      keep_both  -- if candidates disagree on identity (different tags),
                   return all as comma-joined string; if same tag, return it
      nearest    -- original behavior: closest by size distance

    Returns None if no candidates, else the chosen tag string.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].tag

    if prefer == "nearest":
        return candidates[0].tag

    if prefer == "keep_both":
        chosen = candidates[0]
        for c in candidates[1:]:
            result = keep_both(chosen, c)
            if isinstance(result, tuple):
                tags = sorted(set(result[0].tag, result[1].tag))
                return ",".join(tags)
            chosen = result
        return chosen.tag

    if prefer == "grid":
        best = candidates[0]
        for c in candidates[1:]:
            best = prefer_grid(best, c)
        return best.tag

    if prefer == "geometric":
        best = candidates[0]
        for c in candidates[1:]:
            best = prefer_geometric(best, c)
        return best.tag

    return candidates[0].tag


# Backward-compatibility alias: original nearest-tag-within-0.15m behavior
def assign_window_tag(
    width_m: float, height_m: float, win_sched: dict, tol_m: float = 0.15
) -> Optional[str]:
    """Associate a measured instance with a schedule tag by size.

    Returns the tag whose (width, height) is nearest within tol_m, else
    None (kept as an untagged instance with measured dims -- flagged
    for review at attach time, never dropped).
    """
    cands = assign_window_tag_candidates(width_m, height_m, win_sched, tol_m)
    return adjudicate_tag(cands, prefer="nearest")


# ---------------------------------------------------------------------------
# Dedup: merge observations of one facade across elevations
# ---------------------------------------------------------------------------


@dataclass
class MergedWindow:
    """One physical window merged from >= 1 elevation observations."""

    id: str
    facade: str
    s0_m: float
    s1_m: float
    s_center_m: float
    width_m: float
    sill_m: float
    head_m: float
    height_m: float
    tag: Optional[str]
    category_hint: str
    sources: list = field(default_factory=list)  # [(sheet_id, obs_id)]
    confidence: float = 0.0
    single_source: bool = False
    provenance: Provenance = None


def _overlap_frac(a: FacadeWindow, b: FacadeWindow) -> float:
    ov = interval_overlap(a.s0_m, a.s1_m, b.s0_m, b.s1_m)
    return ov / max(min(a.width_m, b.width_m), 1e-9)


def merge_elevation_observations(
    fw_lists: list, tol_m: float = 0.30, min_overlap_frac: float = 0.5
) -> tuple:
    """Merge per-elevation FacadeWindow lists into physical windows.

    Two observations merge when their along-wall intervals overlap by
    >= min_overlap_frac of the narrower AND their centers agree within
    tol_m. Overlapping-but-disagreeing pairs are CONFLICTS: kept
    separate and returned for review (never silently averaged).

    Returns (merged: [MergedWindow], conflicts: [dict]).
    """
    pool = [w for lst in fw_lists for w in lst]
    pool.sort(key=lambda w: w.s_center_m)
    used = set()
    merged, conflicts = [], []
    mid = 0

    for i, w in enumerate(pool):
        if i in used:
            continue
        used.add(i)
        group = [w]
        for j in range(i + 1, len(pool)):
            if j in used:
                continue
            v = pool[j]
            if v.s0_m - w.s1_m > tol_m:
                break  # sorted: no later window can overlap
            if v.sheet_id == w.sheet_id:
                continue  # same sheet: distinct instances, not dupes
            if _overlap_frac(w, v) < min_overlap_frac:
                continue
            dc = abs(v.s_center_m - w.s_center_m)
            if dc > tol_m:
                conflicts.append(
                    {
                        "a": w.id,
                        "b": v.id,
                        "delta_center_m": round(dc, 3),
                        "note": (f"same facade interval, centers disagree beyond {tol_m} m"),
                    }
                )
                continue
            group.append(v)
            used.add(j)
        mid += 1
        cw = sum(g.confidence for g in group)
        s0 = sum(g.s0_m * g.confidence for g in group) / cw
        s1 = sum(g.s1_m * g.confidence for g in group) / cw
        sill = sum(g.sill_m * g.confidence for g in group) / cw
        head = sum(g.head_m * g.confidence for g in group) / cw
        sheets = sorted({g.sheet_id for g in group})
        prov = Provenance(
            sheet_id="+".join(sheets),
            revision=0,
            method="elevation_dedup",
            confidence=round(sum(g.confidence for g in group) / len(group), 3),
            note=(
                f"merged {len(group)} observation(s) from "
                f"{sheets}; center spread "
                f"{max(g.s_center_m for g in group) - min(g.s_center_m for g in group):.3f} m"
            ),
        )
        merged.append(
            MergedWindow(
                id=f"MW-{mid}",
                facade=w.facade,
                s0_m=s0,
                s1_m=s1,
                s_center_m=(s0 + s1) / 2,
                width_m=s1 - s0,
                sill_m=sill,
                head_m=head,
                height_m=head - sill,
                tag=None,
                category_hint=w.category_hint,
                sources=[(g.sheet_id, g.id) for g in group],
                confidence=round(sum(g.confidence for g in group) / len(group), 3),
                single_source=len(group) == 1,
                provenance=prov,
            )
        )
    merged.sort(key=lambda m: m.s_center_m)
    for k, m in enumerate(merged, 1):
        m.id = f"MW-{k}"
    return merged, conflicts


# ---------------------------------------------------------------------------
# Attach merged windows to spaces (with exact placement)
# ---------------------------------------------------------------------------


@dataclass
class ElevationLinkReport:
    building_id: str
    per_elevation: dict = field(default_factory=dict)  # key -> {detected, sheet_id, method}
    n_merged: int = 0
    n_conflicts: int = 0
    n_attached: int = 0
    n_unlinked: int = 0
    n_reconcile_flags: int = 0
    review_items: int = 0


def _facade_from_meta(meta: dict, D: float, W: float) -> Facade:
    name = meta.get("facade", "south")
    ref = tuple(meta.get("facade_ref_corner_m", [0.0, D]))
    return Facade(
        name=name,
        ref_corner_m=ref,
        length_m=meta.get("facade_length_m", W),
        fixed_coord_m=D if name in ("south", "north") else W,
        axis="x" if name in ("south", "north") else "y",
    )


def attach_merged_windows(
    model: BuildingModel, spaces: list, bldg: dict, merged: list, win_sched: dict, facade: str
) -> tuple:
    """Attach merged windows to rooms; exact placement on every opening.

    Returns (attached, unlinked). Unlinked windows are flagged for
    review, never dropped.
    """
    segments = link.south_wall_segments(bldg)  # v1: south facade
    space_of_num = {s.number: s for s in spaces}
    attached, unlinked = [], []
    for m in merged:
        m.tag = assign_window_tag(m.width_m, m.height_m, win_sched)
        seg, frac, ambiguous = match_interval_to_segments(m.s0_m, m.s1_m, segments)
        conf = m.confidence * (0.5 + 0.5 * frac)
        if m.tag is None:
            conf *= 0.8  # untagged: measured dims only
        prov = Provenance(
            sheet_id=m.provenance.sheet_id,
            revision=1,
            method="elevation_instance_detection",
            confidence=round(conf, 3),
            note=(
                f"merged {m.id} {m.sources}: interval "
                f"[{m.s0_m:.2f}, {m.s1_m:.2f}] m center "
                f"{m.s_center_m:.2f} m, sill {m.sill_m:.2f} m, head "
                f"{m.head_m:.2f} m; tag "
                f"{m.tag or 'UNTAGGED (measured dims)'}; segment "
                f"{seg['id'] if seg else None} overlap {frac:.0%}"
                + (" AMBIGUOUS" if ambiguous else "")
                + (" SINGLE-SOURCE" if m.single_source else "")
            ),
        )
        if seg is None:
            unlinked.append(m)
            model.flag_for_review(
                "window_room_link",
                f"window {m.id} at [{m.s0_m:.2f}, {m.s1_m:.2f}] m matches no wall segment",
                conf,
                prov,
            )
            continue
        sp = space_of_num[seg["room_number"]]
        entry = win_sched.get(m.tag) if m.tag else None
        width_m = entry.width_m if entry else round(m.width_m, 3)
        height_m = entry.height_m if entry else round(m.height_m, 3)
        needs_review = ambiguous or conf < REVIEW_CONFIDENCE or m.tag is None
        sp.openings.append(
            SpaceOpening(
                id=f"{facade}-{m.id}",
                tag=m.tag or "",
                category="window",
                width_m=width_m,
                height_m=height_m,
                sill_m=round(m.sill_m, 3),
                head_m=round(m.head_m, 3),
                host_facade=facade,
                host_interval_m=[round(m.s0_m, 3), round(m.s1_m, 3)],
                s_center_m=round(m.s_center_m, 3),
                area_m2=round(width_m * height_m, 4),
                provenance=prov,
                needs_review=needs_review,
            )
        )
        attached.append((m, sp.number))
        if needs_review:
            model.flag_for_review(
                "window_room_link",
                f"window {m.id} -> room {sp.number}: "
                f"{'ambiguous span; ' if ambiguous else ''}"
                f"{'untagged; ' if m.tag is None else ''}"
                f"confidence {conf:.2f}",
                conf,
                prov,
            )
    return attached, unlinked


# ---------------------------------------------------------------------------
# Reconciliation: elevation instances vs arch-plan tag counts
# ---------------------------------------------------------------------------


def reconcile_window_counts(
    model: BuildingModel, plan_counts: dict, merged: list, facade: str, plan_sheet_id: str
) -> list:
    """Elevation instances should agree with arch-plan tag counts.

    plan_counts: {tag: n} from the arch plan (per facade).
    Returns a list of discrepancy dicts; each also becomes a review
    item. Nothing is silently dropped.
    """
    elev_counts = Counter(m.tag if m.tag else "UNTAGGED" for m in merged)
    flags = []
    for tag in sorted(set(plan_counts) | set(elev_counts)):
        pc, ec = plan_counts.get(tag, 0), elev_counts.get(tag, 0)
        if ec == pc:
            continue
        if ec > pc:
            desc = (
                f"{facade} facade: {ec - pc} '{tag}' window instance(s) "
                f"on elevation(s) with no matching arch-plan tag "
                f"(elev {ec} vs plan {pc})"
            )
        else:
            desc = (
                f"{facade} facade: {pc - ec} '{tag}' arch-plan tag(s) "
                f"with no elevation instance "
                f"(plan {pc} vs elev {ec})"
            )
        prov = Provenance(
            sheet_id=plan_sheet_id,
            revision=1,
            method="window_reconciliation",
            confidence=0.7,
            note=desc,
        )
        model.flag_for_review("window_reconciliation", desc, 0.7, prov)
        flags.append({"tag": tag, "plan": pc, "elevation": ec, "description": desc})
    return flags


# ---------------------------------------------------------------------------
# Daylighting: ASHRAE 90.1 sidelighted zones from exact window placement
# ---------------------------------------------------------------------------


@dataclass
class DaylightParams:
    """Parameterization of the sidelighted-area geometry.

    ASHRAE 90.1-2019 Section 3.2 (definitions, informative here):
      primary sidelighted area: adjacent to vertical fenestration,
        depth = window head height; lateral extent = window width +
        one head height on each side;
      secondary sidelighted area: the band from one to two head
        heights from the window wall.
    Lateral/depth extents are clipped to the room polygon. Full-height
    interior partitions (which truncate the zones in the real standard)
    are NOT modeled in v1 -- zones may be optimistic in partitioned
    rooms. Toplighting (skylights) is out of scope.
    """

    standard: str = "ASHRAE 90.1-2019 sidelighted-area geometry (approx, partitions not modeled)"
    primary_depth_factor: float = 1.0  # x window head height
    secondary_depth_factor: float = 2.0  # x window head height
    lateral_factor: float = 1.0  # x head height, each side


def _facade_frame(facade: str, W: float, D: float):
    """(ref_corner, lateral_dir, inward_dir, wall_coord_fn) in y-down m."""
    if facade == "south":
        return (0.0, D), (1.0, 0.0), (0.0, -1.0)
    if facade == "north":
        return (0.0, 0.0), (1.0, 0.0), (0.0, 1.0)
    if facade == "east":
        return (W, 0.0), (0.0, 1.0), (-1.0, 0.0)
    if facade == "west":
        return (0.0, 0.0), (0.0, 1.0), (1.0, 0.0)
    raise ValueError(f"unknown facade {facade!r}")


def _clip_polygon(subject: list, clip: list) -> list:
    """Sutherland-Hodgman clip of subject against a CONVEX clip polygon.

    Both as [[x, y], ...] in y-down meters. Interior side of each clip
    edge is determined from the clip polygon's signed shoelace area
    (area > 0 in y-down = visually clockwise = interior right of edge).
    """
    area = sum(
        clip[i][0] * clip[(i + 1) % len(clip)][1] - clip[(i + 1) % len(clip)][0] * clip[i][1]
        for i in range(len(clip))
    )
    cw = area > 0

    def inside(p, a, b):
        cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        return cross >= -1e-9 if cw else cross <= 1e-9

    def intersect(p1, p2, a, b):
        d1 = (p2[0] - p1[0], p2[1] - p1[1])
        d2 = (b[0] - a[0], b[1] - a[1])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-12:
            return p1
        t = ((a[0] - p1[0]) * d2[1] - (a[1] - p1[1]) * d2[0]) / den
        return [p1[0] + t * d1[0], p1[1] + t * d1[1]]

    out = [list(p) for p in subject]
    n = len(clip)
    for i in range(n):
        a, b = clip[i], clip[(i + 1) % n]
        if not out:
            break
        inp = out
        out = []
        s = inp[-1]
        for e in inp:
            e_in, s_in = inside(e, a, b), inside(s, a, b)
            if e_in:
                if not s_in:
                    out.append(intersect(s, e, a, b))
                out.append(e)
            elif s_in:
                out.append(intersect(s, e, a, b))
            s = e
    return out


def _poly_area(poly: list) -> float:
    if len(poly) < 3:
        return 0.0
    return (
        abs(
            sum(
                poly[i][0] * poly[(i + 1) % len(poly)][1]
                - poly[(i + 1) % len(poly)][0] * poly[i][1]
                for i in range(len(poly))
            )
        )
        / 2
    )


def compute_daylit_zones(space, W: float, D: float, params: DaylightParams = None) -> None:
    """Compute primary/secondary sidelighted polygons for one space.

    Uses each window opening's exact placement (host_interval_m,
    head_m) on its host facade. Zones are clipped to the room polygon
    so they never leak into neighboring rooms.
    """
    from building_model import SpaceDaylight  # local: avoids circulars

    params = params or DaylightParams()
    dl = SpaceDaylight(
        params_note=(
            f"{params.standard}: primary "
            f"{params.primary_depth_factor}xH, secondary "
            f"{params.secondary_depth_factor}xH, lateral "
            f"{params.lateral_factor}xH/side"
        ),
        provenance=Provenance(
            sheet_id=",".join(
                sorted({o.provenance.sheet_id for o in space.openings if o.provenance})
            ),
            revision=1,
            method="daylit_zone_calc",
            confidence=min(
                [o.provenance.confidence for o in space.openings if o.provenance] or [1.0]
            ),
            note=f"{len(space.openings)} window(s)",
        ),
    )
    poly = [list(p) for p in space.polygon_m]
    if len(poly) < 3:
        space.daylight = dl
        return
    for o in space.openings:
        if o.category != "window" or not o.host_interval_m:
            continue
        H = (o.head_m - o.sill_m) if (o.head_m and o.sill_m) else None
        if not H or H <= 0:
            H = o.height_m or 1.5
        ref, lat, inw = _facade_frame(o.host_facade, W, D)

        def wall_pt(s, t):
            return [ref[0] + lat[0] * s + inw[0] * t, ref[1] + lat[1] * s + inw[1] * t]

        # room lateral extent + inward depth along this facade
        s_vals, depths = [], []
        for v in poly:
            sv = (v[0] - ref[0]) * lat[0] + (v[1] - ref[1]) * lat[1]
            s_vals.append(sv)
            wp = [ref[0] + lat[0] * sv, ref[1] + lat[1] * sv]
            depths.append((v[0] - wp[0]) * inw[0] + (v[1] - wp[1]) * inw[1])
        rl0, rl1 = min(s_vals), max(s_vals)
        room_depth = max(depths)
        s0, s1 = o.host_interval_m
        lat0 = max(s0 - params.lateral_factor * H, rl0)
        lat1 = min(s1 + params.lateral_factor * H, rl1)
        if lat1 <= lat0 or room_depth <= 0:
            continue
        dp = min(params.primary_depth_factor * H, room_depth)
        ds = min(params.secondary_depth_factor * H, room_depth)
        bands = [("primary", 0.0, dp)]
        if ds > dp + 1e-9:
            bands.append(("secondary", dp, ds))
        for zc, t0, t1 in bands:
            pre = [wall_pt(lat0, t0), wall_pt(lat1, t0), wall_pt(lat1, t1), wall_pt(lat0, t1)]
            clipped = _clip_polygon(pre, poly)
            area = _poly_area(clipped)
            if area <= 1e-9:
                continue
            zone = DaylitZone(
                id=f"{space.id}-DL-{o.id}-{zc[0].upper()}",
                zone_class=zc,
                window_id=o.id,
                polygon_m=[[round(x, 3), round(y, 3)] for x, y in clipped],
                area_m2=round(area, 4),
                head_height_m=round(H, 3),
                provenance=Provenance(
                    sheet_id=o.provenance.sheet_id if o.provenance else "",
                    revision=1,
                    method="daylit_zone_calc",
                    confidence=(o.provenance.confidence if o.provenance else 1.0),
                    note=(
                        f"{zc} sidelighted: {t0:.2f}-{t1:.2f} m from "
                        f"{o.host_facade} wall, H={H:.2f} m"
                    ),
                ),
            )
            (dl.primary if zc == "primary" else dl.secondary).append(zone)
    space.daylight = dl


# ---------------------------------------------------------------------------
# Top-level: multi-elevation linking with dedup
# ---------------------------------------------------------------------------


def link_elevations(
    model: BuildingModel,
    bldg: dict,
    spaces: list,
    elevation_keys: tuple,
    win_sched: dict,
    detector_backend: str = "contour",
    daylight_params: DaylightParams = None,
    dedup_tol_m: float = 0.30,
) -> ElevationLinkReport:
    """Detect -> register -> dedup -> attach -> reconcile -> daylight.

    elevation_keys: e.g. ("elev_grid", "elev_nogrid"). All elevations
    must show the SAME facade (v1).
    """
    W, D = bldg["W_m"], bldg["D_m"]
    report = ElevationLinkReport(building_id=bldg["building_id"])
    fw_lists, facades = [], []
    for key in elevation_keys:
        sh = bldg["sheets"][key]
        meta, data = sh["meta"], sh["data"]
        facade = _facade_from_meta(meta, D, W)
        facades.append(facade.name)
        obs = detect_windows(
            sh["image"],
            data["px_per_m"],
            meta["sheet_id"],
            meta["revision"],
            backend=detector_backend,
        )
        if data.get("bubbles"):
            reg = register_elevation_grid(
                meta["sheet_id"],
                facade,
                plan_grid_m=bldg["grids_v"],
                elev_bubbles=data["bubbles"],
                v_ground_px=data["v_ground_px"],
                elev_px_per_m=data["px_per_m"],
                revision=meta["revision"],
            )
            method = "grid"
        else:
            reg = register_elevation_geometric(
                meta["sheet_id"],
                facade,
                wall_u0_px=data["wall_u0_px"],
                elev_px_per_m=data["px_per_m"],
                v_ground_px=data["v_ground_px"],
                revision=meta["revision"],
            )
            method = "geometric"
        fw = observations_to_facade(obs, reg, facade.name)
        fw_lists.append(fw)
        report.per_elevation[key] = {
            "sheet_id": meta["sheet_id"],
            "method": method,
            "detected": len(obs),
        }
        model.log_revision(
            meta["sheet_id"],
            meta["revision"],
            "ingest",
            f"{len(obs)} window instances detected ({detector_backend}) via {method} path",
        )
    if len(set(facades)) != 1:
        raise ValueError(f"v1 needs one facade across elevations, got {facades}")
    facade_name = facades[0]

    merged, conflicts = merge_elevation_observations(fw_lists, tol_m=dedup_tol_m)
    report.n_merged = len(merged)
    report.n_conflicts = len(conflicts)
    for c in conflicts:
        model.flag_for_review(
            "elevation_conflict",
            f"facade {facade_name}: observations {c['a']} vs {c['b']} "
            f"overlap but centers disagree ({c['note']})",
            0.5,
            Provenance(
                sheet_id=",".join(elevation_keys),
                revision=1,
                method="elevation_dedup",
                confidence=0.5,
                note=c["note"],
            ),
        )

    attached, unlinked = attach_merged_windows(model, spaces, bldg, merged, win_sched, facade_name)
    report.n_attached = len(attached)
    report.n_unlinked = len(unlinked)

    plan_counts = Counter(w["tag"] for w in bldg.get("south_windows", []))
    flags = reconcile_window_counts(
        model, dict(plan_counts), merged, facade_name, bldg["sheets"]["arch"]["meta"]["sheet_id"]
    )
    report.n_reconcile_flags = len(flags)

    for sp in spaces:
        compute_daylit_zones(sp, W, D, daylight_params)

    model.log_revision(
        "link_elevations",
        1,
        "relink",
        f"{len(merged)} merged windows -> "
        f"{len(attached)} attached, {len(unlinked)} "
        f"unlinked, {len(conflicts)} conflicts, "
        f"{len(flags)} reconciliation flags",
    )
    report.review_items = len(model.review_queue)
    return report


def build_model_with_elevations(
    bldg: dict,
    elevation_keys: tuple = ("elev_grid", "elev_nogrid"),
    building_name: str = "",
    detector_backend: str = "contour",
    daylight_params: DaylightParams = None,
) -> tuple:
    """Canonical model with exact window placement from elevations.

    Arch/lighting/mech link exactly as link.build_model; elevations go
    through detect -> dedup -> attach -> reconcile -> daylighting
    (link_elevations) instead of the single-elevation GT path.
    """
    model, _ = link.build_model(bldg, elevation_key=None, building_name=building_name)
    spaces = list(model.spaces.values())
    win_sched, _ = link._schedules(bldg)
    report = link_elevations(
        model, bldg, spaces, elevation_keys, win_sched, detector_backend, daylight_params
    )
    return model, report
