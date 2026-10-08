"""Door/window detection behind a provider chosen by config (#743, ROADMAP R2).

A ``DetectionProvider`` takes one sheet image and returns
``datasets_adapter.Detection`` rows (label, score, pixel bbox, source), the
shape the takeoff join already reads, so the rest of the pipeline does not care
which backend ran. Which provider runs is a config value, not code:

    {"provider": "precomputed", "dir": "dets/", "artifact": "<ledger path>"}
    {"provider": "yolo_sahi", "weights": "best.pt", "artifact": "<ledger path>"}
    {"provider": "none"}

Every provider names the trained artifact behind it. Its license class comes
from ``license_ledger.json`` (#751): weights trained on non-commercial or
copyleft data, or weights the ledger does not list, are evaluation only. Their
detections are marked so in the set report, and ``release=True`` refuses them.
"""

from __future__ import annotations

import fnmatch
import json
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


PROVIDERS: Dict[str, type] = {
    "none": NoneProvider,
    "precomputed": PrecomputedProvider,
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
    if name not in PROVIDERS:
        raise ProviderConfigError(
            f"detector provider {name!r} is not one of {', '.join(sorted(PROVIDERS))}"
        )
    missing = [k for k in _REQUIRED.get(name, ()) if not cfg.get(k)]
    if missing:
        raise ProviderConfigError(f"detector provider {name!r} needs {', '.join(missing)}")
    prov = PROVIDERS[name](**cfg)
    if release and prov.info.eval_only:
        raise ProviderLicenseError(f"{prov.info.note()}; not allowed in a release")
    return prov


__all__ = [
    "DetectionProvider",
    "NoneProvider",
    "PrecomputedProvider",
    "YoloSahiProvider",
    "ProviderInfo",
    "ProviderConfigError",
    "ProviderLicenseError",
    "PROVIDERS",
    "license_class",
    "provider_from_config",
]
