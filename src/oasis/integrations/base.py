"""Protocols that keep Toolsets independent from Scotland-specific APIs."""

from __future__ import annotations

from typing import Protocol

from oasis.models.hydrometry import WaterLevelAreaSummary
from oasis.models.rainfall import LatestRainfallAreaSummary, RainfallAreaSummary
from oasis.models.hazard import HazardLookupResult
from oasis.models.current_hazard import CurrentHazardSnapshot
from oasis.models.map_conversation import GeoPlace
from oasis.models.routes import RouteCandidate, RouteHazardSummary


class HydrometricProvider(Protocol):
    async def recent_water_levels_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 30,
        period_days: int = 1,
        limit: int = 3,
    ) -> WaterLevelAreaSummary: ...

class RainfallProvider(Protocol):
    async def recent_rainfall_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 20,
        period_hours: int = 24,
        limit: int = 3,
    ) -> RainfallAreaSummary: ...


class LatestRainfallProvider(Protocol):
    async def latest_rainfall_near_location(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 20,
        limit: int = 3,
    ) -> LatestRainfallAreaSummary: ...


class GeocodingProvider(Protocol):
    async def geocode(self, query: str) -> GeoPlace | None: ...


class NearbyPlaceProvider(Protocol):
    async def search_nearby(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        tag_key: str | None,
        tag_value: str | None,
        limit: int,
    ) -> list[GeoPlace]: ...


class CurrentHazardProvider(Protocol):
    async def lookup(
        self,
        latitude: float,
        longitude: float,
    ) -> HazardLookupResult: ...

    async def refresh(self) -> CurrentHazardSnapshot: ...

    async def status(self) -> CurrentHazardSnapshot: ...


class RoutingProvider(Protocol):
    async def candidate_routes(
        self,
        *,
        origin_latitude: float,
        origin_longitude: float,
        destination_latitude: float,
        destination_longitude: float,
        alternatives: int,
    ) -> list[RouteCandidate]: ...


class RouteHazardProvider(Protocol):
    async def analyse(
        self,
        coordinates: list[tuple[float, float]],
        *,
        sample_spacing_m: float,
    ) -> RouteHazardSummary: ...
