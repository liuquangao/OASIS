"""Official SEPA KiWIS hydrometric time-series integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from html import unescape
from math import ceil
import re
from typing import Any

import httpx

from oasis.domain.geo import haversine_km
from oasis.domain.rainfall import maximum_rolling_rainfall, rainfall_total
from oasis.domain.water_levels import relative_level_context
from oasis.models.hydrometry import (
    MonitoringStation,
    WaterLevelAreaSummary,
    WaterLevelReading,
    WaterLevelSummary,
)
from oasis.models.provenance import DataProvenance
from oasis.models.rainfall import (
    LatestRainfallAreaSummary,
    LatestRainfallObservation,
    RainfallAreaSummary,
    RainfallReading,
    RainfallStation,
    RainfallStationSummary,
)


SEPA_BASE_URL = "https://timeseries.sepa.org.uk/KiWIS/KiWIS"
SEPA_DOCS_URL = "https://timeseriesdoc.sepa.org.uk/api-documentation/"
SEPA_WATER_LEVELS_URL = "https://waterlevels.sepa.org.uk"
SEPA_LATEST_RAINFALL_URL = "https://www2.sepa.org.uk/Rainfall/api/Stations"

_NORMAL_RANGE_PATTERN = re.compile(
    r"Normal\s+range\s+(-?\d+(?:\.\d+)?)\s*m\s+to\s+(-?\d+(?:\.\d+)?)\s*m",
    re.IGNORECASE,
)


class SepaTimeSeriesError(RuntimeError):
    """Raised when a SEPA response cannot satisfy a typed tool request."""


class SepaTimeSeriesClient:
    """Translate the SEPA KiWIS API into provider-neutral domain models."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _query(self, **params: Any) -> Any:
        query = {
            "service": "kisters",
            "type": "queryServices",
            "datasource": "0",
            **params,
        }
        response = await self._client.get(SEPA_BASE_URL, params=query)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise SepaTimeSeriesError("SEPA returned a non-JSON response") from exc

    async def latest_rainfall_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 20,
        limit: int = 3,
    ) -> LatestRainfallAreaSummary:
        """Return the latest reported accumulation at nearby SEPA rain gauges."""

        if radius_km <= 0:
            raise ValueError("radius_km must be positive")
        if not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")

        response = await self._client.get(SEPA_LATEST_RAINFALL_URL)
        response.raise_for_status()
        try:
            rows = response.json()
        except ValueError as exc:
            raise SepaTimeSeriesError("SEPA returned a non-JSON response") from exc

        observations: list[LatestRainfallObservation] = []
        for row in rows:
            try:
                station_latitude = float(row["station_latitude"])
                station_longitude = float(row["station_longitude"])
                accumulation_mm = float(row["itemValue"])
                if accumulation_mm < 0:
                    continue
                accumulation_hours = float(row.get("accumRange") or 1)
                if accumulation_hours <= 0:
                    accumulation_hours = 1
                timestamp = datetime.strptime(
                    row["itemDate"], "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                distance_km = haversine_km(
                    latitude,
                    longitude,
                    station_latitude,
                    station_longitude,
                )
                if distance_km > radius_km:
                    continue
                observations.append(
                    LatestRainfallObservation(
                        station=RainfallStation(
                            station_no=str(row["station_no"]),
                            name=row["station_name"],
                            latitude=station_latitude,
                            longitude=station_longitude,
                            distance_km=round(distance_km, 3),
                        ),
                        timestamp=timestamp,
                        accumulation_mm=round(accumulation_mm, 3),
                        accumulation_hours=accumulation_hours,
                        rate_mm_per_hour=round(
                            accumulation_mm / accumulation_hours,
                            3,
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        observations.sort(key=lambda item: item.station.distance_km)
        observations = observations[:limit]
        warnings = [
            "Latest SEPA rain-gauge accumulations are local observations, not a forecast or an observation at the user's exact location.",
            "Use each station's timestamp and accumulation period; the latest published value may lag the present instant.",
        ]
        if observations:
            newest = max(item.timestamp for item in observations)
            if (datetime.now(timezone.utc) - newest).total_seconds() > 2 * 3600:
                warnings.append("The newest nearby SEPA rainfall observation is more than two hours old.")

        return LatestRainfallAreaSummary(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            station_count=len(observations),
            stations=observations,
            provenance=DataProvenance(
                provider="Scottish Environment Protection Agency (SEPA)",
                dataset="SEPA latest rainfall observations",
                source_url=SEPA_LATEST_RAINFALL_URL,
                retrieved_at=datetime.now(timezone.utc),
                observation_start=(
                    min(item.timestamp for item in observations)
                    if observations
                    else None
                ),
                observation_end=(
                    max(item.timestamp for item in observations)
                    if observations
                    else None
                ),
                licence=None,
                integration="oasis.integrations.sepa.SepaTimeSeriesClient.latest_rainfall_near_location",
            ),
            warnings=warnings,
        )

    async def list_level_stations(self) -> list[MonitoringStation]:
        rows = await self._query(
            request="getStationList",
            stationparameter_name="Level",
            returnfields=(
                "station_no,station_name,station_latitude,station_longitude,"
                "stationparameter_name,stationparameter_no"
            ),
            object_type="General",
            format="objson",
        )
        stations: list[MonitoringStation] = []
        for row in rows:
            try:
                stations.append(
                    MonitoringStation(
                        station_no=str(row["station_no"]),
                        name=row["station_name"],
                        latitude=float(row["station_latitude"]),
                        longitude=float(row["station_longitude"]),
                        parameter_name=row.get("stationparameter_name", "Level"),
                        parameter_no=row.get("stationparameter_no", "SG"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return stations

    async def nearby_level_stations(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 30,
        limit: int = 5,
    ) -> list[MonitoringStation]:
        stations = await self.list_level_stations()
        nearby: list[MonitoringStation] = []
        for station in stations:
            distance = haversine_km(
                latitude, longitude, station.latitude, station.longitude
            )
            if distance <= radius_km:
                nearby.append(station.model_copy(update={"distance_km": distance}))
        nearby.sort(key=lambda item: item.distance_km or 0)
        return nearby[:limit]

    async def _level_timeseries(self, station_no: str) -> dict[str, Any]:
        rows = await self._query(
            request="getTimeseriesList",
            station_no=station_no,
            stationparameter_no="SG",
            ts_shortname="15m.Cmd",
            returnfields=(
                "station_no,station_name,station_latitude,station_longitude,"
                "stationparameter_name,stationparameter_no,ts_name,ts_id,ts_path"
            ),
            format="objson",
        )
        if not rows:
            raise SepaTimeSeriesError(
                f"No 15-minute level time series found for station {station_no}"
            )
        return rows[0]

    async def _station_normal_range(
        self, station_no: str
    ) -> tuple[float, float] | None:
        """Read the station-specific normal range from SEPA's public page."""

        try:
            response = await self._client.get(
                f"{SEPA_WATER_LEVELS_URL}/Station/{station_no}"
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        match = _NORMAL_RANGE_PATTERN.search(unescape(response.text))
        if not match:
            return None
        low_m, high_m = (float(value) for value in match.groups())
        if high_m <= low_m:
            return None
        return low_m, high_m

    async def recent_level_summary(
        self, station_no: str, *, period_days: int = 1
    ) -> WaterLevelSummary:
        if not 1 <= period_days <= 31:
            raise ValueError("period_days must be between 1 and 31")
        series = await self._level_timeseries(station_no)
        payload, normal_range = await asyncio.gather(
            self._query(
                request="getTimeseriesValues",
                ts_id=series["ts_id"],
                period=f"P{period_days}D",
                returnfields="Timestamp,Value,Quality Code",
                format="json",
            ),
            self._station_normal_range(station_no),
        )
        if not payload or not payload[0].get("data"):
            raise SepaTimeSeriesError(
                f"No recent level values returned for station {station_no}"
            )
        table = payload[0]
        columns = [part.strip() for part in table["columns"].split(",")]
        readings: list[WaterLevelReading] = []
        for values in table["data"]:
            row = dict(zip(columns, values, strict=False))
            if row.get("Value") is None:
                continue
            readings.append(
                WaterLevelReading(
                    timestamp=datetime.fromisoformat(
                        str(row["Timestamp"]).replace("Z", "+00:00")
                    ),
                    value_m=float(row["Value"]),
                    quality_code=row.get("Quality Code"),
                )
            )
        if not readings:
            raise SepaTimeSeriesError(
                f"SEPA returned only missing values for station {station_no}"
            )
        readings.sort(key=lambda item: item.timestamp)
        first, latest = readings[0], readings[-1]
        elapsed_hours = (latest.timestamp - first.timestamp).total_seconds() / 3600
        change = latest.value_m - first.value_m
        rate = change / elapsed_hours if elapsed_hours > 0 else None
        if rate is None or abs(rate) < 0.005:
            trend = "stable"
        elif rate > 0:
            trend = "rising"
        else:
            trend = "falling"

        station = MonitoringStation(
            station_no=str(series["station_no"]),
            name=series["station_name"],
            latitude=float(series["station_latitude"]),
            longitude=float(series["station_longitude"]),
            parameter_name=series.get("stationparameter_name", "Level"),
            parameter_no=series.get("stationparameter_no", "SG"),
        )
        warnings = [
            "SEPA Low/Normal/High state is historical station context, not an operational flood warning."
        ]
        normal_low_m: float | None = None
        normal_high_m: float | None = None
        level_state = None
        relative_percent: float | None = None
        context_url: str | None = None
        if normal_range is not None:
            normal_low_m, normal_high_m = normal_range
            level_state, relative_percent = relative_level_context(
                latest.value_m, normal_low_m, normal_high_m
            )
            context_url = f"{SEPA_WATER_LEVELS_URL}/Station/{station_no}"
        else:
            warnings.append(
                "SEPA station normal range was unavailable; no Low/Normal/High context was assigned."
            )
        if any(reading.quality_code not in (None, 0, "0") for reading in readings):
            warnings.append(
                "SEPA quality codes are preserved; interpret them using SEPA metadata."
            )

        return WaterLevelSummary(
            station=station,
            reading_count=len(readings),
            recent_readings=readings[-8:],
            latest_value_m=latest.value_m,
            latest_timestamp=latest.timestamp,
            minimum_value_m=min(item.value_m for item in readings),
            maximum_value_m=max(item.value_m for item in readings),
            change_m=round(change, 4),
            change_per_hour_m=round(rate, 6) if rate is not None else None,
            trend=trend,
            normal_range_low_m=normal_low_m,
            normal_range_high_m=normal_high_m,
            relative_level_percent=relative_percent,
            level_state=level_state,
            level_context_source_url=context_url,
            provenance=DataProvenance(
                provider="Scottish Environment Protection Agency (SEPA)",
                dataset="SEPA hydrometric time-series data",
                source_url=SEPA_BASE_URL,
                retrieved_at=datetime.now(timezone.utc),
                observation_start=first.timestamp,
                observation_end=latest.timestamp,
                licence=None,
                integration="oasis.integrations.sepa.SepaTimeSeriesClient",
            ),
            warnings=warnings,
        )

    async def recent_water_levels_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 30,
        period_days: int = 1,
        limit: int = 3,
    ) -> WaterLevelAreaSummary:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude or longitude is outside WGS84 bounds")
        if not 0 < radius_km <= 200:
            raise ValueError("radius_km must be greater than 0 and at most 200")
        if not 1 <= period_days <= 31:
            raise ValueError("period_days must be between 1 and 31")
        if not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")

        nearby = await self.nearby_level_stations(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            limit=limit,
        )
        outcomes = await asyncio.gather(
            *(
                self.recent_level_summary(
                    station.station_no, period_days=period_days
                )
                for station in nearby
            ),
            return_exceptions=True,
        )
        summaries: list[WaterLevelSummary] = []
        warnings = [
            "SEPA Low/Normal/High states are historical station context, not operational flood warnings."
        ]
        for station, outcome in zip(nearby, outcomes, strict=False):
            if isinstance(outcome, BaseException):
                warnings.append(
                    f"Station {station.station_no} could not be summarized: {outcome}"
                )
                continue
            summaries.append(
                outcome.model_copy(
                    update={
                        "station": outcome.station.model_copy(
                            update={
                                "distance_km": (
                                    round(station.distance_km, 3)
                                    if station.distance_km is not None
                                    else None
                                )
                            }
                        )
                    }
                )
            )
        if not nearby:
            warnings.append("No SEPA river-level stations found within the radius.")

        return WaterLevelAreaSummary(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            station_count=len(summaries),
            stations=summaries,
            warnings=warnings,
        )

    async def _list_rainfall_series(self) -> list[dict[str, Any]]:
        rows = await self._query(
            request="getTimeseriesList",
            stationparameter_no="RE",
            ts_shortname="15m.Total",
            returnfields=(
                "station_no,station_name,station_latitude,station_longitude,"
                "stationparameter_name,stationparameter_no,ts_name,ts_id,ts_path"
            ),
            format="objson",
        )
        series_by_station: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                station_no = str(row["station_no"])
                float(row["station_latitude"])
                float(row["station_longitude"])
                str(row["ts_id"])
            except (KeyError, TypeError, ValueError):
                continue
            series_by_station.setdefault(station_no, row)
        return list(series_by_station.values())

    async def _recent_rainfall_station_summary(
        self,
        series: dict[str, Any],
        *,
        distance_km: float,
        period_hours: int,
    ) -> RainfallStationSummary:
        fetch_days = ceil(max(period_hours, 24) / 24)
        payload = await self._query(
            request="getTimeseriesValues",
            ts_id=series["ts_id"],
            period=f"P{fetch_days}D",
            returnfields="Timestamp,Value,Quality Code",
            format="json",
        )
        if not payload or not payload[0].get("data"):
            raise SepaTimeSeriesError(
                f"No recent rainfall values returned for station {series['station_no']}"
            )

        table = payload[0]
        columns = [part.strip() for part in table["columns"].split(",")]
        readings: list[RainfallReading] = []
        for values in table["data"]:
            row = dict(zip(columns, values, strict=False))
            if row.get("Value") is None:
                continue
            try:
                value_mm = float(row["Value"])
                if value_mm < 0:
                    continue
                readings.append(
                    RainfallReading(
                        timestamp=datetime.fromisoformat(
                            str(row["Timestamp"]).replace("Z", "+00:00")
                        ),
                        value_mm=value_mm,
                        quality_code=row.get("Quality Code"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not readings:
            raise SepaTimeSeriesError(
                f"SEPA returned no usable rainfall values for station {series['station_no']}"
            )

        readings.sort(key=lambda reading: reading.timestamp)
        latest = readings[-1].timestamp
        requested_start = latest.timestamp() - period_hours * 3600
        requested = [
            reading
            for reading in readings
            if reading.timestamp.timestamp() > requested_start
        ]
        if not requested:
            raise SepaTimeSeriesError(
                f"No rainfall values fell within the requested period for station {series['station_no']}"
            )

        quality_codes: list[int | str] = []
        for reading in requested:
            if (
                reading.quality_code is not None
                and reading.quality_code not in quality_codes
            ):
                quality_codes.append(reading.quality_code)

        return RainfallStationSummary(
            station=RainfallStation(
                station_no=str(series["station_no"]),
                name=series["station_name"],
                latitude=float(series["station_latitude"]),
                longitude=float(series["station_longitude"]),
                distance_km=round(distance_km, 3),
            ),
            requested_period_hours=period_hours,
            reading_count=len(requested),
            recent_readings=requested[-8:],
            latest_15min_mm=round(requested[-1].value_mm, 3),
            total_mm=round(sum(reading.value_mm for reading in requested), 3),
            last_1h_mm=round(rainfall_total(readings, hours=1), 3),
            last_3h_mm=round(rainfall_total(readings, hours=3), 3),
            last_6h_mm=round(rainfall_total(readings, hours=6), 3),
            last_24h_mm=round(rainfall_total(readings, hours=24), 3),
            maximum_15min_mm=round(
                max(reading.value_mm for reading in requested), 3
            ),
            maximum_1h_mm=round(
                maximum_rolling_rainfall(requested, hours=1), 3
            ),
            observation_start=requested[0].timestamp,
            latest_timestamp=latest,
            quality_codes=quality_codes,
        )

    async def recent_rainfall_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 20,
        period_hours: int = 24,
        limit: int = 3,
    ) -> RainfallAreaSummary:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude or longitude is outside WGS84 bounds")
        if not 0 < radius_km <= 200:
            raise ValueError("radius_km must be greater than 0 and at most 200")
        if not 1 <= period_hours <= 168:
            raise ValueError("period_hours must be between 1 and 168")
        if not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")

        candidates: list[tuple[float, dict[str, Any]]] = []
        for series in await self._list_rainfall_series():
            distance = haversine_km(
                latitude,
                longitude,
                float(series["station_latitude"]),
                float(series["station_longitude"]),
            )
            if distance <= radius_km:
                candidates.append((distance, series))
        candidates.sort(key=lambda item: item[0])
        selected = candidates[:limit]

        outcomes = await asyncio.gather(
            *(
                self._recent_rainfall_station_summary(
                    series,
                    distance_km=distance,
                    period_hours=period_hours,
                )
                for distance, series in selected
            ),
            return_exceptions=True,
        )
        summaries: list[RainfallStationSummary] = []
        warnings = [
            "Rain-gauge totals are local observations, not rainfall forecasts or flood-warning thresholds.",
            "Nearby gauges may not represent spatially variable rainfall across the full study area.",
        ]
        for (_, series), outcome in zip(selected, outcomes, strict=False):
            if isinstance(outcome, BaseException):
                warnings.append(
                    f"Station {series['station_no']} could not be summarized: {outcome}"
                )
            else:
                summaries.append(outcome)

        if not selected:
            warnings.append("No 15-minute SEPA rainfall series found within the radius.")
        if any(
            code not in (0, "0")
            for summary in summaries
            for code in summary.quality_codes
        ):
            warnings.append(
                "SEPA quality codes are preserved; interpret them using SEPA metadata."
            )

        return RainfallAreaSummary(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            station_count=len(summaries),
            stations=summaries,
            provenance=DataProvenance(
                provider="Scottish Environment Protection Agency (SEPA)",
                dataset="SEPA 15-minute rainfall time-series data",
                source_url=SEPA_BASE_URL,
                retrieved_at=datetime.now(timezone.utc),
                observation_start=(
                    min(summary.observation_start for summary in summaries)
                    if summaries
                    else None
                ),
                observation_end=(
                    max(summary.latest_timestamp for summary in summaries)
                    if summaries
                    else None
                ),
                licence=None,
                integration="oasis.integrations.sepa.SepaTimeSeriesClient",
            ),
            warnings=warnings,
        )
