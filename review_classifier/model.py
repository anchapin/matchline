"""Jev-style typed decision API over a calibrated sklearn classifier.

``TypedDecider`` wraps TF-IDF + logistic regression in
``CalibratedClassifierCV`` (sigmoid/Platt scaling) and exposes the two Jev
primitives we need for the review queue:

- ``noul``: yes/no question -> P(yes)
- ``choice``: multiple-choice question -> {option: probability}

Plus ``decide`` which returns the argmax label and its confidence, so callers
can apply a confidence threshold and escalate to a human below it.

Model files use ``.npz`` format (numpy archive) instead of pickle for
security: numpy arrays carry no code-execution risk. See ``to_npz`` / ``from_npz``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
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
        self._calibrators = [
            (cc.calibrators[0].a_, cc.calibrators[0].b_)
            for cc in self._calibrated.calibrated_classifiers_
        ]
        self._calibrator_avg = (
            float(np.mean([a for a, _ in self._calibrators])),
            float(np.mean([b for _, b in self._calibrators])),
        )
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
        if hasattr(self, "_calibrator_avg"):
            base_proba = self._calibrated.calibrated_classifiers_[0].estimator.predict_proba(X)
            a, b = self._calibrator_avg
            if len(self.classes_) == 2:
                p = 1.0 / (1.0 + np.exp(-(a * base_proba[:, 1] + b)))
                out = np.empty((X.shape[0], 2))
                out[:, 1] = p
                out[:, 0] = 1.0 - p
                return out
            out = np.empty_like(base_proba)
            for i in range(len(self.classes_)):
                out[:, i] = 1.0 / (1.0 + np.exp(-(a * base_proba[:, i] + b)))
            out /= out.sum(axis=1, keepdims=True)
            return out
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

    def to_npz(self, path: Path) -> None:
        """Serialize the fitted model to a numpy ``.npz`` archive.

        Unlike pickle, numpy arrays carry no code-execution risk. The archive
        stores the AVERAGED base estimator and calibrator parameters across CV
        folds, which is sufficient for prediction (sklearn internally uses the
        per-fold averages for prediction anyway).
        """
        assert self._calibrated is not None, "call fit() first"
        assert self.classes_ is not None

        fv = self.featurizer.text_vec
        fs = self.featurizer.scaler
        cal = self._calibrated
        cal_classifiers = cal.calibrated_classifiers_

        base_coef = np.stack([cc.estimator.coef_ for cc in cal_classifiers]).mean(axis=0)
        base_intercept = np.stack([cc.estimator.intercept_ for cc in cal_classifiers]).mean(axis=0)

        a_vals = [cc.calibrators[0].a_ for cc in cal_classifiers]
        b_vals = [cc.calibrators[0].b_ for cc in cal_classifiers]
        calibrator_avg = {"a": float(np.mean(a_vals)), "b": float(np.mean(b_vals))}

        npz = {
            "featurizer_idf.npy": fv.idf_,
            "featurizer_scaler_mean.npy": fs.mean_,
            "featurizer_scaler_scale.npy": fs.scale_,
            "base_estimator_coef.npy": base_coef,
            "base_estimator_intercept.npy": base_intercept,
            "featurizer_vocab.json": json.dumps({k: int(v) for k, v in fv.vocabulary_.items()}),
            "featurizer_num_keys.json": json.dumps(self.featurizer._num_keys),
            "base_estimator_classes.json": json.dumps(np.array(self.classes_).tolist()),
            "calibrator_avg.json": json.dumps(calibrator_avg),
            "meta.json": json.dumps(
                {"cv": self.cv, "seed": self.seed, "max_features": self.featurizer.max_features}
            ),
        }
        np.savez(path, **npz)

    @classmethod
    def from_npz(cls, path: Path) -> "TypedDecider":
        """Reconstruct a ``TypedDecider`` from a numpy ``.npz`` archive.

        This is the safe alternative to ``joblib.load()``: numpy arrays cannot
        embed executable code.
        """
        data = dict(np.load(path, allow_pickle=False))

        vocab = json.loads(data["featurizer_vocab.json"].item())
        idf = data["featurizer_idf.npy"]
        num_keys = json.loads(data["featurizer_num_keys.json"].item())
        scaler_mean = data["featurizer_scaler_mean.npy"]
        scaler_scale = data["featurizer_scaler_scale.npy"]
        base_coef = data["base_estimator_coef.npy"]
        base_intercept = data["base_estimator_intercept.npy"]
        classes = json.loads(data["base_estimator_classes.json"].item())
        meta = json.loads(data["meta.json"].item())

        decider = cls(cv=meta["cv"], seed=meta["seed"], max_features=meta["max_features"])

        decider.featurizer.text_vec = TfidfVectorizer(
            max_features=meta["max_features"], ngram_range=(1, 2), sublinear_tf=True
        )
        decider.featurizer.text_vec.vocabulary_ = vocab
        decider.featurizer.text_vec.idf_ = idf
        decider.featurizer.scaler.mean_ = scaler_mean
        decider.featurizer.scaler.scale_ = scaler_scale
        decider.featurizer._num_keys = num_keys

        base_estimator = LogisticRegression(max_iter=2000, random_state=meta["seed"])
        base_estimator.coef_ = base_coef
        base_estimator.intercept_ = base_intercept
        base_estimator.classes_ = np.array(classes)
        base_estimator.n_features_in_ = base_coef.shape[1]

        cal_avg = json.loads(data["calibrator_avg.json"].item())
        cal_a, cal_b = float(cal_avg["a"]), float(cal_avg["b"])

        base_estimator = LogisticRegression(max_iter=2000, random_state=meta["seed"])
        base_estimator.coef_ = base_coef
        base_estimator.intercept_ = base_intercept
        base_estimator.classes_ = np.array(classes)
        base_estimator.n_features_in_ = base_coef.shape[1]

        class _FakeCalibratedClassifierCV:
            def __init__(self, base_est):
                self.base_estimator_ = base_est

            def predict_proba(self, X):
                return self.base_estimator_.predict_proba(X)

        cal_estimator = _FakeCalibratedClassifierCV(base_estimator)
        decider._calibrated = cal_estimator
        decider._calibrators = [(cal_a, cal_b)]
        decider.classes_ = list(classes)

        return decider
