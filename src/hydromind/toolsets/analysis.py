"""PydanticAI tools for the extended deterministic Core Analyst pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter
from pydantic_ai import FunctionToolset, RunContext

from hydromind.deps import MapAgentDeps
from hydromind.models.analysis import (
    AnalysisRunSummary,
    DataReadinessSummary,
    GeneralizedAnalysisPlan,
    ExtensionFactor,
    HazardExtensionSpec,
    PriorityScenarioInput,
    PriorityUnitInput,
    PriorityWeights,
)
from hydromind.models.map_conversation import MapEvent


analysis_tools = FunctionToolset[MapAgentDeps](
    instructions=(
        "Use these deterministic Core Analyst tools for full hazard, exposure, "
        "vulnerability, priority, scenario, and sensitivity analysis. Check data "
        "readiness before exposure or vulnerability analysis. Full results are "
        "stored server-side and referenced by run_id. Never describe partial, "
        "proxy, static-reference, or demo results as operational forecasts. "
        "Priority weights are human value judgements and must be explicit. After "
        "a tool succeeds, reuse its returned result or run_id instead of repeating it."
    )
)


def _trace(ctx: RunContext[MapAgentDeps], name: str) -> None:
    if not ctx.deps.tool_trace or ctx.deps.tool_trace[-1] != name:
        ctx.deps.tool_trace.append(name)


def _show_layers(ctx: RunContext[MapAgentDeps], result: AnalysisRunSummary) -> AnalysisRunSummary:
    known = {layer.id for layer in ctx.deps.state.analysis_layers}
    ctx.deps.state.analysis_layers.extend(layer for layer in result.map_layers if layer.id not in known)
    ids = [layer.id for layer in result.map_layers]
    visible_ids = [layer.id for layer in result.map_layers if layer.visible]
    ctx.deps.state.visible_analysis_layer_ids = list(dict.fromkeys(
        [*ctx.deps.state.visible_analysis_layer_ids, *visible_ids]
    ))
    if ids:
        ctx.deps.events.append(MapEvent(type="sync_analysis_layers", layer_ids=ids))
    return result


@analysis_tools.tool
async def get_core_analysis_data_readiness(
    ctx: RunContext[MapAgentDeps],
    category: Literal["hazard", "exposure", "vulnerability", "study_area"] | None = None,
) -> DataReadinessSummary:
    """Report which verified datasets are available without running an analysis."""

    _trace(ctx, "get_core_analysis_data_readiness")
    return await ctx.deps.analysis.data_readiness(category)


@analysis_tools.tool
async def prepare_core_analysis_inputs(
    ctx: RunContext[MapAgentDeps],
) -> AnalysisRunSummary:
    """Prepare local exposure/vulnerability adapters; call only on an explicit user request."""

    _trace(ctx, "prepare_core_analysis_inputs")
    return await ctx.deps.analysis.prepare_inputs()


@analysis_tools.tool
async def run_core_hazard_analysis(
    ctx: RunContext[MapAgentDeps],
    hazard_type: Literal["pluvial", "fluvial", "coastal"],
    scenario: Literal["current", "future"],
    use_live_data: bool = True,
    forecast_horizon_hours: int = 6,
) -> AnalysisRunSummary:
    """Run a Glasgow hazard analysis with controlled local inputs and output paths.

    Live current pluvial analysis uses SEPA rainfall. Other live modes may require
    configured provider credentials. With live data disabled, fluvial/coastal use
    static reference evidence; pluvial uses clearly labelled demo rainfall.
    """

    if not 1 <= forecast_horizon_hours <= 48:
        raise ValueError("forecast_horizon_hours must be between 1 and 48")
    _trace(ctx, "run_core_hazard_analysis")
    return _show_layers(ctx, await ctx.deps.analysis.run_hazard(
        hazard_type=hazard_type,
        scenario=scenario,
        use_live_data=use_live_data,
        forecast_horizon=forecast_horizon_hours,
    ))


@analysis_tools.tool
async def run_core_exposure_analysis(
    ctx: RunContext[MapAgentDeps],
    hazard_run_id: str,
    exposure_types: list[
        Literal["population", "buildings", "critical_infrastructure"]
    ] | None = None,
    hazard_threshold: int = 2,
) -> AnalysisRunSummary:
    """Analyse verified population/building/facility exposure for a hazard run."""

    if hazard_threshold not in {1, 2, 3}:
        raise ValueError("hazard_threshold must be 1, 2, or 3")
    _trace(ctx, "run_core_exposure_analysis")
    return _show_layers(ctx, await ctx.deps.analysis.run_exposure(
        hazard_run_id=hazard_run_id,
        exposure_types=exposure_types,
        hazard_threshold=hazard_threshold,
    ))


@analysis_tools.tool
async def combine_core_hazard_analyses(
    ctx: RunContext[MapAgentDeps],
    pluvial_run_id: str,
    fluvial_run_id: str,
    coastal_run_id: str,
    scenario: Literal["current", "future"],
    exposure_threshold: int = 2,
) -> AnalysisRunSummary:
    """Combine aligned pluvial, fluvial, and coastal runs by pixelwise maximum."""

    if exposure_threshold not in {1, 2, 3}:
        raise ValueError("exposure_threshold must be 1, 2, or 3")
    _trace(ctx, "combine_core_hazard_analyses")
    return _show_layers(ctx, await ctx.deps.analysis.combine_hazards(
        pluvial_run_id=pluvial_run_id,
        fluvial_run_id=fluvial_run_id,
        coastal_run_id=coastal_run_id,
        scenario=scenario,
        exposure_threshold=exposure_threshold,
    ))


@analysis_tools.tool
async def get_core_coastal_dynamic_evidence(
    ctx: RunContext[MapAgentDeps],
    historical_hours: int = 24,
) -> AnalysisRunSummary:
    """Retrieve station-level tide, flood-monitoring, and configured tidal evidence.

    Warning/alert records remain separate from water-level observations, and
    station evidence is not a coastal flood-extent forecast.
    """

    if not 1 <= historical_hours <= 168:
        raise ValueError("historical_hours must be between 1 and 168")
    _trace(ctx, "get_core_coastal_dynamic_evidence")
    return await ctx.deps.analysis.coastal_dynamic_evidence(
        historical_hours=historical_hours
    )


@analysis_tools.tool
async def run_core_vulnerability_analysis(
    ctx: RunContext[MapAgentDeps],
    scenario: Literal["current", "future"] = "current",
    dimensions: list[
        Literal["demographic", "socioeconomic", "accessibility"]
    ] | None = None,
) -> AnalysisRunSummary:
    """Build relative vulnerability profiles from verified statistical geography."""

    _trace(ctx, "run_core_vulnerability_analysis")
    return _show_layers(ctx, await ctx.deps.analysis.run_vulnerability(
        scenario=scenario,
        dimensions=dimensions,
    ))


@analysis_tools.tool
async def run_core_priority_analysis(
    ctx: RunContext[MapAgentDeps],
    units: list[PriorityUnitInput],
    hazard_weight: float,
    exposure_weight: float,
    vulnerability_weight: float,
    scenario_name: str = "custom",
    top_n: int = 10,
) -> AnalysisRunSummary:
    """Rank explicit unit-level hazard, exposure, and vulnerability scores.

    Every unit must provide all three normalized scores. Do not invent scores or
    derive unit rankings from whole-area aggregate summaries.
    """

    if not units:
        raise ValueError("At least one unit is required")
    if not 1 <= top_n <= 100:
        raise ValueError("top_n must be between 1 and 100")
    weights = PriorityWeights(
        hazard=hazard_weight,
        exposure=exposure_weight,
        vulnerability=vulnerability_weight,
    )
    _trace(ctx, "run_core_priority_analysis")
    return _show_layers(ctx, await ctx.deps.analysis.run_priority(
        units=units,
        weights=weights,
        scenario_name=scenario_name,
        top_n=top_n,
    ))


@analysis_tools.tool
async def run_all_core_hazards(
    ctx: RunContext[MapAgentDeps],
    use_live_data: bool = True,
    forecast_horizon_hours: int = 6,
) -> AnalysisRunSummary:
    """Run current and future pluvial, fluvial, coastal and combined outputs in one call."""

    _trace(ctx, "run_all_core_hazards")
    return _show_layers(ctx, await ctx.deps.analysis.run_all_hazards(
        use_live_data=use_live_data,
        forecast_horizon=forecast_horizon_hours,
    ))


@analysis_tools.tool
async def run_core_flood_priority_assessment(
    ctx: RunContext[MapAgentDeps],
    scenario: Literal["current", "future"] = "future",
    use_live_data: bool = True,
    forecast_horizon_hours: int = 24,
    hazard_threshold: Literal[1, 2, 3] = 2,
    priority_scenario: Literal[
        "life_safety", "social_equity", "economic_protection"
    ] = "social_equity",
    all_hazards_run_id: str | None = None,
) -> AnalysisRunSummary:
    """Run the full deterministic Data Zone risk-priority assessment.

    This single call runs or reuses all hazards, then calculates population,
    building and official-facility exposure, multidimensional vulnerability,
    all three priority scenarios, and sensitivity outputs. It never asks the
    language model to manufacture unit scores.
    """

    if not 1 <= forecast_horizon_hours <= 48:
        raise ValueError("forecast_horizon_hours must be between 1 and 48")
    _trace(ctx, "run_core_flood_priority_assessment")
    return _show_layers(
        ctx,
        await ctx.deps.analysis.run_flood_priority_assessment(
            scenario=scenario,
            use_live_data=use_live_data,
            forecast_horizon=forecast_horizon_hours,
            hazard_threshold=hazard_threshold,
            priority_scenario=priority_scenario,
            all_hazards_run_id=all_hazards_run_id,
        ),
    )


@analysis_tools.tool
async def list_nrfa_historical_stations(
    ctx: RunContext[MapAgentDeps],
    dataset: Literal["nrfa_historical_river_flow", "nrfa_historical_rainfall"],
) -> AnalysisRunSummary:
    """List locally available NRFA historical flow or catchment-rainfall stations."""

    _trace(ctx, "list_nrfa_historical_stations")
    return await ctx.deps.analysis.nrfa_stations(dataset)


@analysis_tools.tool
async def query_nrfa_historical_series(
    ctx: RunContext[MapAgentDeps],
    dataset: Literal["nrfa_historical_river_flow", "nrfa_historical_rainfall"],
    station_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> AnalysisRunSummary:
    """Query an NRFA station's daily historical flow or rainfall time series."""

    _trace(ctx, "query_nrfa_historical_series")
    return await ctx.deps.analysis.nrfa_history(
        dataset=dataset,
        station_id=station_id,
        start_date=start_date,
        end_date=end_date,
    )


