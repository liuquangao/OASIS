"""Composable geospatial and visualization tools for the map Agent."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from pydantic_ai import FunctionToolset, RunContext

from hydromind.deps import MapAgentDeps
from hydromind.models.map_conversation import MapEvent, RememberedLocation
from hydromind.models.routes import RememberedRoute, RouteSummary


map_tools = FunctionToolset[MapAgentDeps](
    instructions=(
        "Use these generic tools to discover places, request and analyse candidate "
        "driving routes against the latest calculated raster, and change the browser map. "
        "Nearby OpenStreetMap filters use tag_key and "
        "tag_value, for example amenity=hospital, amenity=school, railway=station, "
        "or public_transport=station. Compose tools instead of assuming a fixed task."
    )
)


def _location(state_locations: list[RememberedLocation], location_id: str) -> RememberedLocation:
    return next(location for location in state_locations if location.id == location_id)


def _route(state_routes: list[RememberedRoute], route_id: str) -> RememberedRoute:
    return next(route for route in state_routes if route.id == route_id)


def _route_summary(route: RememberedRoute) -> RouteSummary:
    return RouteSummary(
        id=route.id,
        label=route.label,
        mode=route.mode,
        distance_m=route.distance_m,
        duration_seconds=route.duration_seconds,
        hazard=route.hazard,
        rank=route.rank,
    )


@map_tools.tool
async def geocode_location(
    ctx: RunContext[MapAgentDeps],
    query: str,
    display_label: str | None = None,
) -> RememberedLocation:
    """Resolve a UK postcode, address, landmark, or facility and remember it.

    Args:
        ctx: Agent runtime dependencies.
        query: Canonical place query suitable for a UK geocoder.
        display_label: Short label to show to the user, if different from query.
    """

    ctx.deps.tool_trace.append("geocode_location")
    place = await ctx.deps.geocoder.geocode(query)
    if place is None:
        raise ValueError(f"No UK location was found for {query!r}.")
    location = RememberedLocation(
        id=f"loc-{uuid4().hex[:10]}",
        label=display_label or place.label,
        search_query=query,
        latitude=place.latitude,
        longitude=place.longitude,
        place_type=place.place_type,
        provider=place.provider,
        source_url=place.source_url,
        retrieved_at=place.retrieved_at,
    )
    ctx.deps.state.locations.append(location)
    ctx.deps.state.active_location_id = location.id
    ctx.deps.state.last_task = "locate"
    return location


@map_tools.tool
async def search_nearby_places(
    ctx: RunContext[MapAgentDeps],
    center_location_id: str,
    radius_km: float = 2,
    tag_key: str | None = None,
    tag_value: str | None = None,
    limit: int = 10,
) -> list[RememberedLocation]:
    """Discover named OpenStreetMap places around a remembered location.

    Args:
        ctx: Agent runtime dependencies.
        center_location_id: Exact remembered location ID used as the search centre.
        radius_km: Search radius in kilometres.
        tag_key: Optional OpenStreetMap tag key, such as amenity or railway.
        tag_value: Optional OpenStreetMap tag value, such as hospital or station.
        limit: Maximum number of nearby places to return.
    """

    ctx.deps.tool_trace.append("search_nearby_places")
    center = _location(ctx.deps.state.locations, center_location_id)
    places = await ctx.deps.nearby_places.search_nearby(
        latitude=center.latitude,
        longitude=center.longitude,
        radius_km=radius_km,
        tag_key=tag_key,
        tag_value=tag_value,
        limit=limit,
    )
    remembered = [
        RememberedLocation(
            id=f"loc-{uuid4().hex[:10]}",
            label=place.label,
            search_query=place.label,
            latitude=place.latitude,
            longitude=place.longitude,
            place_type=place.place_type,
            distance_km=place.distance_km,
            provider=place.provider,
            source_url=place.source_url,
            retrieved_at=place.retrieved_at,
        )
        for place in places
    ]
    ctx.deps.state.locations.extend(remembered)
    ctx.deps.state.last_task = "nearby"
    return remembered


@map_tools.tool
async def get_candidate_routes(
    ctx: RunContext[MapAgentDeps],
    origin_location_id: str,
    destination_location_id: str,
    alternatives: int = 3,
) -> list[RouteSummary]:
    """Get up to three candidate driving routes between two remembered places.

    Args:
        ctx: Agent runtime dependencies.
        origin_location_id: Exact remembered ID for the route origin.
        destination_location_id: Exact remembered ID for the destination.
        alternatives: Requested candidate count from one to three.
    """

    ctx.deps.tool_trace.append("get_candidate_routes")
    origin = _location(ctx.deps.state.locations, origin_location_id)
    destination = _location(ctx.deps.state.locations, destination_location_id)
    candidates = await ctx.deps.routing.candidate_routes(
        origin_latitude=origin.latitude,
        origin_longitude=origin.longitude,
        destination_latitude=destination.latitude,
        destination_longitude=destination.longitude,
        alternatives=alternatives,
    )
    ctx.deps.state.routes = [
        RememberedRoute(
            id=f"route-{uuid4().hex[:10]}",
            label=f"Route {index}",
            origin_location_id=origin.id,
            destination_location_id=destination.id,
            coordinates=candidate.coordinates,
            distance_m=candidate.distance_m,
            duration_seconds=candidate.duration_seconds,
            provider=candidate.provider,
            source_url=candidate.source_url,
        )
        for index, candidate in enumerate(candidates, start=1)
    ]
    ctx.deps.state.visible_route_ids = []
    ctx.deps.state.last_task = "route"
    ctx.deps.events.append(MapEvent(type="clear_routes"))
    return [_route_summary(route) for route in ctx.deps.state.routes]


@map_tools.tool
async def analyse_route_hazard(
    ctx: RunContext[MapAgentDeps],
    route_ids: list[str],
    sample_spacing_m: float = 20,
) -> list[RouteSummary]:
    """Sample the latest calculated hazard raster along candidate route centrelines.

    Args:
        ctx: Agent runtime dependencies.
        route_ids: Exact remembered route IDs to analyse.
        sample_spacing_m: Distance between centreline samples in metres.
    """

    ctx.deps.tool_trace.append("analyse_route_hazard")
    routes = [_route(ctx.deps.state.routes, route_id) for route_id in route_ids]
    results = await asyncio.gather(
        *(
            ctx.deps.route_hazard.analyse(
                route.coordinates,
                sample_spacing_m=sample_spacing_m,
            )
            for route in routes
        )
    )
    for route, result in zip(routes, results):
        route.hazard = result
    ctx.deps.events.append(MapEvent(type="refresh_routes", route_ids=route_ids))
    return [_route_summary(route) for route in routes]


@map_tools.tool
def rank_routes(
    ctx: RunContext[MapAgentDeps],
    route_ids: list[str],
) -> list[RouteSummary]:
    """Rank analysed candidates by raster coverage, hazard index, then duration.

    Args:
        ctx: Agent runtime dependencies.
        route_ids: Exact remembered route IDs whose hazard has been analysed.
    """

    ctx.deps.tool_trace.append("rank_routes")
    routes = [_route(ctx.deps.state.routes, route_id) for route_id in route_ids]
    if any(route.hazard is None for route in routes):
        raise ValueError("Every route must be analysed before ranking.")
    ranked = sorted(
        routes,
        key=lambda route: (
            100 - route.hazard.coverage_percent,
            route.hazard.hazard_index
            if route.hazard.hazard_index is not None
            else float("inf"),
            route.duration_seconds,
        ),
    )
    for rank, route in enumerate(ranked, start=1):
        route.rank = rank
    ctx.deps.events.append(
        MapEvent(type="refresh_routes", route_ids=[route.id for route in ranked])
    )
    return [_route_summary(route) for route in ranked]


@map_tools.tool
def display_routes(
    ctx: RunContext[MapAgentDeps],
    route_ids: list[str],
) -> list[str]:
    """Draw candidate routes on the map and fit the map to their combined extent.

    Args:
        ctx: Agent runtime dependencies.
        route_ids: Exact remembered route IDs to display.
    """

    ctx.deps.tool_trace.append("display_routes")
    ctx.deps.state.visible_route_ids = list(route_ids)
    ctx.deps.events.extend(
        [
            MapEvent(type="display_routes", route_ids=route_ids),
            MapEvent(type="fit_routes", route_ids=route_ids),
        ]
    )
    return route_ids


@map_tools.tool
def display_locations(
    ctx: RunContext[MapAgentDeps],
    location_ids: list[str],
    replace_visible: bool = False,
) -> list[str]:
    """Display remembered locations as markers and fit the map to them.

    Args:
        ctx: Agent runtime dependencies.
        location_ids: Exact remembered location IDs to display.
        replace_visible: Replace existing markers instead of adding to them.
    """

    ctx.deps.tool_trace.append("display_locations")
    if replace_visible:
        removed = [
            item for item in ctx.deps.state.visible_location_ids if item not in location_ids
        ]
        if removed:
            ctx.deps.events.append(
                MapEvent(type="remove_locations", location_ids=removed)
            )
        ctx.deps.state.visible_location_ids = list(location_ids)
    else:
        ctx.deps.state.visible_location_ids = list(
            dict.fromkeys([*ctx.deps.state.visible_location_ids, *location_ids])
        )
    ctx.deps.events.extend(
        [
            MapEvent(type="display_locations", location_ids=location_ids),
            MapEvent(type="fit_locations", location_ids=location_ids),
        ]
    )
    if location_ids:
        ctx.deps.state.active_location_id = location_ids[-1]
    return ctx.deps.state.visible_location_ids


@map_tools.tool
def remove_locations(
    ctx: RunContext[MapAgentDeps],
    location_ids: list[str],
) -> list[str]:
    """Remove remembered locations from the session and map.

    Args:
        ctx: Agent runtime dependencies.
        location_ids: Exact remembered location IDs to remove.
    """

    ctx.deps.tool_trace.append("remove_locations")
    ctx.deps.state.locations = [
        item for item in ctx.deps.state.locations if item.id not in location_ids
    ]
    ctx.deps.state.visible_location_ids = [
        item for item in ctx.deps.state.visible_location_ids if item not in location_ids
    ]
    ctx.deps.events.append(MapEvent(type="remove_locations", location_ids=location_ids))
    ctx.deps.state.active_location_id = (
        ctx.deps.state.locations[-1].id if ctx.deps.state.locations else None
    )
    return location_ids


@map_tools.tool
def clear_map_session(ctx: RunContext[MapAgentDeps]) -> bool:
    """Clear all remembered places, markers, and hazard-layer state."""

    ctx.deps.tool_trace.append("clear_map_session")
    ctx.deps.state.locations = []
    ctx.deps.state.visible_location_ids = []
    ctx.deps.state.routes = []
    ctx.deps.state.visible_route_ids = []
    ctx.deps.state.active_location_id = None
    ctx.deps.state.hazard_layer_visible = False
    ctx.deps.state.last_task = None
    ctx.deps.events.extend(
        [
            MapEvent(type="clear_locations"),
            MapEvent(type="clear_routes"),
            MapEvent(type="set_hazard_layer", visible=False),
        ]
    )
    return True
