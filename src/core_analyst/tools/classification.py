from __future__ import annotations

import numpy as np


class HazardClassifier:
    class_values = {"low": 1, "medium": 2, "high": 3}

    def classify(self, hazard_index: np.ndarray, thresholds: dict[str, list[float]]) -> np.ndarray:
        classes = np.zeros_like(hazard_index, dtype="uint8")
        for label, bounds in thresholds.items():
            lower, upper = float(bounds[0]), float(bounds[1])
            if label == "high":
                mask = (hazard_index >= lower) & (hazard_index <= upper)
            else:
                mask = (hazard_index >= lower) & (hazard_index < upper)
            classes[mask] = self.class_values[label]
        return classes
