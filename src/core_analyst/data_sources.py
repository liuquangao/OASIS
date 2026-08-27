from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
import json

import numpy as np
import rasterio
from rasterio.coords import BoundingBox
from rasterio.warp import transform as transform_coords


@dataclass
class RasterGrid:
    """Analysis-ready raster/grid representation shared by all data sources."""

    name: str
    data: np.ndarray
    profile: dict[str, Any]
    source_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def nodata(self) -> float | int | None:
        return self.profile.get("nodata")


class DataSource(ABC):
    @abstractmethod
    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        """Return an analysis-ready raster/grid."""


class StaticRasterSource(DataSource):
    def __init__(self, name: str, path: str | Path):
        self.name = name
        self.path = Path(path)

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if not self.path.exists():
            raise FileNotFoundError(f"Static raster not found for {self.name}: {self.path}")

        with rasterio.open(self.path) as dataset:
            data = dataset.read(1).astype("float32")
            profile = dataset.profile.copy()

        return RasterGrid(
            name=self.name,
            data=data,
            profile=profile,
            source_type="static",
            metadata={"path": str(self.path)},
        )


class RealTimeAPISource(DataSource):
    """Base class for dynamic sources that become analysis-ready grids."""

    source_name = "real_time_api"

    @abstractmethod
    def retrieve_observations(self) -> dict[str, Any]:
        """Retrieve or simulate current observations."""

    @abstractmethod
    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        """Convert observations into the same grid as the reference raster."""

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("Real-time sources require a reference raster grid for spatial alignment.")

        observations = self.retrieve_observations()
        grid = self.observations_to_grid(observations, reference).astype("float32")
        profile = reference.profile.copy()
        profile.update(dtype="float32", count=1)
        return RasterGrid(
            name="rainfall",
            data=grid,
            profile=profile,
            source_type="real_time",
            metadata={
                "source": self.source_name,
                "observations": observations,
                "prototype_note": (
                    "Prototype rainfall grid; not a scientifically validated rainfall field reconstruction."
                ),
            },
        )


class MockRainfallAPISource(RealTimeAPISource):
    """Synthetic dynamic rainfall source for demos that must run offline."""

    source_name = "mock_realtime_rainfall_api"

    def __init__(self, base_mm_per_hour: float = 18.0, multiplier: float = 1.0):
        self.base_mm_per_hour = base_mm_per_hour
        self.multiplier = multiplier

    def retrieve_observations(self) -> dict[str, Any]:
        value = self.base_mm_per_hour * self.multiplier
        return {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "rainfall_mm_per_hour": value,
            "stations": [
                {"station_id": "MOCK_GLASGOW_W", "x_weight": 0.25, "y_weight": 0.65, "rainfall_mm_per_hour": value * 0.85},
                {"station_id": "MOCK_GLASGOW_C", "x_weight": 0.52, "y_weight": 0.48, "rainfall_mm_per_hour": value * 1.10},
                {"station_id": "MOCK_GLASGOW_E", "x_weight": 0.78, "y_weight": 0.42, "rainfall_mm_per_hour": value * 0.95},
            ],
        }

    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        height, width = reference.data.shape
        yy, xx = np.mgrid[0:height, 0:width]
        total = np.zeros((height, width), dtype="float32")
        weights = np.zeros((height, width), dtype="float32")

        for station in observations["stations"]:
            sx = station["x_weight"] * (width - 1)
            sy = station["y_weight"] * (height - 1)
            dist2 = (xx - sx) ** 2 + (yy - sy) ** 2
            weight = 1.0 / np.maximum(dist2, 1.0)
            total += weight * float(station["rainfall_mm_per_hour"])
            weights += weight

        return total / np.maximum(weights, 1e-6)