@analysis_tools.tool
async def run_historical_flood_validation(
    ctx: RunContext[MapAgentDeps],
    issue_time: str = "2023-10-06T06:00:00Z",
    forecast_horizon_hours: int = 24,
    hazard_threshold: Literal[1, 2, 3] = 2,
    priority_scenario: Literal[
        "life_safety", "social_equity", "economic_protection"
    ] = "social_equity",
) -> AnalysisRunSummary:
    """Validate archived UKV rainfall and Data Zone ranking without time leakage."""

    parsed = datetime.fromisoformat(issue_time.replace("Z", "+00:00"))
    _trace(ctx, "run_historical_flood_validation")
    return _show_layers(
        ctx,
        await ctx.deps.analysis.run_historical_validation(
            issue_time=parsed,
            forecast_horizon=forecast_horizon_hours,
            hazard_threshold=hazard_threshold,
            priority_scenario=priority_scenario,
        ),
    )


@analysis_tools.tool
async def plan_generalized_core_analysis(
    ctx: RunContext[MapAgentDeps],
    area: str,
    hazard_type: str,
    temporal_scope: Literal["historical", "current", "future"],
) -> GeneralizedAnalysisPlan:
    """Discover reusable components and missing data for a new area or hazard."""

    _trace(ctx, "plan_generalized_core_analysis")
    return await ctx.deps.analysis.generalized_plan(
        area=area,
        hazard_type=hazard_type,
        temporal_scope=temporal_scope,
    )


