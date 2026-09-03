from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rasterio.coords import BoundingBox
from rasterio.warp import transform as transform_coords

from core_analyst.study_area import StudyAreaBounds, load_glasgow_1km_buffer_bounds


EA_FLOOD_MONITORING_BASE = "https://environment.data.gov.uk/flood-monitoring"
EA_TIDE_GAUGE_DOC = "https://environment.data.gov.uk/flood-monitoring/doc/tidegauge"
EA_FLOOD_MONITORING_DOC = "https://environment.data.gov.uk/flood-monitoring/doc/reference"
ADMIRALTY_DEVELOPER_URL = "https://developer.admiralty.co.uk/"
DEFAULT_SEARCH_RADIUS_KM = 120.0


@dataclass(frozen=True)
class CoastalDynamicConfig:
    input_dir: str | Path = "Input"
    historical_hours: int = 24
    search_radius_km: float = DEFAULT_SEARCH_RADIUS_KM
    candidate_station_reference: str | None = "E74039"
    timeout_seconds: int = 20


class EnvironmentAgencyTideGaugeClient:
    def __init__(self, base_url: str = EA_FLOOD_MONITORING_BASE, timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def discover_stations(
        self,
        study_area: StudyAreaBounds | None,
        *,
        search_radius_km: float = DEFAULT_SEARCH_RADIUS_KM,
        candidate_station_reference: str | None = None,
    ) -> dict[str, Any]:
        center_lon, center_lat = _study_area_center_lon_lat(study_area)
        payload = self._get_json(
            f"{self.base_url}/id/stations?"
            + urlencode({"type": "TideGauge", "lat": center_lat, "long": center_lon, "dist": search_radius_km})
        )
        stations = [_normalise_station(item, study_area) for item in _items(payload)]
        stations = [station for station in stations if station.get("station_reference")]
        stations.sort(key=lambda item: (_station_relevance_rank(item), item.get("distance_to_study_area_km") or math.inf))

        candidate_evaluation = None
        if candidate_station_reference:
            candidate_evaluation = self.evaluate_station(candidate_station_reference, study_area)

        return {
            "source_name": "Environment Agency Tide Gauge",
            "source_url": f"{self.base_url}/id/stations",
            "documentation": EA_TIDE_GAUGE_DOC,
            "retrieved_at": _utc_now(),
            "status": "available" if stations else "unavailable",
            "type": "observed",
            "flood_type": "coastal",
            "temporal_states": ["historical", "current"],
            "data_category": "dynamic",
            "study_area": _study_area_metadata(study_area),
            "search": {
                "method": "EA TideGauge stations within a configured radius of the Glasgow 1km-buffer study-area centre, ranked with Clyde/Firth-of-Clyde relevance and distance-to-boundary metadata.",
                "radius_km": search_radius_km,
                "center": {"latitude": center_lat, "longitude": center_lon},
            },
            "stations": stations,
            "selected_station": stations[0] if stations else None,
            "candidate_station_evaluation": candidate_evaluation,
            "reason_if_unavailable": None if stations else "No EA TideGauge stations were returned for the configured Glasgow/Clyde search.",
        }

    def evaluate_station(self, station_reference: str, study_area: StudyAreaBounds | None) -> dict[str, Any]:
        try:
            station = self.station_metadata(station_reference)
        except Exception as exc:
            return {
                "station_reference": station_reference,
                "status": "unavailable",
                "adopted_as_candidate": False,
                "reason": str(exc),
            }
        normalised = _normalise_station(station, study_area)
        adopted = _station_relevance_rank(normalised) <= 1 or (normalised.get("distance_to_study_area_km") or math.inf) <= DEFAULT_SEARCH_RADIUS_KM
        return {
            **normalised,
            "status": "available",
            "adopted_as_candidate": adopted,
            "reason": (
                "Station has Clyde/Firth-of-Clyde relevance or lies within the configured coastal search radius."
                if adopted
                else "Station metadata was available but it was not spatially/relevantly close enough for Glasgow/Clyde evidence."
            ),
        }

    def station_metadata(self, station_reference: str) -> dict[str, Any]:
        payload = self._get_json(f"{self.base_url}/id/stations/{station_reference}")
        return payload.get("items", {})

    def latest_reading(self, measure_id: str) -> dict[str, Any]:
        payload = self._get_json(f"{measure_id}/readings?" + urlencode({"_limit": 1, "latest": ""}))
        readings = _items(payload)
        return readings[0] if readings else {}

    def historical_readings(self, measure_id: str, *, hours: int = 24, limit: int = 500) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=max(hours, 1))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = self._get_json(f"{measure_id}/readings?" + urlencode({"since": since, "_limit": limit}))
        return _items(payload)

    def _get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class EnvironmentAgencyFloodMonitoringClient:
    def __init__(self, base_url: str = EA_FLOOD_MONITORING_BASE, timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def current_evidence(self, study_area: StudyAreaBounds | None, *, search_radius_km: float = DEFAULT_SEARCH_RADIUS_KM) -> dict[str, Any]:
        center_lon, center_lat = _study_area_center_lon_lat(study_area)
        floods_url = f"{self.base_url}/id/floods?" + urlencode({"lat": center_lat, "long": center_lon, "dist": search_radius_km})
        areas_url = f"{self.base_url}/id/floodAreas?" + urlencode({"lat": center_lat, "long": center_lon, "dist": search_radius_km})
        floods = _items(self._get_json(floods_url))
        flood_areas = _items(self._get_json(areas_url))
        return {
            "source_name": "Environment Agency Flood Monitoring",
            "source_url": self.base_url,
            "documentation": EA_FLOOD_MONITORING_DOC,
            "retrieved_at": _utc_now(),
            "status": "available",
            "type": "observed",
            "flood_type": "coastal",
            "temporal_state": "current",
            "data_category": "dynamic",
            "variables": {
                "current_flood_warning": [_warning_record(item) for item in floods if item],
                "current_flood_alert": [_area_record(item) for item in flood_areas if item],
                "current_water_level": {
                    "status": "not_queried_here",
                    "reason": "Water-level readings remain separate station observations; flood warnings/alerts are not water levels.",
                },
            },
            "semantic_guardrails": [
                "flood_warning_is_not_water_level",
                "flood_alert_is_not_tide_height",
            ],
        }

    def _get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class AdmiraltyTidalApiClient:
    def __init__(
        self,
        api_key_env: str = "ADMIRALTY_API_KEY",
        base_url_env: str = "ADMIRALTY_TIDAL_API_BASE_URL",
        timeout_seconds: int = 20,
    ):
        self.api_key_env = api_key_env
        self.base_url_env = base_url_env
        self.timeout_seconds = timeout_seconds

    @property
    def api_key(self) -> str | None:
        for key in (self.api_key_env, "UKHO_API_KEY", "ADMIRALTY_TIDAL_API_KEY", "ADMIRALTY_SUBSCRIPTION_KEY"):
            value = os.getenv(key, "").strip()
            if value:
                return value
        return None

    @property
    def base_url(self) -> str | None:
        return os.getenv(self.base_url_env, "").strip() or None

    def future_prediction(self, station_id: str | None = None, *, hours: int = 24) -> dict[str, Any]:
        if not self.api_key:
            return _admiralty_unavailable("credentials_not_configured")
        if not self.base_url:
            return _admiralty_unavailable("base_url_not_configured")
        if not station_id:
            return _admiralty_unavailable("station_not_configured")
        url = self.base_url.rstrip("/") + "/predictions?" + urlencode({"station": station_id, "durationHours": hours})
        request = Request(url, headers={"Ocp-Apim-Subscription-Key": self.api_key, "Accept": "application/json"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "source_name": "ADMIRALTY Tidal API",
            "source_url": url,
            "documentation": ADMIRALTY_DEVELOPER_URL,
            "retrieved_at": _utc_now(),
            "status": "available",
            "type": "forecast",
            "flood_type": "coastal",
            "temporal_state": "future",
            "data_category": "dynamic",
            "semantic_role": "predicted_tidal_height",
            "not_a": ["storm_surge_forecast", "coastal_flood_forecast", "flood_extent_forecast"],
            "station": station_id,
            "prediction": payload,
        }


def build_coastal_dynamic_evidence(config: CoastalDynamicConfig | None = None) -> dict[str, Any]:
    config = config or CoastalDynamicConfig()
    study_area = load_glasgow_1km_buffer_bounds(config.input_dir)
    tide_client = EnvironmentAgencyTideGaugeClient(timeout_seconds=config.timeout_seconds)
    flood_client = EnvironmentAgencyFloodMonitoringClient(timeout_seconds=config.timeout_seconds)

    discovery = _safe_call(lambda: tide_client.discover_stations(
        study_area,
        search_radius_km=config.search_radius_km,
        candidate_station_reference=config.candidate_station_reference,
    ))
    selected = discovery.get("selected_station") if discovery.get("status") in {"available", "partial"} else None
    measure = _preferred_measure(selected) if selected else None

    historical = _coastal_group("historical")
    current = _coastal_group("current")
    future = _coastal_group("future")

    historical["datasets"].append(_historical_tide_dataset(tide_client, selected, measure, config.historical_hours))
    current["datasets"].append(_current_tide_dataset(tide_client, selected, measure))
    current["datasets"].append(_safe_call(lambda: flood_client.current_evidence(study_area, search_radius_km=config.search_radius_km)))
    future["datasets"].append(AdmiraltyTidalApiClient(timeout_seconds=config.timeout_seconds).future_prediction(station_id=None))

    return {
        "flood_type": "coastal",
        "data_category": "dynamic",
        "source_flags": {
            "point_observations_are_not_rasters": True,
            "does_not_modify_hazard_score": True,
            "bbc_tide_tables_not_used": True,
        },
        "station_discovery": discovery,
        "historical": _finalise_group(historical),
        "current": _finalise_group(current),
        "future": _finalise_group(future),
        "remaining_unavailable": [
            "storm_surge_forecast",
            "wave_exposure",
            "coastal_defence",
            "coastal_hydraulic_forecast",
        ],
    }


def _historical_tide_dataset(client: EnvironmentAgencyTideGaugeClient, station: dict[str, Any] | None, measure: dict[str, Any] | None, hours: int) -> dict[str, Any]:
    if not station or not measure:
        return _ea_tide_unavailable("historical", "no_relevant_tide_gauge_measure_discovered")
    try:
        readings = client.historical_readings(measure["measure_id"], hours=hours)
    except Exception as exc:
        return _ea_tide_unavailable("historical", str(exc))
    normalised = [_reading_record(reading, station, measure) for reading in readings]
    return {
        "source_name": "Environment Agency Tide Gauge",
        "source_url": measure["measure_id"],
        "documentation": EA_TIDE_GAUGE_DOC,
        "retrieved_at": _utc_now(),
        "status": "available" if normalised else "unavailable",
        "type": "observed",
        "temporal_state": "historical",
        "data_category": "dynamic",
        "semantic_role": "historical_tide_observations",
        "window_hours": hours,
        "station": station,
        "readings": normalised,
        "reason_if_unavailable": None if normalised else "EA Tide Gauge returned no historical readings for the configured window.",
    }


def _current_tide_dataset(client: EnvironmentAgencyTideGaugeClient, station: dict[str, Any] | None, measure: dict[str, Any] | None) -> dict[str, Any]:
    if not station or not measure:
        return _ea_tide_unavailable("current", "no_relevant_tide_gauge_measure_discovered")
    try:
        reading = client.latest_reading(measure["measure_id"])
    except Exception as exc:
        return _ea_tide_unavailable("current", str(exc))
    record = _reading_record(reading, station, measure) if reading else None
    return {
        "source_name": "Environment Agency Tide Gauge",
        "source_url": measure["measure_id"],
        "documentation": EA_TIDE_GAUGE_DOC,
        "retrieved_at": _utc_now(),
        "status": "available" if record else "unavailable",
        "type": "observed",
        "temporal_state": "current",
        "data_category": "dynamic",
        "semantic_role": "current_tide_observation",
        "station": station,
        "reading": record,
        "reason_if_unavailable": None if record else "EA Tide Gauge returned no latest reading.",
    }


def _normalise_station(station: dict[str, Any], study_area: StudyAreaBounds | None) -> dict[str, Any]:
    measures = [_normalise_measure(item) for item in _listify(station.get("measures"))]
    lon = _float_or_none(station.get("long"))
    lat = _float_or_none(station.get("lat"))
    easting = _float_or_none(station.get("easting"))
    northing = _float_or_none(station.get("northing"))
    if (easting is None or northing is None) and lon is not None and lat is not None:
        xs, ys = transform_coords("EPSG:4326", "EPSG:27700", [lon], [lat])
        easting, northing = float(xs[0]), float(ys[0])
    distance = _distance_to_bounds_km(easting, northing, study_area.bounds if study_area else None)
    return {
        "station_reference": station.get("stationReference") or Path(str(station.get("@id", ""))).name,
        "label": station.get("label"),
        "latitude": lat,
        "longitude": lon,
        "easting": easting,
        "northing": northing,
        "distance_to_study_area_km": distance,
        "catchment_name": station.get("catchmentName"),
        "ea_region_name": station.get("eaRegionName"),
        "status": station.get("status"),
        "measures": measures,
        "preferred_measure": _preferred_measure({"measures": measures}),
        "relevance": _relevance_note(station, distance),
    }


def _normalise_measure(measure: dict[str, Any]) -> dict[str, Any]:
    return {
        "measure_id": measure.get("@id"),
        "label": measure.get("label"),
        "parameter": measure.get("parameter"),
        "qualifier": measure.get("qualifier"),
        "unit": measure.get("unitName") or measure.get("unit"),
        "period": measure.get("period"),
        "value_type": measure.get("valueType"),
        "datum": measure.get("datumType") or measure.get("datum"),
    }


def _preferred_measure(station: dict[str, Any] | None) -> dict[str, Any] | None:
    if not station:
        return None
    measures = station.get("measures") or []
    scored = sorted(measures, key=lambda item: (0 if "maod" in str(item.get("measure_id", "")).lower() else 1, 0 if "level" in str(item.get("parameter", "")).lower() else 1))
    return scored[0] if scored else None


def _reading_record(reading: dict[str, Any], station: dict[str, Any], measure: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _iso_utc(reading.get("dateTime")),
        "value": _float_or_none(reading.get("value")),
        "unit": measure.get("unit"),
        "datum": measure.get("datum"),
        "station": {
            "station_reference": station.get("station_reference"),
            "label": station.get("label"),
            "latitude": station.get("latitude"),
            "longitude": station.get("longitude"),
        },
        "measure": measure,
    }


def _ea_tide_unavailable(temporal_state: str, reason: str) -> dict[str, Any]:
    return {
        "source_name": "Environment Agency Tide Gauge",
        "documentation": EA_TIDE_GAUGE_DOC,
        "retrieved_at": _utc_now(),
        "status": "unavailable",
        "type": "observed",
        "temporal_state": temporal_state,
        "data_category": "dynamic",
        "reason_if_unavailable": reason,
    }


def _admiralty_unavailable(reason: str) -> dict[str, Any]:
    return {
        "source_name": "ADMIRALTY Tidal API",
        "documentation": ADMIRALTY_DEVELOPER_URL,
        "retrieved_at": _utc_now(),
        "status": "unavailable",
        "type": "forecast",
        "flood_type": "coastal",
        "temporal_state": "future",
        "data_category": "dynamic",
        "semantic_role": "predicted_tidal_height",
        "not_a": ["storm_surge_forecast", "coastal_flood_forecast", "flood_extent_forecast"],
        "reason_if_unavailable": reason,
    }


def _safe_call(fn) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return {"status": "unavailable", "type": "observed", "reason_if_unavailable": str(exc), "retrieved_at": _utc_now()}


def _coastal_group(temporal_state: str) -> dict[str, Any]:
    return {"flood_type": "coastal", "temporal_state": temporal_state, "data_category": "dynamic", "datasets": []}


def _finalise_group(group: dict[str, Any]) -> dict[str, Any]:
    statuses = [dataset.get("status", "unavailable") for dataset in group["datasets"]]
    if statuses and all(status == "available" for status in statuses):
        status = "available"
    elif any(status in {"available", "partial"} for status in statuses):
        status = "partial"
    else:
        status = "unavailable"
    return {**group, "status": status}


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items", [])
    if isinstance(items, list):
        return items
    if isinstance(items, dict):
        return [items]
    return []


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _study_area_center_lon_lat(study_area: StudyAreaBounds | None) -> tuple[float, float]:
    if study_area is None:
        return -4.2518, 55.8642
    bounds = study_area.for_crs("EPSG:4326")
    return (float(bounds.left + bounds.right) / 2.0, float(bounds.bottom + bounds.top) / 2.0)


def _study_area_metadata(study_area: StudyAreaBounds | None) -> dict[str, Any]:
    if study_area is None:
        return {"status": "unavailable", "fallback_center": {"latitude": 55.8642, "longitude": -4.2518}}
    return {
        "name": study_area.name,
        "path": study_area.path,
        "crs": str(study_area.crs) if study_area.crs else None,
        "bounds": {
            "left": study_area.bounds.left,
            "bottom": study_area.bounds.bottom,
            "right": study_area.bounds.right,
            "top": study_area.bounds.top,
        },
        "metadata": study_area.metadata,
    }


def _distance_to_bounds_km(x: float | None, y: float | None, bounds: BoundingBox | None) -> float | None:
    if x is None or y is None or bounds is None:
        return None
    dx = max(bounds.left - x, 0.0, x - bounds.right)
    dy = max(bounds.bottom - y, 0.0, y - bounds.top)
    return math.hypot(dx, dy) / 1000.0


def _station_relevance_rank(station: dict[str, Any]) -> int:
    text = " ".join(str(station.get(key, "")) for key in ("label", "catchment_name", "ea_region_name")).lower()
    if any(token in text for token in ("clyde", "millport", "firth")):
        return 0
    if (station.get("longitude") or 0) < -4.0:
        return 1
    return 2


def _relevance_note(station: dict[str, Any], distance_km: float | None) -> dict[str, Any]:
    text = " ".join(str(station.get(key, "")) for key in ("label", "catchmentName", "eaRegionName")).lower()
    return {
        "clyde_or_firth_label_match": any(token in text for token in ("clyde", "millport", "firth")),
        "west_of_glasgow_longitude_hint": (_float_or_none(station.get("long")) or 0.0) < -4.0,
        "distance_to_study_area_km": distance_km,
    }


def _warning_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("@id"),
        "description": item.get("description"),
        "severity": item.get("severity"),
        "severity_level": item.get("severityLevel"),
        "message": item.get("message"),
        "time_raised": _iso_utc(item.get("timeRaised")),
        "time_message_changed": _iso_utc(item.get("timeMessageChanged")),
        "flood_area": item.get("floodArea"),
    }


def _area_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("@id"),
        "notation": item.get("notation"),
        "label": item.get("label"),
        "county": item.get("county"),
        "river_or_sea": item.get("riverOrSea"),
        "polygon": item.get("polygon"),
    }


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
