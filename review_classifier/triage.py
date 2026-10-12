"""Review-queue triage: typed-decision auto-resolution.

This module wires the local TypedDecider into the review-queue pipeline,
providing three calibrated decisions per flagged item:

- ``needs_human``: noul — does this item require human judgment?
- ``urgency``:   score 0-3 — if wrong, how much does takeoff accuracy suffer?
- ``resolution``: choice — accept link / drop link / reassign

Guardrails
----------
- Auto-resolve fires only when confidence >= 0.95 AND urgency < 2.
- The decider only *dispositions* flags; it never invents geometry.
- All models are loaded from the local ``review_classifier/`` directory.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from review_classifier.data import Example
from review_classifier.model import TypedDecider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUTO_RESOLVE_CONFIDENCE = 0.95
_HIGH_URGENCY_THRESHOLD = 2  # urgency >= this blocks auto-resolve

# ---------------------------------------------------------------------------
# Triage decision record
# ---------------------------------------------------------------------------


@dataclass
class TriageDecision:
    needs_human: float  # P(needs human) — 1.0 means definitely human
    urgency: int  # 0-3: 0=trivial, 3=critical
    resolution: str  # "accept" | "drop" | "reassign"
    resolution_confidence: float  # confidence in the resolution choice
    auto_resolved: bool = False  # True if auto-resolved per guardrails


# ---------------------------------------------------------------------------
# ReviewTriage
# ---------------------------------------------------------------------------


class ReviewTriage:
    """Calibrated triage decider for review-queue items.

    Wraps three TypedDecider instances (one per decision axis) and enforces
    the auto-resolve guardrails before returning a ``TriageDecision``.
    """

    def __init__(
        self,
        needs_human_model_path: pathlib.Path | None = None,
        urgency_model_path: pathlib.Path | None = None,
        resolution_model_path: pathlib.Path | None = None,
        auto_resolve_confidence: float = _AUTO_RESOLVE_CONFIDENCE,
        high_urgency_threshold: int = _HIGH_URGENCY_THRESHOLD,
    ) -> None:
        self._needs_human_decider: TypedDecider | None = None
        self._urgency_decider: TypedDecider | None = None
        self._resolution_decider: TypedDecider | None = None
        self._auto_resolve_confidence = auto_resolve_confidence
        self._high_urgency_threshold = high_urgency_threshold

        if needs_human_model_path is None:
            needs_human_model_path = pathlib.Path(__file__).parent / "trained_model_needs_human.pkl"
        if urgency_model_path is None:
            urgency_model_path = pathlib.Path(__file__).parent / "trained_model_urgency.pkl"
        if resolution_model_path is None:
            resolution_model_path = pathlib.Path(__file__).parent / "trained_model_resolution.pkl"

        self._needs_human_path = needs_human_model_path
        self._urgency_path = urgency_model_path
        self._resolution_path = resolution_model_path

    # -- loading ------------------------------------------------------------

    def load(self) -> "ReviewTriage":
        """Load all three model files, silently skipping any that are absent."""
        self._needs_human_decider = self._load_one(self._needs_human_path)
        self._urgency_decider = self._load_one(self._urgency_path)
        self._resolution_decider = self._load_one(self._resolution_path)
        return self

    def _load_one(self, path: pathlib.Path) -> TypedDecider | None:
        npz_path = path.with_suffix(".npz")
        if npz_path.exists():
            try:
                return TypedDecider.from_npz(npz_path)
            except (OSError, EOFError):
                return None
        if path.exists():
            raise ValueError(
                f"npz fallback is required for security; "
                f"no .npz found for {path.name}. "
                f"Convert the model to .npz format or remove the .pkl file."
            )
        return None

    @property
    def is_loaded(self) -> bool:
        return self._needs_human_decider is not None

    # -- decisions ----------------------------------------------------------

    def triage(self, example: Example) -> TriageDecision:
        """Return a full triage decision for ``example``.

        If a model is not loaded, all decisions return safe defaults
        (needs_human=1.0, urgency=1, resolution="reassign").
        """
        needs_human = self._decide_needs_human(example)
        urgency = self._decide_urgency(example)
        resolution, res_conf = self._decide_resolution(example)

        auto_resolved = (
            res_conf >= self._auto_resolve_confidence and urgency < self._high_urgency_threshold
        )

        return TriageDecision(
            needs_human=needs_human,
            urgency=urgency,
            resolution=resolution,
            resolution_confidence=res_conf,
            auto_resolved=auto_resolved,
        )

    def _decide_needs_human(self, example: Example) -> float:
        if self._needs_human_decider is None:
            return 1.0  # safe default: send to human
        try:
            return self._needs_human_decider.noul(example)
        except ValueError:
            return 1.0

    def _decide_urgency(self, example: Example) -> int:
        if self._urgency_decider is None:
            return 1  # safe default: medium urgency
        try:
            decision = self._urgency_decider.decide(example)
            label = decision.label
            if isinstance(label, bool):
                return 1
            if isinstance(label, int):
                return label
            if isinstance(label, (float, str)):
                return int(label)  # type: ignore[arg-type]
            return 1
        except (ValueError, TypeError):
            return 1

    def _decide_resolution(self, example: Example) -> tuple[str, float]:
        if self._resolution_decider is None:
            return "reassign", 0.0  # safe default
        try:
            decision = self._resolution_decider.decide(example)
            return str(decision.label), decision.confidence
        except (ValueError, TypeError):
            return "reassign", 0.0

    # -- batch -------------------------------------------------------------

    def triage_batch(self, examples: list[Example]) -> list[TriageDecision]:
        return [self.triage(ex) for ex in examples]


# ---------------------------------------------------------------------------
# Global singleton (lazy-loaded on first use)
# ---------------------------------------------------------------------------

_triage_instance: ReviewTriage | None = None


def get_triage() -> ReviewTriage:
    """Return the lazily-loaded global ReviewTriage instance."""
    global _triage_instance
    if _triage_instance is None:
        _triage_instance = ReviewTriage().load()
    return _triage_instance


def reset_triage() -> None:
    """Reset the global singleton (for testing or feature-flag toggling)."""
    global _triage_instance
    _triage_instance = None
