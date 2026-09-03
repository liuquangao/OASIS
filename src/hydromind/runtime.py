"""Lifecycle wrapper for a complete PydanticAI Agent run."""

from __future__ import annotations

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

import json

from hydromind.agent import (
    filtered_toolsets,
    flood_agent,
    intent_agent,
    spatial_agent,
    spatial_execution_agent,
)
from hydromind.assessment import create_assessment_plan
from hydromind.deps import Deps, MapAgentDeps
from hydromind.integrations.current_hazard import CoreAnalystCurrentHazard
from hydromind.integrations.core_analysis import CoreAnalystAnalysisService
from hydromind.integrations.geoserver import GeoServerPublisher
from hydromind.integrations.openstreetmap import OpenStreetMapClient
from hydromind.integrations.osrm import OsrmRoutingClient
from hydromind.integrations.raster_route_hazard import RasterRouteHazardAnalyzer
from hydromind.integrations.sepa import SepaTimeSeriesClient
from hydromind.models.agent_output import AgentOutput
from hydromind.models.map_conversation import MapAgentResponse, MapSessionState, RiskReport
from hydromind.settings import Settings


def build_analysis_service(settings: Settings, *, publish: bool = True) -> CoreAnalystAnalysisService:
    publisher = GeoServerPublisher(
        settings.geoserver_rest_url,
        settings.geoserver_wms_url,
        settings.geoserver_user,
        settings.geoserver_password.get_secret_value(),
    ) if publish else None
    current_hazard = CoreAnalystCurrentHazard(
        input_dir=settings.core_analyst_input_dir,
        config_path=settings.core_analyst_config_path,
        output_dir=settings.current_hazard_output_dir,
        raster_path=settings.current_hazard_raster_path,
        wms_url=settings.geoserver_wms_url,
        layer=settings.current_hazard_layer,
        publisher=publisher,
    )
    return CoreAnalystAnalysisService(
        input_dir=settings.core_analyst_input_dir,
        output_dir=settings.core_analyst_analysis_output_dir,
        config_dir=settings.core_analyst_config_dir,
        current_hazard=current_hazard,
        current_hazard_raster_path=settings.current_hazard_raster_path,
        metoffice_sample_grid_size=settings.metoffice_sample_grid_size,
        publisher=publisher,
        ceda_access_token=(
            settings.ceda_access_token.get_secret_value()
            if settings.ceda_access_token else None
        ),
        ceda_username=settings.ceda_username,
        ceda_password=(
            settings.ceda_password.get_secret_value() if settings.ceda_password else None
        ),
        historical_ukv_path=settings.historical_ukv_path,
    )


def _select_model(
    requested_model: str | Model | None,
    settings: Settings,
) -> str | Model:
    """Bind provider-neutral settings to the selected model implementation."""

    if requested_model is not None:
        return requested_model
    if settings.model_provider == "mimo":
        if settings.mimo_api_key is None:
            raise RuntimeError("MIMO_API_KEY is required for the MiMo provider.")
        return OpenAIChatModel(
            settings.model,
            provider=OpenAIProvider(
                base_url=settings.mimo_base_url,
                api_key=settings.mimo_api_key.get_secret_value(),
            ),
        )
    if settings.model_provider == "vllm":
        if settings.openai_base_url is None or settings.openai_api_key is None:
            raise RuntimeError(
                "OPENAI_BASE_URL and OPENAI_API_KEY are required for the vLLM provider."
            )
        return OpenAIChatModel(
            settings.model,
            provider=OpenAIProvider(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key.get_secret_value(),
            ),
            profile=OpenAIModelProfile(
                openai_chat_supports_multiple_system_messages=False,
            ),
        )
    return settings.model


async def run_agent(
    prompt: str,
    *,
    model: str | Model | None = None,
    settings: Settings | None = None,
) -> AgentOutput:
    settings = settings or Settings.from_env()
    selected_model = _select_model(model, settings)
    if selected_model == "test":
        selected_model = TestModel(
            call_tools=[],
            custom_output_args={
                "answer": (
                    "Offline PydanticAI smoke test completed; no live LLM "
                    "reasoning or tool selection was performed."
                ),
                "evidence": [],
                "unresolved_questions": [
                    "Configure a real model provider to exercise autonomous tool use."
                ],
                "requires_human_review": True,
            },
        )
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        sepa_timeseries = SepaTimeSeriesClient(client)
        deps = Deps(
            hydrometry=sepa_timeseries,
            rainfall=sepa_timeseries,
        )
        result = await flood_agent.run(prompt, deps=deps, model=selected_model)
        return result.output


