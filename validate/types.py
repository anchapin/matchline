"""Validation / invariant layer: the building model must balance its own books.

Alex's framing: "if the first floor is 20x30 ft, the sum of space floor
areas should add up to about 600 sq ft" and "3D volume of the gbXML model
vs footprint x height". These are CONSERVATION LAWS. A model that cannot
balance them should not export.

``run_checks(model, ...)`` runs a named battery of checks. Each check
returns pass / warn / error with a human-readable message, the expected
vs actual numbers, and the offending entity ids. Severity policy:

  * ERROR = the books don't balance (conservation violated, referential
    breakage, missing provenance). Blocks export: see ``export_gate``.
  * WARN  = plausibility tripwire (absurd LPD, sill/head sanity, unit
    smells). Does not block export but must be acknowledged.
  * SKIP  = check not applicable (no export path given, no elevation
    windows linked, no simplifier result passed in).

Every tolerance is documented in docs/validation.md with its rationale.
Nothing here modifies the model; it only reads it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

from bem_export import BEMModel, _shoelace
from building_model import BuildingModel
from datasets_adapter import polygon_area_px2
from safe_xml import safe_xml_parse

try:
    from geometry_simplify import footprint_from_regions, simplify_ring

    _HAS_SIMPLIFY = True
except Exception:
    _HAS_SIMPLIFY = False

FT2_PER_M2 = 10.7639

MIN_CONFIDENCE_THRESHOLD: float = 0.7

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

SEVERITIES = ("pass", "warn", "error", "skip")


@dataclass
class CheckResult:
    check_id: str
    name: str
    severity: str  # "pass" | "warn" | "error" | "skip"
    message: str
    entities: list = field(default_factory=list)  # offending entity ids
    expected: object = None
    actual: object = None
    needs_review: dict = field(default_factory=dict)  # fact_id -> bool

    def to_dict(self) -> dict:
        d = {
            "check_id": self.check_id,
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "entities": sorted(str(e) for e in self.entities),
        }
        if self.expected is not None:
            d["expected"] = _round(self.expected)
        if self.actual is not None:
            d["actual"] = _round(self.actual)
        if self.needs_review:
            d["needs_review"] = {str(k): v for k, v in self.needs_review.items()}
        return d


def _rel_err(actual: float, expected: float) -> float:
    if expected == 0:
        return float('inf')
    return abs(actual - expected) / abs(expected)


def _round(v: object, nd: int = 6) -> object:
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, dict):
        return {k: _round(x, nd) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_round(x, nd) for x in v]
    return v


@dataclass
class ValidationReport:
    building_name: str
    results: list = field(default_factory=list)  # CheckResult

    @property
    def errors(self):
        return [r for r in self.results if r.severity == "error"]

    @property
    def warnings(self):
        return [r for r in self.results if r.severity == "warn"]

    @property
    def passes(self):
        return [r for r in self.results if r.severity == "pass"]

    @property
    def skipped(self):
        return [r for r in self.results if r.severity == "skip"]

    @property
    def ok(self) -> bool:
        """No errors. Warnings do not fail the gate."""
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "building_name": self.building_name,
            "ok": self.ok,
            "summary": {
                "n_checks": len(self.results),
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "passes": len(self.passes),
                "skipped": len(self.skipped),
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    def compact(self) -> str:
        """One-line-per-check human summary."""
        lines = [
            f"validation: {self.building_name} -> "
            f"{'OK' if self.ok else 'ERRORS'} "
            f"({len(self.errors)}E/{len(self.warnings)}W/"
            f"{len(self.passes)}P/{len(self.skipped)}S)"
        ]
        for r in self.results:
            if r.severity in ("error", "warn"):
                lines.append(f"  [{r.severity.upper():5s}] {r.check_id}: {r.message}")
        return "\n".join(lines)


