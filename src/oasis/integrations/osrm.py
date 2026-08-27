"""Driving-route candidates from the OpenStreetMap-based OSRM service."""

from __future__ import annotations

import httpx

from oasis.models.routes import RouteCandidate


class OsrmRoutingClient:
    def __init__(self, client: httpx.AsyncClient, *, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def candidate_routes(
        self,
        *,
        origin_latitude: float,
        origin_longitude: float,
        destination_latitude: float,
        destination_longitude: float,
        alternatives: int,
    ) -> list[RouteCandidate]:
        coordinates = (
            f"{origin_longitude},{origin_latitude};"
            f"{destination_longitude},{destination_latitude}"
        )
        response = await self._client.get(
            f"{self._base_url}/route/v1/driving/{coordinates}",
            params={
                "alternatives": alternatives,
                "steps": "false",
                "overview": "full",
                "geometries": "geojson",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok":
            raise ValueError(payload.get("message") or "OSRM could not find a route.")
        return [
            RouteCandidate(
                coordinates=[tuple(point) for point in route["geometry"]["coordinates"]],
                distance_m=route["distance"],
                duration_seconds=route["duration"],
                provider="OSRM using OpenStreetMap road data",
                source_url=str(response.request.url),
            )
            for route in payload["routes"]
        ]
