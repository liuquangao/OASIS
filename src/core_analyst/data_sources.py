from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from zipfile import ZipFile
import csv
import json
import time

import numpy as np
import rasterio
from rasterio.coords import BoundingBox
from rasterio.errors import RasterioError
from rasterio.warp import transform as transform_coords

from core_analyst.study_area import StudyAreaBounds


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


class MemoizedDataSource(DataSource):
    """Reuse one external source result across analysts on the same raster grid."""

    def __init__(self, source: DataSource):
        self.source = source
        self._grids: dict[tuple[Any, ...], RasterGrid] = {}
        self._errors: dict[tuple[Any, ...], Exception] = {}

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("Memoized data sources require a reference raster grid.")
        key = (
            tuple(reference.data.shape),
            str(reference.profile.get("crs")),
            str(reference.profile.get("transform")),
        )
        if key in self._grids:
            return self._grids[key]
        if key in self._errors:
            raise self._errors[key]
        try:
            grid = self.source.get_data(reference=reference)
        except Exception as exc:
            self._errors[key] = exc
            raise
        self._grids[key] = grid
        return grid


class DynamicDataError(RuntimeError):
    """Raised when a dynamic source fails after bounded retry."""

    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        super().__init__(json.dumps(diagnostics))


# Reasons one input dataset can be genuinely unusable: a live feed that failed after
# retry, unreadable or missing files (OSError covers RasterioIOError), an unusable
# CRS/profile (RasterioError), and malformed contents (ValueError, KeyError).
DATASET_LOAD_ERRORS = (DynamicDataError, OSError, RasterioError, ValueError, KeyError)