class SEPARainfallAPISource(RealTimeAPISource):
    """SEPA rainfall station connector using public station/latest-rainfall JSON."""

    source_name = "sepa_rainfall_api"
    stations_url = "https://www2.sepa.org.uk/Rainfall/api/Stations"
    latest_station_url_template = "https://www2.sepa.org.uk/Rainfall/api/Stations/{station_no}"
    hourly_history_url_template = "https://www2.sepa.org.uk/Rainfall/api/Hourly/{station_no}?all=true"

    def __init__(
        self,
        station_numbers: list[str] | None = None,
        timeout_seconds: int = 20,
        max_age_hours: float = 6.0,
        discovery_buffer_meters: float = 0.0,
        include_hourly_history: bool = False,
        history_hours: int = 6,
    ):
        self.station_numbers = [str(station) for station in station_numbers] if station_numbers else []
        self.timeout_seconds = timeout_seconds
        self.max_age_hours = max_age_hours
        self.discovery_buffer_meters = discovery_buffer_meters
        self.include_hourly_history = include_hourly_history
        self.history_hours = history_hours

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("SEPA rainfall source requires a reference grid for station discovery and gridding.")
        if not self.station_numbers or self.station_numbers == ["auto"]:
            discovered = self.discover_stations(reference, buffer_meters=self.discovery_buffer_meters)
            self.station_numbers = [station["station_no"] for station in discovered]
            if not self.station_numbers:
                raise ValueError("No SEPA rainfall stations found inside the reference grid bounds.")
        return super().get_data(reference=reference)

    def retrieve_observations(self) -> dict[str, Any]:
        stations: list[dict[str, Any]] = []
        for station_no in self.station_numbers:
            url = self.latest_station_url_template.format(station_no=station_no)
            with urlopen(url, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))

            value = float(payload["itemValue"])
            timestamp = datetime.strptime(payload["itemDate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600.0
            if age_hours > self.max_age_hours:
                raise ValueError(
                    f"SEPA station {station_no} rainfall is stale: {payload['itemDate']} UTC "
                    f"({age_hours:.1f} hours old)."
                )
            station_record = {
                "station_no": station_no,
                "station_name": payload.get("station_name"),
                "latitude": float(payload["station_latitude"]),
                "longitude": float(payload["station_longitude"]),
                "timestamp_utc": timestamp.isoformat(),
                "rainfall_mm": value,
                "accumulation_hours": float(payload.get("accumRange", 1) or 1),
                "data_role": "latest_rainfall_observation",
                "metadata_endpoint": self.stations_url,
                "latest_observation_endpoint": url,
                "hourly_history_endpoint": self.hourly_history_url_template.format(station_no=station_no),
            }
            if self.include_hourly_history:
                history = self.retrieve_hourly_history(station_no)
                station_record.update(
                    {
                        "hourly_history": history["records"],
                        "recent_history_hours": self.history_hours,
                        "recent_history_total_mm": history["recent_total_mm"],
                    }
                )
            stations.append(station_record)

        return {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "data_roles": {
                "station_metadata": {
                    "classification": "slow_changing",
                    "endpoint": self.stations_url,
                    "purpose": "Station inventory, coordinates, names, and spatial filtering for the study area.",
                },
                "latest_rainfall_observation": {
                    "classification": "real_time_observation",
                    "endpoint_template": self.latest_station_url_template,
                    "purpose": "Current rainfall forcing at t0, converted to mm/hour for the rainfall grid.",
                },
                "hourly_rainfall_history": {
                    "classification": "recent_observational_history",
                    "endpoint_template": self.hourly_history_url_template,
                    "purpose": "Recent rainfall time series for antecedent wetness and short-term temporal state.",
                    "included_in_response": self.include_hourly_history,
                    "history_hours": self.history_hours,
                },
            },
            "stations": stations,
            "prototype_note": (
                "SEPA station observations are converted to a raster using simple inverse-distance weighting. "
                "Use more stations or radar rainfall for a scientifically stronger rainfall field."
            ),
        }

    def retrieve_hourly_history(self, station_no: str) -> dict[str, Any]:
        url = self.hourly_history_url_template.format(station_no=station_no)
        with urlopen(url, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        records: list[dict[str, Any]] = []
        for item in payload:
            try:
                timestamp = datetime.strptime(item["Timestamp"], "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                rainfall_mm = float(item["Value"])
            except (KeyError, TypeError, ValueError):
                continue
            records.append(
                {
                    "timestamp_utc": timestamp.isoformat(),
                    "rainfall_mm": rainfall_mm,
                    "data_role": "hourly_rainfall_history",
                }
            )

        records.sort(key=lambda record: record["timestamp_utc"], reverse=True)
        recent_records = records[: max(self.history_hours, 0)]
        recent_total = sum(float(record["rainfall_mm"]) for record in recent_records)
        return {
            "endpoint": url,
            "records": recent_records,
            "recent_total_mm": recent_total,
        }

    def discover_stations(self, reference: RasterGrid, buffer_meters: float = 0.0) -> list[dict[str, Any]]:
        with urlopen(self.stations_url, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bounds = BoundingBox(
            left=reference.profile["transform"].c,
            top=reference.profile["transform"].f,
            right=reference.profile["transform"].c + reference.data.shape[1] * reference.profile["transform"].a,
            bottom=reference.profile["transform"].f + reference.data.shape[0] * reference.profile["transform"].e,
        )
        left = min(bounds.left, bounds.right) - buffer_meters
        right = max(bounds.left, bounds.right) + buffer_meters
        bottom = min(bounds.bottom, bounds.top) - buffer_meters
        top = max(bounds.bottom, bounds.top) + buffer_meters

        discovered: list[dict[str, Any]] = []
        for station in payload:
            try:
                lon = float(station["station_longitude"])
                lat = float(station["station_latitude"])
                xs, ys = transform_coords("EPSG:4326", reference.profile["crs"], [lon], [lat])
                x, y = float(xs[0]), float(ys[0])
            except Exception:
                continue
            if left <= x <= right and bottom <= y <= top:
                discovered.append(
                    {
                        "station_no": str(station["station_no"]),
                        "station_name": station.get("station_name"),
                        "latitude": lat,
                        "longitude": lon,
                        "x": x,
                        "y": y,
                        "itemValue": station.get("itemValue"),
                        "itemDate": station.get("itemDate"),
                    }
                )
        return discovered

    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        stations = observations["stations"]
        if not stations:
            raise ValueError("No SEPA rainfall stations returned observations.")

        station_lons = [station["longitude"] for station in stations]
        station_lats = [station["latitude"] for station in stations]
        station_xs, station_ys = transform_coords("EPSG:4326", reference.profile["crs"], station_lons, station_lats)
        station_values = [
            station["rainfall_mm"] / max(station.get("accumulation_hours", 1.0), 1e-6)
            for station in stations
        ]

        valid_mask = np.isfinite(reference.data)
        if len(station_values) == 1:
            grid = np.full(reference.data.shape, station_values[0], dtype="float32")
            grid[~valid_mask] = np.nan
            return grid

        transform = reference.profile["transform"]
        height, width = reference.data.shape
        cols = np.arange(width, dtype="float32")
        rows = np.arange(height, dtype="float32")
        xs = transform.c + (cols + 0.5) * transform.a
        ys = transform.f + (rows + 0.5) * transform.e
        xx, yy = np.meshgrid(xs, ys)
        total = np.zeros(reference.data.shape, dtype="float32")
        weights = np.zeros(reference.data.shape, dtype="float32")

        for sx, sy, value in zip(station_xs, station_ys, station_values):
            dist2 = (xx - float(sx)) ** 2 + (yy - float(sy)) ** 2
            weight = 1.0 / np.maximum(dist2, 25.0)
            total += weight.astype("float32") * float(value)
            weights += weight.astype("float32")

        grid = total / np.maximum(weights, 1e-12)
        grid[~valid_mask] = np.nan
        return grid.astype("float32")


class MetOfficeSiteForecastRainfallSource(RealTimeAPISource):
    """Met Office SiteSpecificForecast rainfall source sampled over the study area."""

    source_name = "metoffice_site_specific_forecast"

    def __init__(
        self,
        sample_points: list[tuple[float, float]] | None = None,
        timesteps: str = "hourly",
        horizon_hours: int = 6,
    ):
        self.sample_points = sample_points
        self.timesteps = timesteps
        self.horizon_hours = horizon_hours
        self._reference: RasterGrid | None = None

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("Met Office site forecast source requires a reference raster grid.")
        self._reference = reference
        return super().get_data(reference=reference)

    def retrieve_observations(self) -> dict[str, Any]:
        from core_analyst.metoffice import MetOfficeSiteSpecificClient, extract_precipitation_series

        if self._reference is None:
            raise ValueError("Reference grid must be set before retrieving Met Office site forecasts.")

        points = self.sample_points or self._default_sample_points(self._reference)
        client = MetOfficeSiteSpecificClient()
        forecasts: list[dict[str, Any]] = []
        for lat, lon in points:
            payload = client.forecast(lat, lon, timesteps=self.timesteps, exclude_parameter_metadata=False)
            precip = extract_precipitation_series(payload)
            hourly_amounts = [
                row for row in precip
                if row["parameter"] == "totalPrecipAmount" and row.get("time")
            ]
            hourly_amounts = hourly_amounts[: max(self.horizon_hours, 1)]
            values = [float(row["value"]) for row in hourly_amounts]
            rainfall_mm_per_hour = max(values) if values else 0.0
            forecasts.append(
                {
                    "latitude": lat,
                    "longitude": lon,
                    "timesteps": self.timesteps,
                    "horizon_hours": self.horizon_hours,
                    "rainfall_mm_per_hour": rainfall_mm_per_hour,
                    "values_used": hourly_amounts,
                }
            )

        return {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "stations": forecasts,
            "prototype_note": (
                "Met Office SiteSpecificForecast point forecasts sampled across the study area; "
                "rainfall grid is IDW interpolation of maximum hourly totalPrecipAmount within the forecast horizon."
            ),
        }

    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        station_lons = [station["longitude"] for station in observations["stations"]]
        station_lats = [station["latitude"] for station in observations["stations"]]
        station_xs, station_ys = transform_coords("EPSG:4326", reference.profile["crs"], station_lons, station_lats)
        station_values = [station["rainfall_mm_per_hour"] for station in observations["stations"]]
        valid_mask = np.isfinite(reference.data)
        if not station_values:
            raise ValueError("No Met Office site-specific forecast samples available.")
        if len(station_values) == 1:
            grid = np.full(reference.data.shape, station_values[0], dtype="float32")
            grid[~valid_mask] = np.nan
            return grid

        transform = reference.profile["transform"]
        height, width = reference.data.shape
        cols = np.arange(width, dtype="float32")
        rows = np.arange(height, dtype="float32")
        xs = transform.c + (cols + 0.5) * transform.a
        ys = transform.f + (rows + 0.5) * transform.e
        xx, yy = np.meshgrid(xs, ys)
        total = np.zeros(reference.data.shape, dtype="float32")
        weights = np.zeros(reference.data.shape, dtype="float32")
        for sx, sy, value in zip(station_xs, station_ys, station_values):
            dist2 = (xx - float(sx)) ** 2 + (yy - float(sy)) ** 2
            weight = 1.0 / np.maximum(dist2, 25.0)
            total += weight.astype("float32") * float(value)
            weights += weight.astype("float32")
        grid = total / np.maximum(weights, 1e-12)
        grid[~valid_mask] = np.nan
        return grid.astype("float32")

    def _default_sample_points(self, reference: RasterGrid) -> list[tuple[float, float]]:
        transform = reference.profile["transform"]
        height, width = reference.data.shape
        sample_pixels = [
            (0.50, 0.50),
            (0.25, 0.25),
            (0.25, 0.75),
            (0.75, 0.25),
            (0.75, 0.75),
            (0.50, 0.25),
            (0.50, 0.75),
            (0.25, 0.50),
            (0.75, 0.50),
        ]
        xs: list[float] = []
        ys: list[float] = []
        for fx, fy in sample_pixels:
            col = fx * (width - 1)
            row = fy * (height - 1)
            xs.append(transform.c + (col + 0.5) * transform.a)
            ys.append(transform.f + (row + 0.5) * transform.e)
        lons, lats = transform_coords(reference.profile["crs"], "EPSG:4326", xs, ys)
        return [(float(lat), float(lon)) for lat, lon in zip(lats, lons)]


class SEPAWaterLevelAPISource(RealTimeAPISource):
    """SEPA river/tidal level observations from Time Series value-layer API."""

    source_name = "sepa_river_tidal_level_api"

    def __init__(
        self,
        timeseriesgroup_id: str = "41804",
        discovery_buffer_meters: float = 0.0,
        timeout_seconds: int = 30,
        risk_thresholds_m: list[float] | None = None,
    ):
        self.timeseriesgroup_id = timeseriesgroup_id
        self.discovery_buffer_meters = discovery_buffer_meters
        self.timeout_seconds = timeout_seconds
        self.risk_thresholds_m = risk_thresholds_m or [0.0, 1.0, 3.0, 5.0]
        self._reference: RasterGrid | None = None

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("SEPA water-level source requires a reference raster grid.")
        self._reference = reference
        return super().get_data(reference=reference)

    def retrieve_observations(self) -> dict[str, Any]:
        if self._reference is None:
            raise ValueError("Reference grid must be set before retrieving SEPA water levels.")
        url = (
            "https://timeseries.sepa.org.uk/KiWIS/KiWIS?"
            "service=kisters&type=queryServices&datasource=0"
            "&request=getTimeseriesValueLayer"
            f"&timeseriesgroup_id={self.timeseriesgroup_id}"
            "&format=geojson&returnfields=timestamp,ts_value,q_code"
        )
        with urlopen(url, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bounds = self._reference_bounds(self._reference, self.discovery_buffer_meters)
        stations: list[dict[str, Any]] = []
        for feature in payload.get("features", []):
            try:
                lon, lat = feature["geometry"]["coordinates"][:2]
                xs, ys = transform_coords("EPSG:4326", self._reference.profile["crs"], [lon], [lat])
                x, y = float(xs[0]), float(ys[0])
                value = float(feature["properties"]["ts_value"])
            except Exception:
                continue
            if bounds.left <= x <= bounds.right and bounds.bottom <= y <= bounds.top:
                stations.append(
                    {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "x": x,
                        "y": y,
                        "level_m": value,
                        "timestamp": feature["properties"].get("timestamp"),
                        "q_code": feature["properties"].get("q_code"),
                    }
                )

        return {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_url": url,
            "stations": stations,
            "risk_thresholds_m": self.risk_thresholds_m,
            "prototype_note": (
                "SEPA river/tidal level observations are converted to a relative risk grid using fixed "
                "prototype thresholds. Station-specific thresholds are needed for scientific flood triggering."
            ),
        }

    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        stations = observations["stations"]
        valid_mask = np.isfinite(reference.data)
        if not stations:
            grid = np.full(reference.data.shape, np.nan, dtype="float32")
            grid[valid_mask] = 0.0
            return grid

        station_lons = [station["longitude"] for station in stations]
        station_lats = [station["latitude"] for station in stations]
        station_xs, station_ys = transform_coords("EPSG:4326", reference.profile["crs"], station_lons, station_lats)
        station_risks = [
            float(np.interp(station["level_m"], self.risk_thresholds_m, [0.0, 0.33, 0.66, 1.0]))
            for station in stations
        ]
        grid = self._idw_grid(reference, station_xs, station_ys, station_risks)
        grid[~valid_mask] = np.nan
        return grid

    def _idw_grid(self, reference: RasterGrid, station_xs: list[float], station_ys: list[float], station_values: list[float]) -> np.ndarray:
        transform = reference.profile["transform"]
        height, width = reference.data.shape
        cols = np.arange(width, dtype="float32")
        rows = np.arange(height, dtype="float32")
        xs = transform.c + (cols + 0.5) * transform.a
        ys = transform.f + (rows + 0.5) * transform.e
        xx, yy = np.meshgrid(xs, ys)
        total = np.zeros(reference.data.shape, dtype="float32")
        weights = np.zeros(reference.data.shape, dtype="float32")
        for sx, sy, value in zip(station_xs, station_ys, station_values):
            dist2 = (xx - float(sx)) ** 2 + (yy - float(sy)) ** 2
            weight = 1.0 / np.maximum(dist2, 25.0)
            total += weight.astype("float32") * float(value)
            weights += weight.astype("float32")
        return (total / np.maximum(weights, 1e-12)).astype("float32")

    def _reference_bounds(self, reference: RasterGrid, buffer_meters: float) -> BoundingBox:
        transform = reference.profile["transform"]
        height, width = reference.data.shape
        left = transform.c
        top = transform.f
        right = transform.c + width * transform.a
        bottom = transform.f + height * transform.e
        return BoundingBox(
            left=min(left, right) - buffer_meters,
            bottom=min(bottom, top) - buffer_meters,
            right=max(left, right) + buffer_meters,
            top=max(bottom, top) + buffer_meters,
        )


def write_raster(path: str | Path, grid: RasterGrid, dtype: str = "float32") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = grid.profile.copy()
    profile.update(driver="GTiff", dtype=dtype, count=1)
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(grid.data.astype(dtype), 1)
