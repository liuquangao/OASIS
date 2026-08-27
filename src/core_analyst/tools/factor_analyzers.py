from __future__ import annotations

import numpy as np

from core_analyst.tools.normalization import RasterNormalizer


class ElevationRiskAnalyzer:
    def __init__(self, normalizer: RasterNormalizer | None = None):
        self.normalizer = normalizer or RasterNormalizer()

    def analyze(self, dem: np.ndarray) -> np.ndarray:
        return self.normalizer.minmax(dem, invert=True)


class SlopeRiskAnalyzer:
    def __init__(self, normalizer: RasterNormalizer | None = None):
        self.normalizer = normalizer or RasterNormalizer()

    def analyze(self, slope: np.ndarray) -> np.ndarray:
        return self.normalizer.minmax(slope, invert=True)


class FlowAccumulationAnalyzer:
    def __init__(self, normalizer: RasterNormalizer | None = None):
        self.normalizer = normalizer or RasterNormalizer()

    def analyze(self, flow_accumulation: np.ndarray) -> np.ndarray:
        return self.normalizer.minmax(np.log1p(np.maximum(flow_accumulation, 0.0)))


class ImperviousnessAnalyzer:
    def __init__(self, normalizer: RasterNormalizer | None = None):
        self.normalizer = normalizer or RasterNormalizer()

    def analyze(self, imperviousness: np.ndarray) -> np.ndarray:
        return self.normalizer.minmax(imperviousness)


class RainfallAnalyzer:
    def __init__(self, normalizer: RasterNormalizer | None = None, thresholds: list[float] | None = None):
        self.normalizer = normalizer or RasterNormalizer()
        self.thresholds = thresholds

    def analyze(self, rainfall: np.ndarray) -> np.ndarray:
        if self.thresholds:
            x0, x1, x2, x3 = [float(value) for value in self.thresholds]
            risk = np.interp(rainfall.astype("float32"), [x0, x1, x2, x3], [0.0, 0.33, 0.66, 1.0])
            risk = np.clip(risk, 0.0, 1.0).astype("float32")
            risk[~np.isfinite(rainfall)] = np.nan
            return risk
        return self.normalizer.minmax(rainfall)
