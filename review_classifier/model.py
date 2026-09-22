"""Jev-style typed decision API over a calibrated sklearn classifier.

``TypedDecider`` wraps TF-IDF + logistic regression in
``CalibratedClassifierCV`` (sigmoid/Platt scaling) and exposes the two Jev
primitives we need for the review queue:

- ``noul``: yes/no question -> P(yes)
- ``choice``: multiple-choice question -> {option: probability}

Plus ``decide`` which returns the argmax label and its confidence, so callers
can apply a confidence threshold and escalate to a human below it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

from review_classifier.data import Example
from review_classifier.features import ReviewFeaturizer


@dataclass
class Decision:
    label: object
    confidence: float
    probabilities: dict


class TypedDecider:
    def __init__(self, cv: int = 5, max_features: int = 2000, seed: int = 20260919):
        self.cv = cv
        self.seed = seed
        self.featurizer = ReviewFeaturizer(max_features=max_features)
        self._calibrated: CalibratedClassifierCV | None = None
        self.classes_: list | None = None

    def _base(self) -> LogisticRegression:
        return LogisticRegression(max_iter=2000, random_state=self.seed)

    def fit(self, examples: list[Example]) -> "TypedDecider":
        X = self.featurizer.fit(examples).transform(examples)
        y = np.array([e.label for e in examples])
        self._calibrated = CalibratedClassifierCV(
            estimator=self._base(), method="sigmoid", cv=self.cv
        )
        self._calibrated.fit(X, y)
        self.classes_ = list(self._calibrated.classes_)
        return self

    def fit_uncalibrated(self, examples: list[Example]) -> LogisticRegression:
        """Same pipeline without the calibration wrapper (for comparison)."""
        X = self.featurizer.fit(examples).transform(examples)
        y = np.array([e.label for e in examples])
        clf = self._base().fit(X, y)
        return clf

    def _proba(self, examples: list[Example]) -> np.ndarray:
        assert self._calibrated is not None, "call fit() first"
        X = self.featurizer.transform(examples)
        return self._calibrated.predict_proba(X)

    def decide(self, example: Example) -> Decision:
        proba = self._proba([example])[0]
        idx = int(np.argmax(proba))
        return Decision(
            label=self.classes_[idx],
            confidence=float(proba[idx]),
            probabilities={c: float(p) for c, p in zip(self.classes_, proba)},
        )

    def noul(self, example: Example) -> float:
        """Yes/no question: probability the answer is True."""
        proba = self._proba([example])[0]
        return float(proba[self.classes_.index(True)])

    def choice(self, example: Example, options: list) -> dict:
        """Multiple-choice question: probability per option (subset of classes)."""
        proba = self._proba([example])[0]
        out = {}
        for opt in options:
            out[opt] = float(proba[self.classes_.index(opt)])
        return out