class NRFADailyZipSource:
    """Read NRFA station daily time series from downloaded ZIP CSV bundles."""

    missing_tokens = {"", "NA", "N/A", "NaN", "nan", "null", "NULL", "-999", "-999.0"}

    def __init__(
        self,
        zip_path: str | Path,
        *,
        dataset_kind: str,
        hazard_type: str,
        temporal_state: str = "historical",
        evidence_type: str = "dynamic",
        source_url: str = "https://nrfa.ceh.ac.uk/data/search",
        license_name: str = "NRFA terms and conditions",
    ):
        self.zip_path = Path(zip_path)
        self.dataset_kind = dataset_kind
        self.hazard_type = hazard_type
        self.temporal_state = temporal_state
        self.evidence_type = evidence_type
        self.source_url = source_url
        self.license_name = license_name

    def station_ids(self) -> list[str]:
        return sorted(self._station_entries())

    def station_metadata(self, station_id: str) -> dict[str, Any]:
        parsed = self._parse_station(station_id, include_values=False)
        return parsed["metadata"]

    def daily_values(
        self,
        station_id: str,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> dict[str, Any]:
        parsed = self._parse_station(station_id, include_values=True)
        start = _coerce_date(start_date)
        end = _coerce_date(end_date)
        values = []
        missing_count = 0
        for row in parsed["values"]:
            row_date = row["date"]
            if start and row_date < start:
                continue
            if end and row_date > end:
                continue
            if row["value"] is None:
                missing_count += 1
            values.append(
                {
                    "date": row_date.isoformat(),
                    "value": row["value"],
                    "missing": row["value"] is None,
                }
            )
        return {
            "station_id": str(station_id),
            "metadata": parsed["metadata"],
            "values": values,
            "missing_value_count": missing_count,
            "record_count": len(values),
            "provenance": self._provenance(parsed["metadata"]),
        }

    def _station_entries(self) -> dict[str, str]:
        if not self.zip_path.exists():
            raise FileNotFoundError(f"NRFA ZIP not found: {self.zip_path}")
        entries: dict[str, str] = {}
        with ZipFile(self.zip_path) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                station = Path(name).stem.split("_")[0]
                entries[station] = name
        return entries

    def _parse_station(self, station_id: str, *, include_values: bool) -> dict[str, Any]:
        entries = self._station_entries()
        station_id = str(station_id)
        if station_id not in entries:
            raise KeyError(f"Station {station_id} not found in {self.zip_path}")
        metadata: dict[str, Any] = {
            "file": {},
            "station": {},
            "database": {},
            "dataType": {},
            "data": {},
        }
        values: list[dict[str, Any]] = []
        with ZipFile(self.zip_path) as archive:
            with archive.open(entries[station_id]) as raw:
                text = (line.decode("utf-8-sig", "replace") for line in raw)
                reader = csv.reader(text)
                in_data = False
                for row in reader:
                    if not row:
                        continue
                    if not in_data and len(row) >= 3 and row[0] in metadata:
                        metadata[row[0]][row[1]] = row[2]
                        continue
                    if not in_data and row[0] == "data" and len(row) >= 3:
                        metadata["data"][row[1]] = row[2]
                        continue
                    in_data = True
                    if not include_values:
                        continue
                    if len(row) < 2:
                        continue
                    parsed_date = _parse_iso_date(row[0])
                    if parsed_date is None:
                        continue
                    values.append({"date": parsed_date, "value": self._parse_value(row[1])})
        metadata["source_file"] = entries[station_id]
        metadata["zip_path"] = str(self.zip_path)
        metadata["dataset_kind"] = self.dataset_kind
        metadata["data_status"] = "historical"
        metadata["evidence_type"] = self.evidence_type
        metadata["temporal_state"] = self.temporal_state
        metadata["hazard_type"] = self.hazard_type
        metadata["analytical_position"] = self.analytical_position
        metadata["source_url"] = self.source_url
        metadata["license"] = self.license_name
        metadata["temporal_resolution"] = metadata.get("dataType", {}).get("period", "day")
        return {"metadata": metadata, "values": values}

    @property
    def analytical_position(self) -> dict[str, str]:
        return {
            "hazard_type": self.hazard_type,
            "temporal_state": self.temporal_state,
            "evidence_type": self.evidence_type,
        }

    def _parse_value(self, raw_value: str) -> float | None:
        value = raw_value.strip()
        if value in self.missing_tokens:
            return None
        try:
            number = float(value)
        except ValueError:
            return None
        if not np.isfinite(number):
            return None
        return number

    def _provenance(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "National River Flow Archive",
            "dataset_name": metadata.get("dataType", {}).get("name", self.dataset_kind),
            "source_url": self.source_url,
            "download_date": metadata.get("file", {}).get("timestamp"),
            "spatial_resolution": "station/catchment daily time series",
            "temporal_resolution": metadata.get("temporal_resolution", "day"),
            "crs": "British National Grid grid references in station metadata",
            "coverage": metadata.get("station", {}).get("name"),
            "processing_method": "Parsed downloaded NRFA ZIP CSV without spatial interpolation.",
            "license": self.license_name,
            "data_status": "historical",
            "analytical_position": self.analytical_position,
        }


class NRFAHistoricalRiverFlowSource(NRFADailyZipSource):
    """NRFA gauged daily river flow: Fluvial / Historical / Dynamic."""

    def __init__(self, zip_path: str | Path):
        super().__init__(
            zip_path,
            dataset_kind="gauged_daily_flow",
            hazard_type="fluvial",
        )


class NRFAHistoricalRainfallSource(NRFADailyZipSource):
    """NRFA catchment daily rainfall: Historical / Dynamic rainfall evidence."""

    def __init__(self, zip_path: str | Path, *, hazard_type: str = "pluvial"):
        super().__init__(
            zip_path,
            dataset_kind="catchment_daily_rainfall",
            hazard_type=hazard_type,
        )


class RealTimeAPISource(DataSource):
    """Base class for dynamic sources that become analysis-ready grids."""

    source_name = "real_time_api"
    output_name = "rainfall"
    data_status = "available"
    data_type = "observed"
    is_mock = False
    max_attempts = 1
    retry_backoff_seconds = 0.0

    @abstractmethod
    def retrieve_observations(self) -> dict[str, Any]:
        """Retrieve or simulate current observations."""

    @abstractmethod
    def observations_to_grid(self, observations: dict[str, Any], reference: RasterGrid) -> np.ndarray:
        """Convert observations into the same grid as the reference raster."""

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("Real-time sources require a reference raster grid for spatial alignment.")

        observations, diagnostics = self._retrieve_with_retries()
        grid = self.observations_to_grid(observations, reference).astype("float32")
        profile = reference.profile.copy()
        profile.update(dtype="float32", count=1)
        return RasterGrid(
            name=self.output_name,
            data=grid,
            profile=profile,
            source_type="real_time",
            metadata={
                "source": self.source_name,
                "availability": {
                    "dataset": self.source_name,
                    "status": self.data_status,
                    "type": self.data_type,
                    "source": self.source_name,
                    "is_mock": self.is_mock,
                },
                "observations": observations,
                "dynamic_data_diagnostics": diagnostics,
                "prototype_note": (
                    "Prototype rainfall grid; not a scientifically validated rainfall field reconstruction."
                ),
            },
        )

    def _retrieve_with_retries(self) -> tuple[dict[str, Any], dict[str, Any]]:
        attempts = self.max_attempts
        backoff = self.retry_backoff_seconds
        errors: list[dict[str, Any]] = []
        for attempt in range(1, attempts + 1):
            try:
                observations = self.retrieve_observations()
                if not observations:
                    raise ValueError("Dynamic source returned an empty response.")
                return observations, {
                    "source": self.source_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "attempts": attempt,
                    "final_status": "success",
                    "errors": errors,
                    "fallback_used": None,
                }
            except (OSError, ValueError, KeyError, TypeError) as exc:
                retry_after = None
                if getattr(exc, "code", None) == 429:
                    header = getattr(exc, "headers", {}).get("Retry-After")
                    if header:
                        try:
                            retry_after = max(float(header), 0.0)
                        except ValueError:
                            retry_after = None
                errors.append({
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "retry_after_seconds": retry_after,
                })
                if attempt < attempts:
                    delay = retry_after if retry_after is not None else backoff * attempt
                    if delay:
                        time.sleep(delay)
        raise DynamicDataError({
            "source": self.source_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attempts": attempts,
            "final_status": "failed",
            "errors": errors,
            "fallback_used": None,
        })

    def _idw_grid(
        self,
        reference: RasterGrid,
        station_xs: list[float],
        station_ys: list[float],
        station_values: list[float],
        minimum_distance_squared: float = 25.0,
    ) -> np.ndarray:
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
            weight = 1.0 / np.maximum(dist2, minimum_distance_squared)
            total += weight.astype("float32") * float(value)
            weights += weight.astype("float32")
        return (total / np.maximum(weights, 1e-12)).astype("float32")


class MockRainfallAPISource(RealTimeAPISource):
    """Synthetic dynamic rainfall source for demos that must run offline."""

    source_name = "mock_realtime_rainfall_api"
    data_type = "mock"
    is_mock = True

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
    data_type = "observed"
    stations_url = "https://www2.sepa.org.uk/Rainfall/api/Stations"
    latest_station_url_template = "https://www2.sepa.org.uk/Rainfall/api/Stations/{station_no}"
    hourly_history_url_template = "https://www2.sepa.org.uk/Rainfall/api/Hourly/{station_no}?all=true"

    def __init__(
        self,
        station_numbers: list[str] | None = None,
        timeout_seconds: int = 20,
        max_age_hours: float = 6.0,
        discovery_buffer_meters: float = 0.0,
        discovery_bounds: Any | None = None,
        include_hourly_history: bool = False,
        history_hours: int = 6,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.25,
    ):
        self.station_numbers = [str(station) for station in station_numbers] if station_numbers else []
        self.timeout_seconds = timeout_seconds
        self.max_age_hours = max_age_hours
        self.discovery_buffer_meters = discovery_buffer_meters
        self.discovery_bounds = discovery_bounds
        self.discovery_metadata = _discovery_metadata(discovery_bounds, discovery_buffer_meters)
        self.include_hourly_history = include_hourly_history
        self.history_hours = history_hours
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None:
            raise ValueError("SEPA rainfall source requires a reference grid for station discovery and gridding.")
        if not self.station_numbers or self.station_numbers == ["auto"]:
            discovered = self.discover_stations(
                reference,
                buffer_meters=self.discovery_buffer_meters,
                discovery_bounds=self.discovery_bounds,
            )
            self.station_numbers = [station["station_no"] for station in discovered]
            if not self.station_numbers:
                raise ValueError("No SEPA rainfall stations found inside the study-area discovery bounds.")
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
            "station_discovery": self.discovery_metadata,
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

    def discover_stations(
        self,
        reference: RasterGrid,
        buffer_meters: float = 0.0,
        discovery_bounds: Any | None = None,
    ) -> list[dict[str, Any]]:
        with urlopen(self.stations_url, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bounds = _discovery_bounds_for_reference(reference, discovery_bounds)
        left = min(bounds.left, bounds.right) - buffer_meters
        right = max(bounds.left, bounds.right) + buffer_meters
        bottom = min(bounds.bottom, bounds.top) - buffer_meters
        top = max(bounds.bottom, bounds.top) + buffer_meters

        discovered: list[dict[str, Any]] = []
        skipped = 0
        for station in payload:
            try:
                lon = float(station["station_longitude"])
                lat = float(station["station_latitude"])
                xs, ys = transform_coords("EPSG:4326", reference.profile["crs"], [lon], [lat])
                x, y = float(xs[0]), float(ys[0])
            except (KeyError, TypeError, ValueError):
                skipped += 1
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
        self.discovery_metadata["skipped_malformed_stations"] = skipped
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

        grid = self._idw_grid(reference, station_xs, station_ys, station_values)
        grid[~valid_mask] = np.nan
        return grid.astype("float32")


class MetOfficeSiteForecastRainfallSource(RealTimeAPISource):
    """Met Office SiteSpecificForecast rainfall source sampled over the study area."""

    source_name = "metoffice_site_specific_forecast"
    data_type = "forecast"

    def __init__(
        self,
        sample_points: list[tuple[float, float]] | None = None,
        sample_grid_size: int = 5,
        timesteps: str = "hourly",
        horizon_hours: int = 6,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.25,
    ):
        if not 3 <= sample_grid_size <= 9:
            raise ValueError("Met Office sample_grid_size must be between 3 and 9.")
        self.sample_points = sample_points
        self.sample_grid_size = sample_grid_size
        self.timesteps = timesteps
        self.horizon_hours = horizon_hours
        self._reference: RasterGrid | None = None
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

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
            if not values:
                continue
            rainfall_mm_per_hour = max(values)
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

        if not forecasts:
            raise ValueError("No Met Office precipitation forecast values were available for the study area samples.")

        return {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "stations": forecasts,
            "sampling": {
                "method": "regular_grid" if self.sample_points is None else "explicit_points",
                "grid_size": self.sample_grid_size if self.sample_points is None else None,
                "requested_point_count": len(points),
                "returned_point_count": len(forecasts),
            },
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

        grid = self._idw_grid(reference, station_xs, station_ys, station_values)
        grid[~valid_mask] = np.nan
        return grid.astype("float32")

    def _default_sample_points(self, reference: RasterGrid) -> list[tuple[float, float]]:
        transform = reference.profile["transform"]
        height, width = reference.data.shape
        fractions = np.linspace(0.1, 0.9, self.sample_grid_size)
        sample_pixels = [(fx, fy) for fy in fractions for fx in fractions]
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
    output_name = "water_level"
    data_type = "observed"

    def __init__(
        self,
        timeseriesgroup_id: str = "41804",
        discovery_buffer_meters: float = 0.0,
        discovery_bounds: Any | None = None,
        timeout_seconds: int = 30,
        risk_thresholds_m: list[float] | None = None,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.25,
    ):
        self.timeseriesgroup_id = timeseriesgroup_id
        self.discovery_buffer_meters = discovery_buffer_meters
        self.discovery_bounds = discovery_bounds
        self.discovery_metadata = _discovery_metadata(discovery_bounds, discovery_buffer_meters)
        self.timeout_seconds = timeout_seconds
        self.risk_thresholds_m = risk_thresholds_m or [0.0, 1.0, 3.0, 5.0]
        self._reference: RasterGrid | None = None
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

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

        bounds = self._reference_bounds(self._reference, self.discovery_buffer_meters, self.discovery_bounds)
        stations: list[dict[str, Any]] = []
        skipped = 0
        for feature in payload.get("features", []):
            try:
                lon, lat = feature["geometry"]["coordinates"][:2]
                xs, ys = transform_coords("EPSG:4326", self._reference.profile["crs"], [lon], [lat])
                x, y = float(xs[0]), float(ys[0])
                value = float(feature["properties"]["ts_value"])
            except (KeyError, TypeError, ValueError):
                skipped += 1
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
            "station_discovery": self.discovery_metadata,
            "stations": stations,
            "skipped_malformed_features": skipped,
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
            raise ValueError("No SEPA water-level stations found inside the study-area discovery bounds.")

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

    def _reference_bounds(self, reference: RasterGrid, buffer_meters: float, discovery_bounds: Any | None = None) -> BoundingBox:
        bounds = _discovery_bounds_for_reference(reference, discovery_bounds)
        left = bounds.left
        top = bounds.top
        right = bounds.right
        bottom = bounds.bottom
        return BoundingBox(
            left=min(left, right) - buffer_meters,
            bottom=min(bottom, top) - buffer_meters,
            right=max(left, right) + buffer_meters,
            top=max(bottom, top) + buffer_meters,
        )


def _reference_grid_bounds(reference: RasterGrid) -> BoundingBox:
    transform = reference.profile["transform"]
    height, width = reference.data.shape
    return BoundingBox(
        left=transform.c,
        top=transform.f,
        right=transform.c + width * transform.a,
        bottom=transform.f + height * transform.e,
    )


def _discovery_bounds_for_reference(reference: RasterGrid, discovery_bounds: StudyAreaBounds | None = None) -> BoundingBox:
    if discovery_bounds is None:
        return _reference_grid_bounds(reference)
    return discovery_bounds.for_crs(reference.profile.get("crs"))


def _discovery_metadata(discovery_bounds: StudyAreaBounds | None, buffer_meters: float) -> dict[str, Any]:
    if discovery_bounds is None:
        return {
            "bounds_source": "reference_grid",
            "additional_buffer_meters": buffer_meters,
        }
    bounds = discovery_bounds.bounds
    return {
        "bounds_source": discovery_bounds.path,
        "bounds_name": discovery_bounds.name,
        "bounds": {
            "left": bounds.left,
            "bottom": bounds.bottom,
            "right": bounds.right,
            "top": bounds.top,
        },
        "crs": None if discovery_bounds.crs is None else str(discovery_bounds.crs),
        "additional_buffer_meters": buffer_meters,
        "metadata": discovery_bounds.metadata,
    }


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _coerce_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    parsed = _parse_iso_date(value)
    if parsed is None:
        raise ValueError(f"Expected ISO date, got {value!r}")
    return parsed


def write_raster(path: str | Path, grid: RasterGrid, dtype: str = "float32") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = grid.profile.copy()
    profile.update(driver="GTiff", dtype=dtype, count=1)
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(grid.data.astype(dtype), 1)
