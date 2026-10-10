"""Door/window detection behind a provider chosen by config (#743, ROADMAP R2).

A ``DetectionProvider`` takes one sheet image and returns
``datasets_adapter.Detection`` rows (label, score, pixel bbox, source), the
shape the takeoff join already reads, so the rest of the pipeline does not care
which backend ran. Which provider runs is a config value, not code:

    {"provider": "precomputed", "dir": "dets/", "artifact": "<ledger path>"}
    {"provider": "yolo_sahi", "weights": "best.pt", "artifact": "<ledger path>"}
    {"provider": "door_swing"}
    {"provider": "vector_glazing"}
    {"provider": "combined", "providers": [{"provider": "door_swing"},
                                           {"provider": "vector_glazing"}]}
    {"provider": "none"}

``door_swing`` finds door swings (leaf plus quarter arc) by rule
(``door_detect.py``): no training data and no weights, so it may ship. It
needs the sheet's scale, which the set reader passes per sheet; a fixed
``px_per_m`` in the config is used when the caller has none.
``vector_glazing`` reports the windows the wall reader found in the sheet's
vector glazing (``plan_walls``); the set reader hands it each sheet's walls.

Every provider names the trained artifact behind it. Its license class comes
from ``license_ledger.json`` (#751): weights trained on non-commercial or
copyleft data, or weights the ledger does not list, are evaluation only. Their
detections are marked so in the set report, and ``release=True`` refuses them.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from datasets_adapter import Detection, detections_from_yolo_json

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "license_ledger.json"
SHIPPABLE = ("permissive", "share_alike")  # share-alike ships with its notice
_RANK = {"permissive": 0, "share_alike": 1, "copyleft": 2, "noncommercial": 3, "unknown": 4}


class ProviderConfigError(ValueError):
    """The detector config names no known provider or misses a required key."""


class ProviderLicenseError(RuntimeError):
    """An evaluation-only provider was asked to run for a release."""


def _ledger_artifact(ledger: dict, artifact: str) -> Optional[dict]:
    if not artifact:
        return None
    for a in ledger.get("artifacts", []):
        ap = a["path"]
        pats = {ap, os.path.expanduser(ap)}
        if ap.startswith("~/"):  # the ledger's home-relative paths match under any home
            pats.add("*/" + ap[2:])
        for pat in pats:
            if artifact == pat or artifact.endswith("/" + pat) or fnmatch.fnmatch(artifact, pat):
                return a
    return None


def license_class(artifact: str, ledger_path: Union[str, Path] = LEDGER) -> str:
    """Worst license class of the datasets behind ``artifact``, or "unknown"."""
    ledger = json.loads(Path(ledger_path).read_text())
    art = _ledger_artifact(ledger, artifact)
    if art is None:
        return "unknown"
    ds = {d["id"]: d["class"] for d in ledger.get("datasets", [])}
    classes = [ds.get(i, "unknown") for i in art.get("datasets", [])]
    return max(classes, key=lambda c: _RANK.get(c, 4)) if classes else "unknown"


@dataclass
class ProviderInfo:
    """What the set report records about the detector that ran."""

    provider: str
    artifact: str
    license_class: str
    eval_only: bool

    def note(self) -> str:
        if self.provider == "none":
            return "no detector configured"
        why = (
            "the license ledger does not list it"
            if self.license_class == "unknown"
            else f"trained on {self.license_class} data"
        )
        return (
            f"{self.provider} ({self.artifact or 'no artifact named'}): evaluation only, {why}"
            if self.eval_only
            else f"{self.provider} ({self.artifact}): {self.license_class}, may ship"
        )


class DetectionProvider:
    """Base provider: one sheet image in, typed detections out."""

    name = "base"

    def __init__(self, artifact: str = "", ledger_path: Union[str, Path] = LEDGER):
        self.artifact = artifact or ""
        lc = license_class(self.artifact, ledger_path) if self.artifact else "unknown"
        self.info = ProviderInfo(self.name, self.artifact, lc, lc not in SHIPPABLE)

    def detect(self, image_path: Union[str, Path], sheet_id: str) -> List[Detection]:
        raise NotImplementedError


class NoneProvider(DetectionProvider):
    """No detector: every sheet gets no detections (the default)."""

    name = "none"

    def __init__(self, **_kw):
        self.artifact = ""
        self.info = ProviderInfo("none", "", "permissive", False)

    def detect(self, image_path, sheet_id):
        return []


class PrecomputedProvider(DetectionProvider):
    """Reads ``<dir>/<image stem>.json`` written by detector/sahi_infer.py.

    A sheet with no file gets no detections; the caller reports it as such.
    """

    name = "precomputed"

    def __init__(self, dir: Union[str, Path], artifact: str = "", **kw):
        super().__init__(artifact, **kw)
        self.dir = Path(dir)

    def has(self, image_path) -> bool:
        return (self.dir / f"{Path(image_path).stem}.json").exists()

    def detect(self, image_path, sheet_id):
        p = self.dir / f"{Path(image_path).stem}.json"
        return detections_from_yolo_json(p, sheet_id) if p.exists() else []


class YoloSahiProvider(DetectionProvider):
    """Tiled YOLO inference (detector/sahi_infer.py). Needs ultralytics; CPU by default."""

    name = "yolo_sahi"

    def __init__(
        self,
        weights: Union[str, Path],
        artifact: str = "",
        tile: int = 1024,
        overlap: float = 0.2,
        conf: float = 0.25,
        device: str = "cpu",
        **kw,
    ):
        super().__init__(artifact or str(weights), **kw)
        self.weights = str(weights)
        self.opts = {"tile": tile, "overlap": overlap, "conf": conf, "device": device}

    def detect(self, image_path, sheet_id):
        import tempfile

        from detector.sahi_infer import infer_sheet

        preds, (w, h) = infer_sheet(self.weights, str(image_path), **self.opts)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"image": str(image_path), "width": w, "height": h, "preds": preds}, f)
        try:
            return detections_from_yolo_json(f.name, sheet_id)
        finally:
            os.unlink(f.name)


class DoorSwingProvider(DetectionProvider):
    """Door swings found by rule in the rendered sheet (``door_detect.py``).

    No dataset and no trained weights sit behind it, so its license class is
    permissive. A door counts only when the leaf, the arc and both jambs are
    drawn; windows are not found. Without a scale it reports nothing rather
    than guess a door width.

    It also takes the sheet's walls result (``needs_walls``) when the set
    reader has one: the wall reader's door-wide gaps in partitions too thin to
    be walls (``thin_gaps``) let a door hung in one through (#874). Without
    them, a door needs a jamb wall at least ``MIN_WALL_M`` thick, as before.
    """

    name = "door_swing"
    needs_scale = True
    needs_walls = True

    def __init__(
        self,
        px_per_m: Optional[float] = None,
        ink_max: Optional[int] = None,
        min_width_m: Optional[float] = None,
        max_width_m: Optional[float] = None,
    ):
        # rules, not weights: nothing for the license ledger to look up, so
        # the class is set here (permissive) rather than read from it
        self.artifact = "door_detect.py"
        self.info = ProviderInfo(self.name, self.artifact, "permissive", False)
        self.px_per_m = px_per_m
        self.opts = {
            k: v
            for k, v in (
                ("ink_max", ink_max),
                ("min_width_m", min_width_m),
                ("max_width_m", max_width_m),
            )
            if v is not None
        }

    def detect(
        self,
        image_path,
        sheet_id,
        px_per_m: Optional[float] = None,
        plan: Optional[dict] = None,
    ):
        import numpy as np
        from PIL import Image

        from door_detect import detect_door_swings

        ppm = px_per_m or self.px_per_m
        if not ppm:
            return []
        gray = np.asarray(Image.open(image_path).convert("L"))
        ops = thin_gap_segments(plan)
        opts = {**self.opts, "openings_px": ops} if ops else self.opts
        return [
            Detection(
                label="door",
                tag="",
                score=float(d["score"]),
                bbox=tuple(float(v) for v in d["bbox_px"]),
                source=sheet_id,
            )
            for d in detect_door_swings(gray, float(ppm), **opts)
        ]


def thin_gap_segments(plan: Optional[dict]) -> List[tuple]:
    """The wall reader's ``thin_gaps`` as ((ax, ay), (bx, by)) in rendered
    pixels (#874); empty without a walls result or a scale.

    Same frame as ``window_detections``: ``plan_walls`` metres are y-up from
    the sheet's bottom edge, the rendered sheet is y-down at ``px_per_pt``.
    """
    if not plan:
        return []
    walls = plan.get("walls") or {}
    m = walls.get("m_per_pt")
    if not m:
        return []
    H, ppt = float(plan["height_pt"]), float(plan["px_per_pt"])

    def px(p):
        return (p[0] / m * ppt, (H - p[1] / m) * ppt)

    return [(px(g["a_m"]), px(g["b_m"])) for g in walls.get("thin_gaps", [])]


class VectorGlazingProvider(DetectionProvider):
    """Windows read from the sheet's vector glazing by the wall reader.

    ``plan_walls`` marks a glazing line drawn inside a wall run, a storefront
    run and a glazing-layer band as ``kind: "window"`` openings. This provider
    turns each into a window detection: the opening's span, widened by its
    wall's thickness, in rendered pixels. Rules, not weights, so it may ship.
    It needs the sheet's walls result (``needs_walls``); the set reader passes
    it per sheet. Doors are not reported here; ``door_swing`` finds those.
    """

    name = "vector_glazing"
    needs_walls = True

    def __init__(self):
        self.artifact = "plan_walls.py"
        self.info = ProviderInfo(self.name, self.artifact, "permissive", False)

    def detect(self, image_path, sheet_id, plan: Optional[dict] = None):
        if not plan:
            return []
        return window_detections(plan["walls"], plan["height_pt"], plan["px_per_pt"], sheet_id)


def window_detections(
    walls: dict, height_pt: float, px_per_pt: float, source: str
) -> List[Detection]:
    """Window openings of one ``plan_walls`` result as pixel-box detections.

    ``plan_walls`` metres are y-up from the sheet's bottom edge
    (``x_m = x_pt * m_per_pt``, ``y_m = (H - y_pt) * m_per_pt``); the rendered
    sheet is y-down at ``px_per_pt``. The box spans the opening and half its
    wall's thickness either side of the centreline.
    """
    m = walls.get("m_per_pt")
    if not m:
        return []
    thick = {w["id"]: w.get("thickness_m", 0.0) for w in walls.get("walls", [])}

    def px(p):
        return (p[0] / m * px_per_pt, (height_pt - p[1] / m) * px_per_pt)

    out = []
    for op in walls.get("openings", []):
        if op.get("kind") != "window":
            continue
        ids = op.get("walls") or []
        pad = max((thick.get(i, 0.0) for i in ids), default=0.0) / 2 / m * px_per_pt
        for a, b in _glass_pieces(op):
            (ax, ay), (bx, by) = px(a), px(b)
            out.append(
                Detection(
                    label="window",
                    tag=str(op.get("tag_text") or ""),
                    score=float(op.get("window_confidence", 0.5)),
                    bbox=(
                        min(ax, bx) - pad,
                        min(ay, by) - pad,
                        max(ax, bx) + pad,
                        max(ay, by) + pad,
                    ),
                    source=source,
                )
            )
    return out


MIN_GLASS_PIECE_M = 0.10  # glass left beside a door narrower than this is a frame, not a window


def _glass_pieces(op: dict) -> List[tuple]:
    """The glass of one window opening, less any doors drawn inside it (#793).

    A storefront run that holds a door is one ``kind: "window"`` opening with
    the door listed under ``doors_in_glazing``. The door is not glass, and
    ``door_swing`` reports it on its own, so the window is cut into the pieces
    of glass either side. Ends are in metres along the run; a piece shorter
    than ``MIN_GLASS_PIECE_M`` is dropped.
    """
    a, b = op["a_m"], op["b_m"]
    doors = op.get("doors_in_glazing") or []
    length = math.dist(a, b)
    if not doors or length <= 0:
        return [(tuple(a), tuple(b))]
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length

    def t(p):
        return (p[0] - a[0]) * ux + (p[1] - a[1]) * uy

    cuts = []
    for d in doors:
        lo, hi = sorted((t(d["a_m"]), t(d["b_m"])))
        lo, hi = max(0.0, lo), min(length, hi)
        if hi > lo:  # a door off the run's ends cuts nothing
            cuts.append((lo, hi))
    cuts.sort()
    pieces, start = [], 0.0
    for lo, hi in cuts + [(length, length)]:
        if lo - start >= MIN_GLASS_PIECE_M:
            pieces.append((start, lo))
        start = max(start, hi)

    def at(s):
        return (a[0] + ux * s, a[1] + uy * s)

    return [(at(lo), at(hi)) for lo, hi in pieces]


class CombinedProvider(DetectionProvider):
    """Several providers on one set, their detections joined per sheet.

    ``{"provider": "combined", "providers": [{"provider": "door_swing"},
    {"provider": "vector_glazing"}]}`` reports doors and windows together.
    It needs whatever any part needs (a scale, the walls), and the set reader
    hands it everything a part asks for. It is evaluation only when any part
    is, so a release refuses it then.
    """

    name = "combined"

    def __init__(self, parts: List[DetectionProvider]):
        self.parts = list(parts)
        self.needs_scale = any(getattr(p, "needs_scale", False) for p in self.parts)
        self.needs_walls = any(getattr(p, "needs_walls", False) for p in self.parts)
        # a part that needs neither still runs on a sheet with no scale; the
        # scale-needing parts then get no px_per_m and report nothing there
        self.runs_without_scale = any(
            not (getattr(p, "needs_scale", False) or getattr(p, "needs_walls", False))
            for p in self.parts
        )
        self.artifact = " + ".join(p.info.artifact or p.info.provider for p in self.parts)
        classes = []
        for p in self.parts:
            if p.info.license_class not in classes:
                classes.append(p.info.license_class)
        self.info = ProviderInfo(
            self.name,
            self.artifact,
            " + ".join(classes),
            any(p.info.eval_only for p in self.parts),
        )

    def has(self, image_path) -> bool:
        return any(not hasattr(p, "has") or p.has(image_path) for p in self.parts)

    def detect(self, image_path, sheet_id, plan: Optional[dict] = None, px_per_m=None):
        out: List[Detection] = []
        for p in self.parts:
            if hasattr(p, "has") and not p.has(image_path):
                continue
            kw = {}
            if getattr(p, "needs_walls", False):
                kw["plan"] = plan
            if getattr(p, "needs_scale", False):
                kw["px_per_m"] = px_per_m
            out.extend(p.detect(image_path, sheet_id, **kw))
        return out


PROVIDERS: Dict[str, type] = {
    "door_swing": DoorSwingProvider,
    "none": NoneProvider,
    "precomputed": PrecomputedProvider,
    "vector_glazing": VectorGlazingProvider,
    "yolo_sahi": YoloSahiProvider,
}
_REQUIRED = {"precomputed": ("dir",), "yolo_sahi": ("weights",)}


def provider_from_config(
    cfg: Union[None, dict, str, Path], release: bool = False
) -> DetectionProvider:
    """Build the provider a config names (a dict or a JSON file path).

    ``release=True`` refuses an evaluation-only provider rather than run it.
    """
    if cfg is None:
        cfg = {"provider": "none"}
    elif not isinstance(cfg, dict):
        cfg = json.loads(Path(cfg).read_text())
    cfg = dict(cfg)
    name = cfg.pop("provider", None)
    if name == "combined":
        return _combined_from_config(cfg, release)
    if name not in PROVIDERS:
        raise ProviderConfigError(
            f"detector provider {name!r} is not one of "
            f"{', '.join(sorted([*PROVIDERS, 'combined']))}"
        )
    missing = [k for k in _REQUIRED.get(name, ()) if not cfg.get(k)]
    if missing:
        raise ProviderConfigError(f"detector provider {name!r} needs {', '.join(missing)}")
    try:
        prov = PROVIDERS[name](**cfg)
    except TypeError as e:
        raise ProviderConfigError(f"detector provider {name!r}: {e}") from e
    if release and prov.info.eval_only:
        raise ProviderLicenseError(f"{prov.info.note()}; not allowed in a release")
    return prov


def _combined_from_config(cfg: dict, release: bool) -> CombinedProvider:
    parts = cfg.pop("providers", None)
    if cfg:
        raise ProviderConfigError(
            f"detector provider 'combined': unknown keys {', '.join(sorted(cfg))}"
        )
    if not isinstance(parts, list) or not parts:
        raise ProviderConfigError("detector provider 'combined' needs a non-empty providers list")
    built = []
    for part in parts:
        if not isinstance(part, dict):
            raise ProviderConfigError(
                "detector provider 'combined': each part is a provider config"
            )
        if part.get("provider") in ("combined", "none"):
            raise ProviderConfigError(
                f"detector provider 'combined' cannot hold {part.get('provider')!r}"
            )
        built.append(provider_from_config(part, release=release))
    return CombinedProvider(built)


__all__ = [
    "CombinedProvider",
    "DetectionProvider",
    "DoorSwingProvider",
    "VectorGlazingProvider",
    "NoneProvider",
    "PrecomputedProvider",
    "YoloSahiProvider",
    "ProviderInfo",
    "ProviderConfigError",
    "ProviderLicenseError",
    "PROVIDERS",
    "license_class",
    "provider_from_config",
    "window_detections",
]
