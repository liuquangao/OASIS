from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import from_origin

from core_analyst.analysts.temporal_reference_flood import TemporalReferenceFloodAnalyst
from core_analyst.data_sources import (
    DataSource,
    MemoizedDataSource,
    MetOfficeSiteForecastRainfallSource,
    RasterGrid,
)


class ArraySource(DataSource):
    def __init__(self, values: np.ndarray):
        self.values = values.astype("float32")

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        assert reference is not None
        return RasterGrid(
            name="test",
            data=self.values,
            profile=reference.profile.copy(),
            source_type="test",
        )


class CountingSource(ArraySource):
    def __init__(self, values: np.ndarray, error: Exception | None = None):
        super().__init__(values)
        self.error = error
        self.calls = 0

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        self.calls += 1
        if self.error:
            raise self.error
        return super().get_data(reference)


def _reference() -> RasterGrid:
    return RasterGrid(
        name="reference",
        data=np.ones((1, 4), dtype="float32"),
        profile={"dtype": "float32", "nodata": np.nan},
        source_type="test",
    )


def _analyst(tmp_path) -> TemporalReferenceFloodAnalyst:
    return TemporalReferenceFloodAnalyst(
        "fluvial",
        {"rainfall_thresholds": [0, 5, 15, 30]},
        tmp_path,
    )


def test_rainfall_forcing_is_converted_from_mm_per_hour_to_relative_risk(tmp_path) -> None:
    analyst = _analyst(tmp_path)

    layers, metadata = analyst._read_forcings(
        {"rainfall_forecast": ArraySource(np.array([[0.0, 5.0, 15.0, 30.0]]))},
        _reference(),
    )

    np.testing.assert_allclose(
        layers["rainfall_forecast"],
        np.array([[0.0, 0.33, 0.66, 1.0]], dtype="float32"),
    )
    assert metadata["rainfall_forecast"]["processing"] == {
        "method": "piecewise_linear_rainfall_risk",
        "input_units": "mm/hour",
        "output_range": [0.0, 1.0],
        "thresholds_mm_per_hour": [0, 5, 15, 30],
    }


def test_non_rainfall_forcing_outside_relative_risk_range_fails_fast(tmp_path) -> None:
    analyst = _analyst(tmp_path)

    with pytest.raises(ValueError, match="must be a relative-risk raster in \\[0, 1\\]"):
        analyst._read_forcings(
            {"river_network": ArraySource(np.array([[0.0, 2.0, 0.0, 1.0]]))},
            _reference(),
        )


def test_light_forecast_rainfall_cannot_cross_medium_threshold_by_itself(tmp_path) -> None:
    analyst = _analyst(tmp_path)
    layers, _ = analyst._read_forcings(
        {"rainfall_forecast": ArraySource(np.full((1, 4), 2.2))},
        _reference(),
    )

    rainfall_contribution = layers["rainfall_forecast"] * 0.20

    assert float(np.max(rainfall_contribution)) < 0.33


def test_memoized_source_reuses_success_for_the_same_analysis_grid() -> None:
    source = CountingSource(np.ones((1, 4)))
    memoized = MemoizedDataSource(source)
    reference = _reference()

    assert memoized.get_data(reference) is memoized.get_data(reference)
    assert source.calls == 1


def test_memoized_source_does_not_repeat_a_failed_external_request() -> None:
    source = CountingSource(np.ones((1, 4)), error=RuntimeError("rate limited"))
    memoized = MemoizedDataSource(source)
    reference = _reference()

    with pytest.raises(RuntimeError, match="rate limited"):
        memoized.get_data(reference)
    with pytest.raises(RuntimeError, match="rate limited"):
        memoized.get_data(reference)
    assert source.calls == 1


def test_metoffice_default_sampling_grid_has_25_unique_points() -> None:
    reference = RasterGrid(
        name="reference",
        data=np.ones((100, 100), dtype="float32"),
        profile={
            "crs": "EPSG:4326",
            "transform": from_origin(-5.0, 56.0, 0.01, 0.01),
        },
        source_type="test",
    )

    points = MetOfficeSiteForecastRainfallSource()._default_sample_points(reference)

    assert len(points) == 25
    assert len(set(points)) == 25


def test_metoffice_sampling_grid_size_is_configurable() -> None:
    reference = RasterGrid(
        name="reference",
        data=np.ones((100, 100), dtype="float32"),
        profile={
            "crs": "EPSG:4326",
            "transform": from_origin(-5.0, 56.0, 0.01, 0.01),
        },
        source_type="test",
    )

    source = MetOfficeSiteForecastRainfallSource(sample_grid_size=7)

    assert len(source._default_sample_points(reference)) == 49


def test_metoffice_sampling_grid_rejects_excessive_request_counts() -> None:
    with pytest.raises(ValueError, match="between 3 and 9"):
        MetOfficeSiteForecastRainfallSource(sample_grid_size=10)
