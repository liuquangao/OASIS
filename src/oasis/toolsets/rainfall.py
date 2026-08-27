"""Provider-neutral rainfall observation tools."""

from pydantic_ai import FunctionToolset, RunContext

from oasis.deps import Deps, MapAgentDeps
from oasis.integrations.base import RainfallProvider
from oasis.models.rainfall import LatestRainfallAreaSummary, RainfallAreaSummary


rainfall_tools = FunctionToolset[Deps](
    instructions=(
        "Rain-gauge totals are local observations, not forecasts or operational "
        "flood-warning thresholds. Preserve provenance and quality warnings."
    )
)

map_rainfall_tools = FunctionToolset[MapAgentDeps](
    instructions=(
        "Use this tool for questions about recent or current rain near a map "
        "location. Report the nearest gauge's latest accumulation, accumulation "
        "period, timestamp, and distance. A gauge observation represents that "
        "gauge and interval, not the user's exact location or the present instant."
    )
)


async def _recent_rainfall_near_location(
    provider: RainfallProvider,
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    period_hours: int,
    station_limit: int,
) -> RainfallAreaSummary:
    return await provider.recent_rainfall_near_location(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        period_hours=period_hours,
        limit=station_limit,
    )


@rainfall_tools.tool
async def get_recent_rainfall_near_location(
    ctx: RunContext[Deps],
    latitude: float,
    longitude: float,
    radius_km: float = 20,
    period_hours: int = 24,
    station_limit: int = 3,
) -> RainfallAreaSummary:
    """Summarize recent 15-minute rainfall at nearby gauges.

    Args:
        ctx: Agent runtime dependencies.
        latitude: WGS84 latitude of the search centre.
        longitude: WGS84 longitude of the search centre.
        radius_km: Maximum distance to a rainfall gauge in kilometres.
        period_hours: Observation window from 1 to 168 hours.
        station_limit: Maximum number of nearby gauges, from 1 to 10.
    """

    return await _recent_rainfall_near_location(
        ctx.deps.rainfall,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        period_hours=period_hours,
        station_limit=station_limit,
    )


@map_rainfall_tools.tool
async def get_latest_rainfall_near_location(
    ctx: RunContext[MapAgentDeps],
    latitude: float,
    longitude: float,
    radius_km: float = 20,
    station_limit: int = 3,
) -> LatestRainfallAreaSummary:
    """Return the latest accumulation reported by rain gauges near a map location.

    Args:
        ctx: Map Agent runtime dependencies.
        latitude: WGS84 latitude of the search centre.
        longitude: WGS84 longitude of the search centre.
        radius_km: Maximum distance to a rainfall gauge in kilometres.
        station_limit: Maximum number of nearby gauges, from 1 to 10.
    """

    ctx.deps.tool_trace.append("get_latest_rainfall_near_location")
    return await ctx.deps.rainfall.latest_rainfall_near_location(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        limit=station_limit,
    )
