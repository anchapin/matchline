"""Natural-language query layer over the parsed building model.

The queries themselves are answered deterministically (no LLM in the answer path).
The LLM is used only to DISPATCH to the right method based on natural language.

Tool-calling / dispatch is deterministic regex + keyword matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from building_model import BuildingModel, Provenance


@dataclass
class QueryResult:
    """A single query result with provenance."""

    answer: Any
    provenance: Provenance | None = None
    method: str = "query_dispatch"


@dataclass
class QueryResponse:
    """Full response from a query call."""

    results: List[QueryResult]
    method: str = "query_dispatch"
    model_version: str = ""


class QueryEngine:
    """Natural-language query engine over a BuildingModel.

    Parameters
    ----------
    model : BuildingModel
        The parsed building model to query.
    llm : Any, optional
        LLM backend for dispatch. Currently unused — dispatch is fully
        deterministic via regex + keyword matching. The parameter is accepted
        for forward compatibility and to satisfy the interface described in
        the issue.
    """

    def __init__(self, model: BuildingModel, llm: Any = None) -> None:
        self.model = model
        self.llm = llm

    # ------------------------------------------------------------------
    # Public query methods (deterministic)
    # ------------------------------------------------------------------

    def count_spaces(self, _params: Optional[dict] = None) -> QueryResult:
        """Count total number of spaces across all levels.

        Provenance: derived from the model's registered spaces.
        """
        spaces = list(getattr(self.model, "spaces", {}).values())
        return QueryResult(
            answer=len(spaces),
            provenance=None,
            method="count_spaces",
        )

    def total_floor_area(self, _params: Optional[dict] = None) -> QueryResult:
        """Compute total conditioned floor area (m²) across all spaces.

        Returns the sum of ``area_m2`` for all spaces in the model.
        Each space's ``area_m2`` is used directly.

        Provenance: the provenance of the first space with a non-None provenance.
        """
        all_spaces = list(getattr(self.model, "spaces", {}).values())
        total = sum(s.area_m2 or 0.0 for s in all_spaces)
        best_prov = next((s.core_provenance for s in all_spaces if s.core_provenance), None)
        return QueryResult(
            answer=round(total, 3),
            provenance=best_prov,
            method="total_floor_area",
        )

    def building_orientation(self, _params: Optional[dict] = None) -> QueryResult:
        """Return the building's primary cardinal orientation.

        Returns the first registered compass bearing from the model metadata,
        or "unknown" if not set. Compass bearing is expressed as
        ``"N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW" | "unknown"``.

        Provenance: the model's provenance field, if set.
        """
        orientation = getattr(self.model, "orientation", None)
        if orientation is None:
            return QueryResult(
                answer="unknown",
                provenance=None,
                method="building_orientation",
            )
        bearing = _bearing_to_cardinal(orientation)
        prov = getattr(self.model, "provenance", None)
        return QueryResult(
            answer=bearing,
            provenance=prov,
            method="building_orientation",
        )

    def zone_list(self, _params: Optional[dict] = None) -> QueryResult:
        """List all thermal zones with their associated space IDs.

        Returns a dict mapping zone name to list of space IDs.

        Provenance: the provenance of the first zone that has one.
        """
        zones: dict = getattr(self.model, "zones", None) or {}
        if not zones:
            return QueryResult(
                answer={},
                provenance=None,
                method="zone_list",
            )
        result = {}
        for zone_name, zone in zones.items():
            result[zone_name] = list(zone.space_ids)
        best_prov = next(
            (z.provenance for z in zones.values() if z.provenance is not None),
            None,
        )
        return QueryResult(
            answer=result,
            provenance=best_prov,
            method="zone_list",
        )

    def envelope_summary(self, _params: Optional[dict] = None) -> QueryResult:
        """Return a summary of the building envelope.

        Includes:
        - total wall area (m²)
        - total window area (m²)
        - window-to-wall ratio (WWR)
        - number of windows
        - number of facades

        Provenance: aggregated from space openings and facade records.
        """
        walls = list(getattr(self.model, "envelope", None) or [])
        total_wall_area = sum(w.area_m2 or 0.0 for w in walls)

        all_openings: List[Any] = []
        for space in getattr(self.model, "spaces", {}).values():
            all_openings.extend(space.openings)

        total_window_area = sum(o.area_m2 or 0.0 for o in all_openings if o.category == "window")
        n_windows = sum(1 for o in all_openings if o.category == "window")

        facade_set: set = set()
        for o in all_openings:
            if getattr(o, "host_facade", None):
                facade_set.add(o.host_facade)

        wwr = (total_window_area / total_wall_area) if total_wall_area > 0 else 0.0
        best_prov = next(
            (
                o.provenance
                for o in all_openings
                if o.provenance is not None and o.category == "window"
            ),
            None,
        )
        return QueryResult(
            answer={
                "total_wall_area_m2": round(total_wall_area, 3),
                "total_window_area_m2": round(total_window_area, 3),
                "window_to_wall_ratio": round(wwr, 4),
                "n_windows": n_windows,
                "n_facades": len(facade_set),
                "facades": sorted(facade_set),
            },
            provenance=best_prov,
            method="envelope_summary",
        )

    # ------------------------------------------------------------------
    # Dispatch table (deterministic)
    # ------------------------------------------------------------------

    _METHOD_NAMES: List[str] = [
        "count_spaces",
        "total_floor_area",
        "building_orientation",
        "zone_list",
        "envelope_summary",
    ]

    _PATTERNS: List[tuple] = [
        (re.compile(r"(?i)\bcount\s+the\s+spaces?\b"), "count_spaces"),
        (re.compile(r"(?i)\bcount\s+spaces?\b"), "count_spaces"),
        (re.compile(r"(?i)\bhow\s+many\s+spaces?\b"), "count_spaces"),
        (re.compile(r"(?i)\bnumber\s+of\s+spaces?\b"), "count_spaces"),
        (re.compile(r"(?i)\btotal\s+floor\s+area\b"), "total_floor_area"),
        (re.compile(r"(?i)\bfloor\s+area\b"), "total_floor_area"),
        (re.compile(r"(?i)\bconditioned\s+area\b"), "total_floor_area"),
        (re.compile(r"(?i)\bbuilding\s+orientation\b"), "building_orientation"),
        (re.compile(r"(?i)\borientation\b"), "building_orientation"),
        (re.compile(r"(?i)\bwhich\s+direction\b"), "building_orientation"),
        (re.compile(r"(?i)\bcompass\b"), "building_orientation"),
        (re.compile(r"(?i)\bzones?\b"), "zone_list"),
        (re.compile(r"(?i)\blist\s+zones?\b"), "zone_list"),
        (re.compile(r"(?i)\bthermal\s+zones?\b"), "zone_list"),
        (re.compile(r"(?i)\benvelope\b"), "envelope_summary"),
        (re.compile(r"(?i)\bwindow.*wall\b"), "envelope_summary"),
        (re.compile(r"(?i)\bwwr\b"), "envelope_summary"),
        (re.compile(r"(?i)\bwindow.*area\b"), "envelope_summary"),
    ]

    def query(self, nl_query: str, params: Optional[dict] = None) -> QueryResponse:
        """Process a natural-language query and return structured results.

        Parameters
        ----------
        nl_query : str
            Natural-language question, e.g. "how many spaces are in the model?"
        params : dict, optional
            Additional parameters passed to the underlying query method.

        Returns
        -------
        QueryResponse
            Contains a list of QueryResult objects and metadata.
        """
        method_name = self._dispatch(nl_query)
        if method_name is None:
            return QueryResponse(
                results=[
                    QueryResult(
                        answer=None,
                        provenance=None,
                        method="query_dispatch",
                    )
                ],
                method="query_dispatch",
                model_version=getattr(self.model, "model_version", ""),
            )

        fn = getattr(self, method_name)
        result = fn(params)

        return QueryResponse(
            results=[result],
            method=method_name,
            model_version=getattr(self.model, "model_version", ""),
        )

    def _dispatch(self, nl_query: str) -> Optional[str]:
        """Map a natural-language query string to a method name.

        Uses deterministic regex + keyword matching. Returns None when
        no pattern matches.
        """
        for pattern, method_name in self._PATTERNS:
            if pattern.search(nl_query):
                return method_name
        return None

    def available_queries(self) -> List[str]:
        """Return list of available query method names."""
        return list(self._METHOD_NAMES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearing_to_cardinal(degrees: float) -> str:
    """Convert a compass bearing in degrees to a cardinal direction.

    Parameters
    ----------
    degrees : float
        Bearing in degrees clockwise from north (0° = North).

    Returns
    -------
    str
        One of ``"N"``, ``"NE"``, ``"E"``, ``"SE"``, ``"S"``, ``"SW"``,
        ``"W"``, ``"NW"``, or ``"unknown"`` if the input is invalid.
    """
    try:
        d = float(degrees) % 360
    except (TypeError, ValueError):
        return "unknown"

    if d < 22.5 or d >= 337.5:
        return "N"
    if d < 67.5:
        return "NE"
    if d < 112.5:
        return "E"
    if d < 157.5:
        return "SE"
    if d < 202.5:
        return "S"
    if d < 247.5:
        return "SW"
    if d < 292.5:
        return "W"
    if d < 337.5:
        return "NW"
    return "unknown"
