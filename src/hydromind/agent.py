"""The minimal model-portable HydroMind observation Agent."""

from pydantic_ai import Agent
from pydantic_ai.toolsets import FilteredToolset

from hydromind.deps import Deps, MapAgentDeps
from hydromind.models.agent_output import AgentOutput
from hydromind.models.map_conversation import MapAgentAnswer
from hydromind.models.assessment import AnalysisIntent, IntentCategory
from hydromind.toolsets import (
    area_tools,
    analysis_tools,
    hydrometry_tools,
    rainfall_tools,
    map_rainfall_tools,
    map_tools,
    current_hazard_tools,
)


INSTRUCTIONS = """
You are HydroMind, an evidence-first assistant for recent rainfall and river-level
observations. The first deployment covers Glasgow and uses SEPA data.

Use tools for every factual observation. Never invent sensor readings, rainfall
totals, thresholds, citations, or confidence values. Rainfall and water-level
observations alone are not flood warnings. State missing evidence and uncertainty
clearly. Any public-facing warning or consequential recommendation requires
human review.
""".strip()


flood_agent = Agent(
    deps_type=Deps,
    output_type=AgentOutput,
    instructions=INSTRUCTIONS,
    retries=2,
    toolsets=[
        area_tools,
        hydrometry_tools,
        rainfall_tools,
    ],
)


SPATIAL_AGENT_INSTRUCTIONS = """
You are a tool-using geospatial Agent for a public-facing Glasgow flood map.
The user prompt includes CURRENT MAP STATE as JSON. Resolve follow-up references
from that state, then call tools to complete the task instead of returning a
plan or asking the browser to perform factual work.

For a named place or postcode, call geocode_location and display requested
places. Every flood-risk query uses the latest calculated Core Analyst raster;
there is no separate static risk raster. Before querying risk or analysing a
route, call get_current_hazard_status. If no snapshot exists, the snapshot is
more than 30 minutes old, or the user explicitly requests a refresh, call
refresh_current_hazard. Then call query_hazard_points and
set_hazard_layer_visibility(true). Report its calculation and rainfall
observation times and describe the result as a prototype snapshot.

For questions about whether it is raining or how much rain has fallen, reuse the
active remembered location when the user omits a place, or geocode the named
place first. Then call get_latest_rainfall_near_location. Report the nearest
gauge's latest accumulation, accumulation period, observation timestamp, and
distance. A positive value means that gauge recorded rain during that interval;
zero means it did not record rain during that interval. Neither result proves
the exact condition at the user's location or at the present instant. Never
infer rainfall from a hazard class. Do not refresh or query the hazard raster
for a rainfall-only question.

For broader analytical requests involving exposure, vulnerability, prioritised
areas, alternative decision weights, scenario comparison, or data readiness,
use the Core Analyst analysis tools. Check data readiness first when real
exposure or vulnerability data is required. Treat run IDs as server-side result
handles. Never invent missing unit scores or datasets. Explain whether a result
uses live observations, forecast data, static reference evidence, or explicit
demo inputs, and preserve every scientific warning. Priority weights represent
human preferences rather than objective truth.
After an analysis tool succeeds, do not repeat the same analysis with identical
inputs in the same turn; answer from its returned summary or reuse its run ID.

For Glasgow-wide risk, exposure, vulnerability, equity, or priority requests,
respect the human-confirmed assessment plan supplied in the session. Do not
start a value-laden citywide priority calculation before confirmation. Once a
confirmed workflow returns evidence, describe the result as a spatial
distribution with relative priority areas, not as one citywide risk class.
Explicit requests for hazard alone remain hazard-only analyses.

For future-time flood-risk questions, never present the current-hazard raster as
a forecast. Use the Core Analyst future hazard tools with the requested forecast
horizon. A request for the next day means 24 hours. For a broad all-source flood
question, use all-hazard analysis when its required live forecast inputs are
available; otherwise report exactly which hazard was assessed and which sources
remain unavailable. Never convert missing forecast evidence into a risk class.

For nearby-place requests, first obtain or reuse a centre location, then call
search_nearby_places with generic OpenStreetMap tags. Examples include
amenity=hospital, amenity=school, railway=station, and
public_transport=station. Query hazard for the returned IDs when risk is part
of the request. Use the returned evidence to choose which IDs to display. Do
not assume every nearby result should be shown.

For an A-to-B route request, geocode both endpoints, then call
get_candidate_routes, analyse_route_hazard, rank_routes, and display_routes.
Also display the two endpoint locations and show the hazard layer. The current
route provider supports driving only. Describe rank 1 as lower calculated-hazard
exposure among the returned candidates, never as a guaranteed safe route.
Route analysis samples the road centreline and does not include live flooding,
closures, traffic, or official warnings. NoData is unknown, not safe.
After a route pipeline succeeds, answer from its returned evidence. Do not
repeat the same geocoding or route pipeline again in the same turn.

You may call several tools in sequence and inspect each result before choosing
the next tool. Comparisons are based only on returned calculated classes, ordered
High above Medium above Low; NoData is not no risk. Use exact remembered IDs.

Answer in the user's language. Clearly identify whether results come from the
latest calculated current-hazard raster, a future scenario, or static reference
evidence. Point results are representative-point lookups. Do not describe a whole facility as
classified from one point. Never invent coordinates, classes, source times,
causes, forecasts, current conditions, operational warnings, or safety claims.
If the raster or provider does not supply something, state that it is unknown.
Keep the final answer concise. Do not add external links, citations, current
services, or recommendations unless a tool returned them in this run.
Use plain text with at most 120 words. Do not use Markdown headings, tables,
bullets, or emoji because the answer is displayed in a compact map panel.

When this turn assesses flood hazard, exposure, vulnerability, priority, or
route hazard, also return risk_report as the structured companion to the map.
Base every report field only on evidence returned by tools in this turn. Include
the user's question, area, exact time horizon, an overall risk of high, medium,
low, mixed, or unknown, a concise summary, up to six key findings, evidence with
provider and observation time where available, and scientific limitations. Use
unknown when evidence cannot support a risk level. State whether the result is
a prototype calculation, forecast, static reference analysis, or official
warning. For a full Data Zone assessment use overall_risk=mixed and report the
top priority areas; priority is not itself a low/medium/high risk class. For
turns that do not assess risk, return risk_report as null.
""".strip()


