"""PydanticAI tools for the extended deterministic Core Analyst pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic_ai import FunctionToolset, RunContext

from hydromind.deps import MapAgentDeps
from hydromind.models.analysis import (
    AnalysisRunSummary,
    DataReadinessSummary,
    GeneralizedAnalysisPlan,
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
