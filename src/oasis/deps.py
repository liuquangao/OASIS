"""Runtime dependencies injected into PydanticAI tool calls."""

from __future__ import annotations

from dataclasses import dataclass

from oasis.integrations.base import (
    GeocodingProvider,
    CurrentHazardProvider,
    HydrometricProvider,
    NearbyPlaceProvider,
    RainfallProvider,
    RouteHazardProvider,
    RoutingProvider,
)
from oasis.models.map_conversation import MapEvent, MapSessionState


@dataclass
class Deps:
    """Swappable runtime resources used by the Agent's toolsets."""

    hydrometry: HydrometricProvider
    rainfall: RainfallProvider


@dataclass
class MapAgentDeps:
    """Runtime resources and mutable per-turn state for the map Agent."""

    geocoder: GeocodingProvider
    nearby_places: NearbyPlaceProvider
    current_hazard: CurrentHazardProvider
    routing: RoutingProvider
    route_hazard: RouteHazardProvider
    state: MapSessionState
    events: list[MapEvent]
    tool_trace: list[str]