@analysis_tools.tool
async def register_core_hazard_extension(
    ctx: RunContext[MapAgentDeps],
    hazard_type: str,
    factors: list[ExtensionFactor],
    medium_threshold: float,
    high_threshold: float,
) -> AnalysisRunSummary:
    """Register a configuration-driven hazard workflow; use only when the user explicitly asks to extend the framework."""

    _trace(ctx, "register_core_hazard_extension")
    return await ctx.deps.analysis.register_extension(HazardExtensionSpec(
        hazard_type=hazard_type,
        factors=factors,
        medium_threshold=medium_threshold,
        high_threshold=high_threshold,
    ))


@analysis_tools.tool
async def run_registered_core_hazard(
    ctx: RunContext[MapAgentDeps],
    hazard_type: str,
    area: str,
    factor_paths_json: str,
) -> AnalysisRunSummary:
    """Run a registered hazard extension from a JSON object mapping factor names to local raster paths."""

    factor_paths = TypeAdapter(dict[str, str]).validate_json(factor_paths_json)
    _trace(ctx, "run_registered_core_hazard")
    return _show_layers(ctx, await ctx.deps.analysis.run_extension(
        hazard_type=hazard_type,
        area=area,
        factor_paths=factor_paths,
    ))


@analysis_tools.tool
async def compare_core_priority_scenarios(
    ctx: RunContext[MapAgentDeps],
    units: list[PriorityUnitInput],
    scenarios_json: str,
    top_n: int = 10,
) -> AnalysisRunSummary:
    """Compare rankings under explicit scenarios supplied as a JSON array.

    Each array item needs name and weights with hazard, exposure, and
    vulnerability values summing to one.
    """

    if not units:
        raise ValueError("At least one unit is required")
    scenarios = TypeAdapter(list[PriorityScenarioInput]).validate_json(scenarios_json)
    if len(scenarios) < 2:
        raise ValueError("At least two scenarios are required")
    _trace(ctx, "compare_core_priority_scenarios")
    return await ctx.deps.analysis.compare_priority(
        units=units,
        scenarios=scenarios,
        top_n=top_n,
    )


