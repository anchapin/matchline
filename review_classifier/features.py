"""Featurizer: Example -> (text, numeric vector) -> sparse feature matrix."""

from __future__ import annotations

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import StandardScaler

from review_classifier.data import Example


class ReviewFeaturizer:
    """TF-IDF over the text rendering + standardized numeric features."""

    def __init__(self, max_features: int = 2000):
        self.max_features = max_features
        self.text_vec = TfidfVectorizer(
            max_features=max_features, ngram_range=(1, 2), sublinear_tf=True
        )
        self.scaler = StandardScaler()
        self._num_keys: list[str] = []

    def fit(self, examples: list[Example]) -> "ReviewFeaturizer":
        self._num_keys = sorted({k for e in examples for k in e.numeric})
        self.text_vec.fit([e.text for e in examples])
        self.scaler.fit(self._num_matrix(examples))
        return self

    def transform(self, examples: list[Example]):
        text_mat = self.text_vec.transform([e.text for e in examples])
        num_mat = sparse.csr_matrix(self.scaler.transform(self._num_matrix(examples)))
        return sparse.hstack([text_mat, num_mat], format="csr")

    def _num_matrix(self, examples: list[Example]) -> np.ndarray:
        return np.array([[e.numeric.get(k, 0.0) for k in self._num_keys] for e in examples])
