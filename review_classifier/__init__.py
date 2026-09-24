"""Jev-style review-queue decision classifier (weekend prototype).

Typed, calibrated decisions (yes/no, multiple choice) over Matchline
extraction-review items, fully local: TF-IDF + logistic regression wrapped
in sigmoid calibration. No external API, no data egress.

Prototype scope: trains on *synthetic* data. Real calibration must be
re-measured on real drawings (see REPORT.md).
"""

from review_classifier.model import TypedDecider
from review_classifier.triage import ReviewTriage, TriageDecision, get_triage, reset_triage

__all__ = ["TypedDecider", "ReviewTriage", "TriageDecision", "get_triage", "reset_triage"]