@analysis_tools.tool
async def run_core_priority_sensitivity(
    ctx: RunContext[MapAgentDeps],
    units: list[PriorityUnitInput],
    base_scenario_name: str,
    base_hazard_weight: float,
    base_exposure_weight: float,
    base_vulnerability_weight: float,
    vary_component: Literal["hazard", "exposure", "vulnerability"],
    values: list[float],
    top_n: int = 10,
) -> AnalysisRunSummary:
    """Measure ranking changes as one explicit priority weight varies."""

    if not units:
        raise ValueError("At least one unit is required")
    if not values or any(value < 0 or value > 1 for value in values):
        raise ValueError("values must contain weights between 0 and 1")
    base_scenario = PriorityScenarioInput(
        name=base_scenario_name,
        weights=PriorityWeights(
            hazard=base_hazard_weight,
            exposure=base_exposure_weight,
            vulnerability=base_vulnerability_weight,
        ),
    )
    _trace(ctx, "run_core_priority_sensitivity")
    return await ctx.deps.analysis.run_sensitivity(
        units=units,
        base_scenario=base_scenario,
        vary_component=vary_component,
        values=values,
        top_n=top_n,
    )


@analysis_tools.tool
async def compare_core_analysis_runs(
    ctx: RunContext[MapAgentDeps],
    baseline_run_id: str,
    comparison_run_id: str,
) -> AnalysisRunSummary:
    """Compare the summaries and provenance of two persisted analysis runs."""

    _trace(ctx, "compare_core_analysis_runs")
    return await ctx.deps.analysis.compare_runs(
        baseline_run_id=baseline_run_id,
        comparison_run_id=comparison_run_id,
    )