async def run_spatial_agent(
    prompt: str,
    state: MapSessionState,
    *,
    model: str | Model | None = None,
    settings: Settings | None = None,
) -> MapAgentResponse:
    """Run one state-aware turn in which the model calls geospatial tools."""

    settings = settings or Settings.from_env()
    selected_model = _select_model(model, settings)
    if selected_model == "test":
        selected_model = TestModel(
            call_tools=[],
            custom_output_args={
                "message": "No live language model is configured.",
            },
        )
    state_payload = _compact_session_state(state)
    state_json = json.dumps(state_payload, ensure_ascii=False)
    user_input = f"CURRENT SESSION STATE:\n{state_json}\n\nUSER MESSAGE:\n{prompt}"
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        osm = OpenStreetMapClient(
            client,
            nominatim_url=settings.nominatim_url,
        )
        analysis = build_analysis_service(settings)
        current_hazard = analysis.current_hazard
        routing = OsrmRoutingClient(client, base_url=settings.osrm_url)
        route_hazard = RasterRouteHazardAnalyzer(settings.current_hazard_raster_path)
        sepa_timeseries = SepaTimeSeriesClient(client)
        deps = MapAgentDeps(
            geocoder=osm,
            nearby_places=osm,
            rainfall=sepa_timeseries,
            analysis=analysis,
            current_hazard=current_hazard,
            routing=routing,
            route_hazard=route_hazard,
            state=state,
            events=[],
            tool_trace=[],
        )
        if isinstance(selected_model, TestModel):
            result = await spatial_agent.run(user_input, deps=deps, model=selected_model)
        else:
            intent_input = (
                f"COMPACT MAP CONTEXT:\n{state_json}\n\nUSER MESSAGE:\n{prompt}"
            )
            intent_result = await intent_agent.run(
                intent_input,
                model=selected_model,
                model_settings=_intent_model_settings(settings),
            )
            intent = intent_result.output
            deps.tool_trace.append("route_analysis_intent")
            if intent.category in {"integrated_risk", "historical_validation"}:
                readiness = await analysis.data_readiness()
                deps.tool_trace.append("get_core_analysis_data_readiness")
                plan = create_assessment_plan(
                    question=prompt,
                    intent=intent,
                    readiness=readiness,
                    output_dir=settings.core_analyst_analysis_output_dir,
                    reusable_run_id=state.recent_analysis_run_id,
                )
                state.pending_assessment = plan
                state.last_task = (
                    "historical"
                    if intent.category == "historical_validation"
                    else "assessment"
                )
                missing = len(plan.missing_datasets)
                message = (
                    f"I prepared a {plan.preferences.forecast_horizon_hours}-hour "
                    f"{plan.preferences.priority_scenario.replace('_', ' ')} assessment plan. "
                    f"Review the proposed threshold and value weights, then confirm to run it. "
                    f"Data readiness reports {missing} unavailable or partial inputs."
                )
                return MapAgentResponse(
                    message=message,
                    state=state,
                    events=[],
                    tools_used=deps.tool_trace,
                    pending_assessment=plan,
                    execution_trace=plan.steps,
                )
            result = await spatial_execution_agent.run(
                user_input,
                deps=deps,
                model=selected_model,
                model_settings=_execution_model_settings(settings),
                toolsets=filtered_toolsets(intent.category),
            )
        report = result.output.risk_report
        deps.state.risk_report = (
            RiskReport.model_validate_json(report)
            if isinstance(report, str)
            else report
        )
        return MapAgentResponse(
            message=result.output.message,
            state=deps.state,
            events=deps.events,
            tools_used=deps.tool_trace,
            pending_assessment=deps.state.pending_assessment,
        )


def _compact_session_state(state: MapSessionState) -> dict:
    """Keep reference resolution while excluding bulky geometry and old reports."""

    return {
        "locations": [
            {
                "id": item.id,
                "label": item.label,
                "latitude": item.latitude,
                "longitude": item.longitude,
                "risk_level": item.risk_level,
                "class_value": item.class_value,
            }
            for item in state.locations
        ],
        "visible_location_ids": state.visible_location_ids,
        "routes": [
            {"id": item.id, "label": item.label, "rank": item.rank}
            for item in state.routes
        ],
        "visible_route_ids": state.visible_route_ids,
        "active_location_id": state.active_location_id,
        "hazard_layer_visible": state.hazard_layer_visible,
        "visible_analysis_layer_ids": state.visible_analysis_layer_ids,
        "recent_analysis_run_id": state.recent_analysis_run_id,
        "pending_plan_id": (
            state.pending_assessment.plan_id if state.pending_assessment else None
        ),
        "last_task": state.last_task,
    }


def _intent_model_settings(settings: Settings) -> OpenAIChatModelSettings:
    values: OpenAIChatModelSettings = {
        "temperature": 0,
        "max_tokens": 400,
    }
    if settings.model_provider == "vllm":
        values["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }
    return values


def _execution_model_settings(settings: Settings) -> OpenAIChatModelSettings:
    values: OpenAIChatModelSettings = {"temperature": 0, "max_tokens": 1200}
    if settings.model_provider == "vllm":
        values["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }
    return values
