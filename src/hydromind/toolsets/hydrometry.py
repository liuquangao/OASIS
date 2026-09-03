"""Provider-neutral hydrometric observation tools."""

from pydantic_ai import FunctionToolset, RunContext

from hydromind.deps import Deps
from hydromind.models.hydrometry import WaterLevelAreaSummary


hydrometry_tools = FunctionToolset[Deps](
    instructions=(
        "Water levels are observations, not station-specific warning thresholds. "
        "Preserve provenance and quality warnings."
    )
)


@hydrometry_tools.tool
async def get_recent_water_levels_near_location(
    ctx: RunContext[Deps],
    latitude: float,
    longitude: float,
    radius_km: float = 30,
    period_days: int = 1,
    station_limit: int = 3,
) -> WaterLevelAreaSummary:
    """Find nearby river-level stations and summarize their recent observations.

    Args:
        ctx: Agent runtime dependencies.
        latitude: WGS84 latitude of the search centre.
        longitude: WGS84 longitude of the search centre.
        radius_km: Maximum geodesic search radius in kilometres.
        period_days: Number of recent days to retrieve, from 1 to 31.
        station_limit: Maximum number of nearby stations, from 1 to 10.
    """

    return await ctx.deps.hydrometry.recent_water_levels_near_location(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        period_days=period_days,
        limit=station_limit,
    )
