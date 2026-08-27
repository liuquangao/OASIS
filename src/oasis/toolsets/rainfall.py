"""Provider-neutral rainfall observation tools."""

from pydantic_ai import FunctionToolset, RunContext

from oasis.deps import Deps
from oasis.models.rainfall import RainfallAreaSummary


rainfall_tools = FunctionToolset[Deps](
    instructions=(
        "Rain-gauge totals are local observations, not forecasts or operational "
        "flood-warning thresholds. Preserve provenance and quality warnings."
    )
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

    return await ctx.deps.rainfall.recent_rainfall_near_location(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        period_hours=period_hours,
        limit=station_limit,
    )