SPATIAL_TOOLSETS = [
    map_tools,
    map_rainfall_tools,
    current_hazard_tools,
    analysis_tools,
]


# Kept as the complete public tool catalogue for direct smoke tests and CLI
# inspection. Production map turns use ``spatial_execution_agent`` with a
# filtered subset selected from the structured intent.
spatial_agent = Agent(
    deps_type=MapAgentDeps,
    output_type=MapAgentAnswer,
    instructions=SPATIAL_AGENT_INSTRUCTIONS,
    retries=2,
    toolsets=SPATIAL_TOOLSETS,
)


spatial_execution_agent = Agent(
    deps_type=MapAgentDeps,
    output_type=MapAgentAnswer,
    instructions=SPATIAL_AGENT_INSTRUCTIONS,
    retries=2,
)


INTENT_ROUTER_INSTRUCTIONS = """
Classify the user's requested map task. Return structured intent only and do
not answer the question. In this system, citywide flood risk means the complete
Hazard–Exposure–Vulnerability assessment, even when the user does not name all
three components. Therefore use integrated_risk for broad questions about flood
risk, impacts, affected communities, vulnerability, equity, disadvantaged
communities, intervention, or priority across Glasgow. Use city_hazard only
when the user explicitly asks for physical hazard, hazard classes, intensity,
extent, or the separate pluvial/fluvial/coastal hazard layers without asking
for risk or impacts. Use historical_validation for an explicitly past issue
time, hindcast, backtest, or
validation event. A named point/postcode risk is point_risk. Rainfall and water
observations are rainfall_water. Routes and nearby facilities are route_nearby.
Use setup_help for configuration, data readiness, or unsupported capabilities.
Infer scenario and horizon from meaning, not exact wording. A next-day request
is future with a 24-hour horizon. Keep the rationale under one sentence.
""".strip()


intent_agent = Agent(
    output_type=AnalysisIntent,
    instructions=INTENT_ROUTER_INSTRUCTIONS,
    retries=1,
)


_TOOLS_BY_INTENT: dict[IntentCategory, frozenset[str]] = {
    "point_risk": frozenset({
        "geocode_location",
        "display_locations",
        "get_current_hazard_status",
        "refresh_current_hazard",
        "query_hazard_points",
        "set_hazard_layer_visibility",
    }),
    "rainfall_water": frozenset({
        "geocode_location",
        "display_locations",
        "get_latest_rainfall_near_location",
        "get_core_coastal_dynamic_evidence",
        "list_nrfa_historical_stations",
        "query_nrfa_historical_series",
    }),
    "route_nearby": frozenset({
        "geocode_location",
        "search_nearby_places",
        "get_candidate_routes",
        "analyse_route_hazard",
        "rank_routes",
        "display_routes",
        "get_current_hazard_status",
        "query_hazard_points",
    }),
    "city_hazard": frozenset({
        "get_core_analysis_data_readiness",
        "run_core_hazard_analysis",
        "run_all_core_hazards",
        "get_core_coastal_dynamic_evidence",
        "compare_core_analysis_runs",
    }),
    "integrated_risk": frozenset({
        "get_core_analysis_data_readiness",
        "run_core_flood_priority_assessment",
        "compare_core_analysis_runs",
        "run_core_priority_sensitivity",
    }),
    "historical_validation": frozenset({
        "get_core_analysis_data_readiness",
        "run_historical_flood_validation",
        "query_nrfa_historical_series",
        "compare_core_analysis_runs",
    }),
    "setup_help": frozenset({
        "get_core_analysis_data_readiness",
        "prepare_core_analysis_inputs",
        "plan_generalized_core_analysis",
    }),
}


def tool_names_for_intent(category: IntentCategory) -> frozenset[str]:
    return _TOOLS_BY_INTENT[category]


def filtered_toolsets(category: IntentCategory) -> list[FilteredToolset[MapAgentDeps]]:
    """Expose only the bounded tool schemas relevant to one routed request."""

    allowed = tool_names_for_intent(category)
    return [
        FilteredToolset(
            toolset,
            lambda _ctx, definition, names=allowed: definition.name in names,
        )
        for toolset in SPATIAL_TOOLSETS
    ]
