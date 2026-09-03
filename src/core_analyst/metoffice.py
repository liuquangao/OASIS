from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
