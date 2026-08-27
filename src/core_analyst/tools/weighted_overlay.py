from __future__ import annotations

import numpy as np


class WeightedOverlayAnalyzer:
    def analyze(self, factors: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
        missing = sorted(set(weights) - set(factors))
        if missing:
            raise ValueError(f"Missing factors for weighted overlay: {missing}")

        total_weight = sum(float(weights[name]) for name in factors)
        if total_weight <= 0:
            raise ValueError("Weighted overlay requires positive total weight.")

        hazard = np.zeros_like(next(iter(factors.values())), dtype="float32")
        for name, factor in factors.items():
            hazard += factor.astype("float32") * (float(weights[name]) / total_weight)

        invalid = np.zeros_like(hazard, dtype=bool)
        for factor in factors.values():
            invalid |= ~np.isfinite(factor)
        hazard[invalid] = np.nan
        return np.clip(hazard, 0.0, 1.0).astype("float32")
