"""Generic UK geocoding and nearby-place discovery through OpenStreetMap."""

from __future__ import annotations

from datetime import datetime, timezone
from math import cos, radians

import httpx

from oasis.domain.geo import haversine_km
from oasis.models.map_conversation import GeoPlace


class OpenStreetMapClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        nominatim_url: str,
    ) -> None:
        self._client = client
        self._nominatim_url = nominatim_url

    async def geocode(self, query: str) -> GeoPlace | None:
        response = await self._client.get(
            self._nominatim_url,
            params={
                "format": "jsonv2",
                "q": query,
                "countrycodes": "gb",
                "limit": 1,
                "addressdetails": 1,
            },
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        item = results[0]
        return GeoPlace(
            label=item["display_name"],
            latitude=float(item["lat"]),
            longitude=float(item["lon"]),
            place_type=item.get("type", "place"),
            provider="OpenStreetMap Nominatim",
            source_url=str(response.request.url),
            retrieved_at=datetime.now(timezone.utc),
        )

    async def search_nearby(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        tag_key: str | None,
        tag_value: str | None,
        limit: int,
    ) -> list[GeoPlace]:
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * cos(radians(latitude)))
        query = tag_value or tag_key or "public place"
        response = await self._client.get(
            self._nominatim_url,
            params={
                "format": "jsonv2",
                "q": query,
                "countrycodes": "gb",
                "limit": limit * 3,
                "viewbox": (
                    f"{longitude - lon_delta},{latitude + lat_delta},"
                    f"{longitude + lon_delta},{latitude - lat_delta}"
                ),
                "bounded": 1,
                "addressdetails": 1,
            },
        )
        response.raise_for_status()
        places: list[GeoPlace] = []
        for item in response.json():
            if tag_key and item.get("category") != tag_key:
                continue
            if tag_value and item.get("type") != tag_value:
                continue
            item_latitude = float(item["lat"])
            item_longitude = float(item["lon"])
            places.append(
                GeoPlace(
                    label=item.get("name") or item["display_name"].split(",")[0],
                    latitude=item_latitude,
                    longitude=item_longitude,
                    place_type=f"{item.get('category', 'place')}:{item.get('type', 'place')}",
                    distance_km=round(
                        haversine_km(
                            latitude,
                            longitude,
                            item_latitude,
                            item_longitude,
                        ),
                        3,
                    ),
                    provider="OpenStreetMap Nominatim nearby search",
                    source_url=str(response.request.url),
                    retrieved_at=datetime.now(timezone.utc),
                )
            )
        return sorted(places, key=lambda place: place.distance_km or 0)[:limit]
