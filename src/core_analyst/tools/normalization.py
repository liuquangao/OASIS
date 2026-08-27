from __future__ import annotations

import numpy as np


class RasterNormalizer:
    def minmax(self, values: np.ndarray, invert: bool = False) -> np.ndarray:
        data = values.astype("float32")
        finite = np.isfinite(data)
        if not finite.any():
            return np.zeros_like(data, dtype="float32")

        minimum = float(np.nanmin(data[finite]))
        maximum = float(np.nanmax(data[finite]))
        if np.isclose(maximum, minimum):
            normalized = np.zeros_like(data, dtype="float32")
        else:
            normalized = (data - minimum) / (maximum - minimum)

        normalized = np.clip(normalized, 0.0, 1.0).astype("float32")
        normalized[~finite] = np.nan
        if invert:
            normalized = 1.0 - normalized
        return normalized.astype("float32")
