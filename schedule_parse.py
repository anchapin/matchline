"""Explicit symbol-to-schedule linkage graph.

Links extracted symbol instances (Detections) to schedule table rows
(ScheduleEntry) with full provenance, confidence scoring, and review queue
for ambiguous matches.

Every extracted fact carries: sheet, revision, method, and confidence.
Low-confidence results go to the review queue — nothing is silently accepted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from datasets_adapter import Detection, ScheduleEntry

log = logging.getLogger(__name__)


class LinkStatus(Enum):
    """Outcome of a symbol-to-schedule linkage attempt."""

    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    NO_TAG = "no_tag"
    NO_MATCH = "no_match"
    MULTIPLE_CANDIDATES = "multiple_candidates"


@dataclass
class Provenance:
    """Provenance for an extracted fact.

    Every extracted fact carries sheet, revision, method, and confidence
    so that downstream consumers can audit the origin of any value.
    """

    sheet_id: str
    revision: str
    method: str
    confidence: float
    bbox: tuple | None = None
    note: str = ""


@dataclass
class SymbolScheduleLink:
    """One explicit link between a symbol instance and a schedule row.

    Attributes
    ----------
    detection : Detection
        The symbol instance being linked.
    schedule_entry : ScheduleEntry
        The schedule row this instance is linked to.
    provenance : Provenance
        Sheet, revision, method, and confidence of this linkage.
    status : LinkStatus
        Outcome of the linkage attempt.
    candidate_entries : list[ScheduleEntry]
        All candidate entries considered (for ambiguous cases).
    """

    detection: Detection
    schedule_entry: ScheduleEntry
    provenance: Provenance
    status: LinkStatus = LinkStatus.LINKED
    candidate_entries: list[ScheduleEntry] = field(default_factory=list)


@dataclass
class SymbolLinkResult:
    """Result of a full symbol-to-schedule linkage run.

    Attributes
    ----------
    links : list[SymbolScheduleLink]
        All successful and ambiguous links.
    unlinked : list[Detection]
        Detections that could not be linked to any schedule row.
    review_queue : list[SymbolScheduleLink]
        Ambiguous / low-confidence links that require human review.
    all_detections : list[Detection]
        Complete list of input detections (for visibility).
    all_entries : dict[str, ScheduleEntry]
        Complete schedule used for linkage.
    """

    links: list[SymbolScheduleLink] = field(default_factory=list)
    unlinked: list[Detection] = field(default_factory=list)
    review_queue: list[SymbolScheduleLink] = field(default_factory=list)
    all_detections: list[Detection] = field(default_factory=list)
    all_entries: dict[str, ScheduleEntry] = field(default_factory=dict)

    @property
    def linked_count(self) -> int:
        return sum(1 for l in self.links if l.status == LinkStatus.LINKED)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for l in self.links if l.status == LinkStatus.AMBIGUOUS)

    @property
    def unlinked_count(self) -> int:
        return len(self.unlinked)

    @property
    def review_queue_count(self) -> int:
        return len(self.review_queue)


def _compute_confidence(
    detection: Detection,
    entry: ScheduleEntry,
    candidates: list[ScheduleEntry],
    method: str,
) -> tuple[float, Provenance]:
    """Compute confidence score for a symbol-to-schedule link.

    Parameters
    ----------
    detection : Detection
        The symbol instance.
    entry : ScheduleEntry
        The matched schedule entry.
    candidates : list[ScheduleEntry]
        All candidate entries (for ambiguity scoring).
    method : str
        Linkage method used (e.g. "tag_exact", "geometry_proximity").

    Returns
    -------
    tuple[float, Provenance]
        Confidence score and provenance object.
    """
    base_confidence = detection.score

    if len(candidates) > 1:
        ambiguity_penalty = 0.2 * (len(candidates) - 1)
        base_confidence = max(0.1, base_confidence - ambiguity_penalty)
    elif len(candidates) == 1 and entry.tag == detection.tag:
        base_confidence = min(1.0, base_confidence + 0.1)

    if detection.tag == "":
        base_confidence *= 0.5
    elif detection.tag != entry.tag:
        base_confidence *= 0.3

    provenance = Provenance(
        sheet_id=detection.source,
        revision="",
        method=method,
        confidence=round(base_confidence, 3),
        bbox=detection.bbox,
        note=f"matched_tag={entry.tag}" if entry.tag else "",
    )

    return round(base_confidence, 3), provenance


def link_symbol_to_schedule(
    detections: list[Detection],
    schedule: dict[str, ScheduleEntry],
    method: str = "tag_exact",
    min_confidence: float = 0.5,
) -> SymbolLinkResult:
    """Link symbol instances to schedule rows.

    This function creates explicit SymbolScheduleLink objects for each
    detection, with confidence scoring and full provenance.

    Ambiguous tag matches (multiple candidate rows) and missing tags are
    flagged for review queue rather than being silently accepted.

    Parameters
    ----------
    detections : list[Detection]
        Stage-1 symbol instances to link.
    schedule : dict[str, ScheduleEntry]
        Schedule table (tag -> ScheduleEntry).
    method : str
        Linkage method identifier (default "tag_exact").
    min_confidence : float
        Minimum confidence threshold. Links below this go to review queue.

    Returns
    -------
    SymbolLinkResult
        Contains links, unlinked detections, review queue, and metadata.

    Examples
    --------
    >>> from datasets_adapter import Detection, ScheduleEntry
    >>> det = Detection(label="Window", tag="A", score=0.9,
    ...                 bbox=(0,0,100,100), source="sheet_01")
    >>> sched = {"A": ScheduleEntry(tag="A", category="window",
    ...                             width_m=1.2, height_m=1.5)}
    >>> result = link_symbol_to_schedule([det], sched)
    >>> result.linked_count
    1
    >>> result.unlinked_count
    0
    """
    if not schedule:
        log.warning("Empty schedule provided — all detections will be unlinked")
        return SymbolLinkResult(
            unlinked=list(detections),
            all_detections=list(detections),
            all_entries={},
        )

    links: list[SymbolScheduleLink] = []
    unlinked: list[Detection] = []
    review_queue: list[SymbolScheduleLink] = []

    for detection in detections:
        tag = detection.tag.strip() if detection.tag else ""

        if not tag:
            conf, prov = _compute_confidence(
                detection, schedule.get("", ScheduleEntry("", "", None, None)), [], method
            )
            no_tag_entry = ScheduleEntry(tag="", category="", width_m=None, height_m=None)
            link = SymbolScheduleLink(
                detection=detection,
                schedule_entry=no_tag_entry,
                provenance=prov,
                status=LinkStatus.NO_TAG,
                candidate_entries=[],
            )
            unlinked.append(detection)
            review_queue.append(link)
            links.append(link)
            continue

        candidates = [entry for entry in schedule.values() if entry.tag == tag]

        if len(candidates) == 0:
            no_match_entry = ScheduleEntry(tag=tag, category="", width_m=None, height_m=None)
            conf, prov = _compute_confidence(detection, no_match_entry, [], method)
            link = SymbolScheduleLink(
                detection=detection,
                schedule_entry=no_match_entry,
                provenance=prov,
                status=LinkStatus.NO_MATCH,
                candidate_entries=[],
            )
            unlinked.append(detection)
            links.append(link)
            continue

        if len(candidates) > 1:
            best_entry = candidates[0]
            conf, prov = _compute_confidence(detection, best_entry, candidates, method)
            link = SymbolScheduleLink(
                detection=detection,
                schedule_entry=best_entry,
                provenance=prov,
                status=LinkStatus.MULTIPLE_CANDIDATES,
                candidate_entries=candidates,
            )
            links.append(link)
            review_queue.append(link)
            continue

        entry = candidates[0]
        conf, prov = _compute_confidence(detection, entry, candidates, method)
        status = LinkStatus.LINKED if conf >= min_confidence else LinkStatus.AMBIGUOUS

        link = SymbolScheduleLink(
            detection=detection,
            schedule_entry=entry,
            provenance=prov,
            status=status,
            candidate_entries=candidates,
        )

        links.append(link)

        if status == LinkStatus.AMBIGUOUS or conf < min_confidence:
            review_queue.append(link)

    return SymbolLinkResult(
        links=links,
        unlinked=unlinked,
        review_queue=review_queue,
        all_detections=list(detections),
        all_entries=dict(schedule),
    )


def get_links_by_symbol_type(
    result: SymbolLinkResult, symbol_type: str
) -> list[SymbolScheduleLink]:
    """Get all links for a specific symbol type.

    Parameters
    ----------
    result : SymbolLinkResult
        Result from link_symbol_to_schedule.
    symbol_type : str
        Symbol type to filter by (e.g. "Window", "Door").

    Returns
    -------
    list[SymbolScheduleLink]
        All links where the detection label matches symbol_type.
    """
    return [l for l in result.links if l.detection.label == symbol_type]


def get_links_by_schedule_tag(result: SymbolLinkResult, tag: str) -> list[SymbolScheduleLink]:
    """Get all links referencing a specific schedule tag.

    Parameters
    ----------
    result : SymbolLinkResult
        Result from link_symbol_to_schedule.
    tag : str
        Schedule tag to filter by (e.g. "A", "D1").

    Returns
    -------
    list[SymbolScheduleLink]
        All links where the schedule entry tag matches.
    """
    return [l for l in result.links if l.schedule_entry.tag == tag]


def get_review_queue_summary(result: SymbolLinkResult) -> dict[str, int]:
    """Get a summary of the review queue by status.

    Parameters
    ----------
    result : SymbolLinkResult
        Result from link_symbol_to_schedule.

    Returns
    -------
    dict[str, int]
        Count of review items by status.
    """
    summary: dict[str, int] = {}
    for link in result.review_queue:
        key = link.status.value
        summary[key] = summary.get(key, 0) + 1
    return summary
