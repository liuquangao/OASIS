from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class MetOfficeAtmosphericClient:
    """Small Weather DataHub Atmospheric Models client.

    API keys must be supplied through an environment variable. Do not commit or
    paste keys into source files.
    """

    api_key_env: str = "METOFFICE_API_KEY"
    base_url: str = "https://data.hub.api.metoffice.gov.uk/atmospheric-models/1.0.0"
    data_spec: str = "1.1.0"
    timeout_seconds: int = 30

    @property
    def api_key(self) -> str:
        value = os.getenv(self.api_key_env, "").strip()
        if not value:
            raise RuntimeError(
                f"Met Office API key not found. Set ${self.api_key_env} in your shell, "
                f"for example PowerShell: $env:{self.api_key_env}='...'"
            )
        return value

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "apikey": self.api_key,
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def list_orders(self) -> dict[str, Any]:
        return self._get_json("/orders", {"detail": "MINIMAL"})

    def latest_order(self, order_name: str, run_filter: str | None = "latest") -> dict[str, Any]:
        params = {"dataSpec": self.data_spec, "detail": "MINIMAL"}
        if run_filter:
            params["runfilter"] = run_filter
        return self._get_json(f"/orders/{order_name}/latest", params)

    def list_runs(self, model_name: str) -> dict[str, Any]:
        return self._get_json(f"/runs/{model_name}", {"sort": "RUNDATETIME"})


@dataclass
class MetOfficeMapImagesClient(MetOfficeAtmosphericClient):
    """Weather DataHub Map Images client.

    Map images are useful for visualization or WebGIS overlays. They should not
    be treated as numeric rainfall rasters unless a separate, documented
    georeferencing and legend-to-value conversion workflow is implemented.
    """

    api_key_env: str = "METOFFICE_MAP_API_KEY"
    base_url: str = "https://data.hub.api.metoffice.gov.uk/map-images/1.0.0"


@dataclass
class MetOfficeNSWWSClient:
    """Met Office National Severe Weather Warnings Service client.

    NSWWS is an Atom feed entry point with linked warning endpoints. It should
    be used as a warning/context layer, not as a numeric rainfall raster.
    """

    api_key_env: str = "METOFFICE_NSWWWS_API_KEY"
    feed_url_env: str = "METOFFICE_NSWWWS_FEED_URL"
    timeout_seconds: int = 30

    @property
    def api_key(self) -> str:
        value = os.getenv(self.api_key_env, "").strip()
        if not value:
            raise RuntimeError(
                f"Met Office NSWWS API key not found. Set ${self.api_key_env} in your shell, "
                f"for example PowerShell: $env:{self.api_key_env}='...'"
            )
        return value

    @property
    def feed_url(self) -> str:
        value = os.getenv(self.feed_url_env, "").strip()
        if not value:
            raise RuntimeError(
                f"Met Office NSWWS Atom Feed URL not found. Set ${self.feed_url_env}. "
                "The NSWWS documentation says this feed URL is supplied with API access."
            )
        return value

    def _request_bytes(self, url: str, accept: str = "application/json") -> bytes:
        request = Request(
            url,
            headers={
                "Accept": accept,
                "x-api-key": self.api_key,
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()

    def atom_feed(self) -> str:
        return self._request_bytes(self.feed_url, accept="application/atom+xml").decode("utf-8")

    def atom_links(self) -> list[dict[str, str]]:
        root = ET.fromstring(self.atom_feed())
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        links: list[dict[str, str]] = []
        for link in root.findall(".//atom:link", ns):
            href = link.attrib.get("href")
            if href:
                links.append(
                    {
                        "href": href,
                        "rel": link.attrib.get("rel", ""),
                        "type": link.attrib.get("type", ""),
                        "title": link.attrib.get("title", ""),
                    }
                )
        return links

    def current_warning_links(self) -> list[dict[str, str]]:
        return [
            link for link in self.atom_links()
            if "geo+json" in link.get("type", "").lower() or "json" in link.get("type", "").lower()
        ]

    def fetch_json(self, url: str) -> dict[str, Any]:
        return json.loads(self._request_bytes(url, accept="application/geo+json, application/json").decode("utf-8"))


def nswws_warning_modifier(warnings_geojson: dict[str, Any]) -> float:
    """Convert warning features to a simple risk modifier for prototype use."""

    severity_scores = {
        "yellow": 0.15,
        "amber": 0.35,
        "red": 0.60,
    }
    weather_type_bonus = {
        "rain": 0.10,
        "thunderstorm": 0.15,
    }
    modifier = 0.0
    for feature in warnings_geojson.get("features", []):
        props = {str(k).lower(): v for k, v in feature.get("properties", {}).items()}
        text = " ".join(str(value).lower() for value in props.values())
        for colour, score in severity_scores.items():
            if colour in text:
                modifier = max(modifier, score)
        for weather_type, bonus in weather_type_bonus.items():
            if weather_type in text:
                modifier = max(modifier, modifier + bonus)
    return min(modifier, 1.0)


@dataclass
class MetOfficeSiteSpecificClient:
    """Weather DataHub SiteSpecificForecast client.

    This API returns point forecasts. For flood analysis it should be sampled at
    one or more representative points, then converted to a rainfall grid.
    """

    api_key_env: str = "METOFFICE_SITE_API_KEY"
    base_url: str = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point"
    timeout_seconds: int = 30

    @property
    def api_key(self) -> str:
        value = os.getenv(self.api_key_env, "").strip()
        if not value:
            raise RuntimeError(
                f"Met Office SiteSpecific API key not found. Set ${self.api_key_env} in your shell, "
                "for example PowerShell: $env:METOFFICE_SITE_API_KEY='...'"
            )
        return value

    def forecast(
        self,
        latitude: float,
        longitude: float,
        timesteps: str = "hourly",
        exclude_parameter_metadata: bool = False,
        include_location_name: bool = True,
    ) -> dict[str, Any]:
        if timesteps not in {"hourly", "three-hourly", "daily"}:
            raise ValueError("timesteps must be hourly, three-hourly, or daily")
        query = urlencode(
            {
                "excludeParameterMetadata": str(exclude_parameter_metadata).lower(),
                "includeLocationName": str(include_location_name).lower(),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
        request = Request(
            f"{self.base_url}/{timesteps}?{query}",
            headers={
                "Accept": "application/json",
                "apikey": self.api_key,
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def extract_precipitation_series(site_specific_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort extraction of precipitation-like values from SiteSpecific JSON.

    Weather DataHub schemas can vary by timestep/product. This parser keeps the
    connector robust by looking for fields whose names indicate precipitation or
    rainfall and pairing them with nearby time fields where present.
    """

    rows: list[dict[str, Any]] = []

    def visit(node: Any, context_time: str | None = None) -> None:
        if isinstance(node, dict):
            time_value = context_time
            for key, value in node.items():
                if key.lower() in {"time", "timestamp", "validtime", "forecasttime", "datetime"}:
                    time_value = str(value)
            for key, value in node.items():
                lower = key.lower()
                if any(token in lower for token in ("precip", "rain", "prate")) and isinstance(value, (int, float, str)):
                    try:
                        rows.append({"time": time_value, "parameter": key, "value": float(value)})
                    except ValueError:
                        pass
                else:
                    visit(value, time_value)
        elif isinstance(node, list):
            for item in node:
                visit(item, context_time)

    visit(site_specific_payload)
    return rows
