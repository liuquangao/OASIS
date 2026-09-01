"""Lifecycle wrapper for a complete PydanticAI Agent run."""

from __future__ import annotations

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

import json

from oasis.agent import flood_agent, spatial_agent
from oasis.deps import Deps, MapAgentDeps
from oasis.integrations.current_hazard import CoreAnalystCurrentHazard
from oasis.integrations.core_analysis import CoreAnalystAnalysisService
from oasis.integrations.geoserver import GeoServerPublisher
from oasis.integrations.openstreetmap import OpenStreetMapClient
from oasis.integrations.osrm import OsrmRoutingClient
from oasis.integrations.raster_route_hazard import RasterRouteHazardAnalyzer
from oasis.integrations.sepa import SepaTimeSeriesClient
from oasis.models.agent_output import AgentOutput
from oasis.models.map_conversation import MapAgentResponse, MapSessionState, RiskReport
from oasis.settings import Settings


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
    state_payload = state.model_dump(mode="json")
    for route in state_payload.get("routes", []):
        route.pop("coordinates", None)
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
        result = await spatial_agent.run(
            user_input,
            deps=deps,
            model=selected_model,
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
        )
