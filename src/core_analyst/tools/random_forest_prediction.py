from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class RandomForestRiskResult:
    risk: np.ndarray
    metadata: dict[str, Any]


class RandomForestRiskPredictor:
    """Train a pixel-wise random forest and return a predicted risk raster."""

    def __init__(
        self,
        *,
        n_estimators: int = 200,
        max_depth: int | None = 12,
        min_samples_leaf: int = 5,
        random_state: int = 42,
        max_training_samples: int = 50000,
    ):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.random_state = random_state
        self.max_training_samples = max_training_samples

    def predict(
        self,
        training_features: dict[str, np.ndarray],
        training_target: np.ndarray,
        prediction_features: dict[str, np.ndarray],
    ) -> RandomForestRiskResult:
        try:
            from sklearn.ensemble import RandomForestRegressor
        except ImportError as exc:
            raise ImportError(
                "Random forest prediction requires scikit-learn. Install project dependencies with "
                "`pip install -r requirements.txt`."
            ) from exc

        feature_names = list(training_features)
        if not feature_names:
            raise ValueError("Random forest prediction requires at least one feature.")
        if set(prediction_features) != set(feature_names):
            raise ValueError("Training and prediction features must have the same names.")

        shape = training_target.shape
        for name, values in {**training_features, **prediction_features}.items():
            if values.shape != shape:
                raise ValueError(f"Feature {name} shape {values.shape} does not match target shape {shape}.")

        train_stack = self._stack_features(training_features, feature_names)
        predict_stack = self._stack_features(prediction_features, feature_names)
        target = training_target.astype("float32").reshape(-1)

        train_mask = np.isfinite(target) & np.all(np.isfinite(train_stack), axis=1)
        predict_mask = np.all(np.isfinite(predict_stack), axis=1)
        train_indices = np.flatnonzero(train_mask)
        if train_indices.size < 2:
            raise ValueError("Random forest prediction requires at least two valid training pixels.")

        sampled_indices = self._sample_indices(train_indices)
        model = RandomForestRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.random_state,
            n_jobs=-1,
        )
        model.fit(train_stack[sampled_indices], target[sampled_indices])

        prediction = np.full(target.shape, np.nan, dtype="float32")
        prediction[predict_mask] = model.predict(predict_stack[predict_mask]).astype("float32")
        prediction = np.clip(prediction.reshape(shape), 0.0, 1.0).astype("float32")

        metadata = {
            "model_type": "RandomForestRegressor",
            "feature_names": feature_names,
            "feature_importance": {
                name: float(importance)
                for name, importance in zip(feature_names, model.feature_importances_)
            },
            "training_pixels": int(train_indices.size),
            "sampled_training_pixels": int(sampled_indices.size),
            "parameters": {
                "n_estimators": self.n_estimators,
                "max_depth": self.max_depth,
                "min_samples_leaf": self.min_samples_leaf,
                "random_state": self.random_state,
                "max_training_samples": self.max_training_samples,
            },
        }
        return RandomForestRiskResult(prediction, metadata)

    def _stack_features(self, features: dict[str, np.ndarray], feature_names: list[str]) -> np.ndarray:
        return np.column_stack([features[name].astype("float32").reshape(-1) for name in feature_names])

    def _sample_indices(self, valid_indices: np.ndarray) -> np.ndarray:
        if valid_indices.size <= self.max_training_samples:
            return valid_indices
        rng = np.random.default_rng(self.random_state)
        sampled = rng.choice(valid_indices, size=self.max_training_samples, replace=False)
        return np.sort(sampled)
