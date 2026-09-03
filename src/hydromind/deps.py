"""Runtime dependencies injected into PydanticAI tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hydromind.integrations.base import (
    GeocodingProvider,
    CurrentHazardProvider,
    HydrometricProvider,
    LatestRainfallProvider,
    NearbyPlaceProvider,
    RainfallProvider,
    RouteHazardProvider,
    RoutingProvider,
)
from hydromind.models.map_conversation import MapEvent, MapSessionState

if TYPE_CHECKING:
    from hydromind.integrations.core_analysis import CoreAnalystAnalysisService


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
    rainfall: LatestRainfallProvider
    analysis: CoreAnalystAnalysisService
    current_hazard: CurrentHazardProvider
    routing: RoutingProvider
    route_hazard: RouteHazardProvider
    state: MapSessionState
    events: list[MapEvent]
    tool_trace: list[str]
