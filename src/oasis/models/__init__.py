"""Pydantic models used as tool inputs, tool results, and agent output."""

from .agent_output import AgentOutput, EvidenceReference
from .hazard import HazardLookupResult
from .current_hazard import CurrentHazardSnapshot
from .map_conversation import (
    GeoPlace,
    MapAgentAnswer,
    MapAgentResponse,
    MapEvent,
    MapSessionState,
    RememberedLocation,
)
from .routes import RememberedRoute, RouteCandidate, RouteHazardSummary, RouteSummary
from .hydrometry import (
    MonitoringStation,
    WaterLevelAreaSummary,
    WaterLevelReading,
    WaterLevelSummary,
)
from .provenance import DataProvenance
from .rainfall import (
    LatestRainfallAreaSummary,
    LatestRainfallObservation,
    RainfallAreaSummary,
    RainfallReading,
    RainfallStation,
    RainfallStationSummary,
)
from .spatial import AreaOfInterest, BoundingBox

__all__ = [
    "AgentOutput",
    "HazardLookupResult",
    "CurrentHazardSnapshot",
    "GeoPlace",
    "MapAgentAnswer",
    "MapAgentResponse",
    "MapEvent",
    "MapSessionState",
    "RememberedLocation",
    "RememberedRoute",
    "RouteCandidate",
    "RouteHazardSummary",
    "RouteSummary",
    "AreaOfInterest",
    "BoundingBox",
    "DataProvenance",
    "EvidenceReference",
    "MonitoringStation",
    "LatestRainfallAreaSummary",
    "LatestRainfallObservation",
    "RainfallAreaSummary",
    "RainfallReading",
    "RainfallStation",
    "RainfallStationSummary",
    "WaterLevelAreaSummary",
    "WaterLevelReading",
    "WaterLevelSummary",
]
