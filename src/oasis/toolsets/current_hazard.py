"""Agent tools for the latest calculated whole-Glasgow hazard raster."""

from __future__ import annotations

from pydantic_ai import FunctionToolset, RunContext

from oasis.deps import MapAgentDeps
from oasis.models.current_hazard import CurrentHazardSnapshot
from oasis.models.map_conversation import MapEvent, RememberedLocation


current_hazard_tools = FunctionToolset[MapAgentDeps](
    instructions=(
        "Use these tools for every flood-hazard request. The raster covers Glasgow and is calculated from "
        "latest available SEPA rainfall plus static terrain/runoff factors."
    )
)


@current_hazard_tools.tool
async def get_current_hazard_status(
    ctx: RunContext[MapAgentDeps],
) -> CurrentHazardSnapshot:
    """Return availability, calculation time, observation time, and limitations."""

    ctx.deps.tool_trace.append("get_current_hazard_status")
    return await ctx.deps.current_hazard.status()


@current_hazard_tools.tool
async def refresh_current_hazard(
    ctx: RunContext[MapAgentDeps],
) -> CurrentHazardSnapshot:
    """Fetch latest SEPA rainfall and recalculate the whole Glasgow 5 m raster."""

    ctx.deps.tool_trace.append("refresh_current_hazard")
    return await ctx.deps.current_hazard.refresh()


@current_hazard_tools.tool
async def query_hazard_points(
    ctx: RunContext[MapAgentDeps],
    location_ids: list[str],
) -> list[RememberedLocation]:
    """Read current calculated classes at remembered representative coordinates."""

    ctx.deps.tool_trace.append("query_hazard_points")
    locations = [
        next(item for item in ctx.deps.state.locations if item.id == location_id)
        for location_id in location_ids
    ]
    for location in locations:
        result = await ctx.deps.current_hazard.lookup(
            location.latitude,
            location.longitude,
        )
        location.class_value = result.class_value
        location.risk_level = result.risk_level
        location.risk_label = result.risk_label
        location.hazard_provider = result.provider
        location.hazard_dataset = result.dataset
        location.hazard_source_url = result.source_url
        location.hazard_retrieved_at = result.retrieved_at
        location.hazard_snapshot_time = result.snapshot_time
        location.hazard_warnings = result.warnings
    ctx.deps.state.last_task = "risk"
    ctx.deps.events.append(
        MapEvent(type="refresh_locations", location_ids=location_ids)
    )
    return locations


@current_hazard_tools.tool
def set_hazard_layer_visibility(
    ctx: RunContext[MapAgentDeps],
    visible: bool,
) -> bool:
    """Show or hide the latest calculated Glasgow hazard raster."""

    ctx.deps.tool_trace.append("set_hazard_layer_visibility")
    ctx.deps.state.hazard_layer_visible = visible
    ctx.deps.events.append(MapEvent(type="set_hazard_layer", visible=visible))
    return visible
