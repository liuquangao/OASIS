from fastapi.testclient import TestClient
import httpx
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic import SecretStr

from hydromind.agent import spatial_agent
from hydromind.api import app
from hydromind.deps import MapAgentDeps
from hydromind.models.map_conversation import (
    MapSessionState,
    RememberedLocation,
    RiskReport,
)
from hydromind.runtime import _select_model, run_spatial_agent
from hydromind.settings import Settings
from hydromind.toolsets.rainfall import get_latest_rainfall_near_location


def test_spatial_agent_exists() -> None:
    assert spatial_agent is not None


def test_spatial_agent_exposes_recent_rainfall() -> None:
    registered_tools = {
        name
        for toolset in spatial_agent.toolsets
        for name in getattr(toolset, "tools", {})
    }
    assert "get_latest_rainfall_near_location" in registered_tools


async def test_map_rainfall_tool_uses_injected_provider_and_records_trace() -> None:
    class FakeRainfallProvider:
        request: dict[str, object] | None = None

        async def latest_rainfall_near_location(self, **kwargs):
            self.request = kwargs
            return "rainfall-summary"

    rainfall = FakeRainfallProvider()
    deps = MapAgentDeps(
        geocoder=None,  # type: ignore[arg-type]
        nearby_places=None,  # type: ignore[arg-type]
        rainfall=rainfall,  # type: ignore[arg-type]
        analysis=None,  # type: ignore[arg-type]
        current_hazard=None,  # type: ignore[arg-type]
        routing=None,  # type: ignore[arg-type]
        route_hazard=None,  # type: ignore[arg-type]
        state=MapSessionState(),
        events=[],
        tool_trace=[],
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    result = await get_latest_rainfall_near_location(
        ctx,
        latitude=55.8642,
        longitude=-4.2518,
    )

    assert result == "rainfall-summary"
    assert rainfall.request == {
        "latitude": 55.8642,
        "longitude": -4.2518,
        "radius_km": 20,
        "limit": 3,
    }
    assert deps.tool_trace == ["get_latest_rainfall_near_location"]


async def test_spatial_agent_returns_updated_session_envelope() -> None:
    state = MapSessionState(
        locations=[
            RememberedLocation(
                id="location-1",
                label="G2 8JB",
                search_query="G2 8JB",
                latitude=55.857087,
                longitude=-4.261645,
                class_value=3,
                risk_level="low",
                risk_label="Low",
            )
        ],
        active_location_id="location-1",
        hazard_layer_visible=True,
        last_task="risk",
    )
    model = TestModel(
        call_tools=[],
        custom_output_args={"message": "The existing point remains available."},
    )
    output = await run_spatial_agent("Summarise it", state, model=model)
    assert output.message == "The existing point remains available."
    assert output.state.active_location_id == "location-1"
    assert output.events == []


async def test_spatial_agent_persists_structured_risk_report() -> None:
    report = RiskReport(
        title="Glasgow flood risk",
        question="What is the flood risk tomorrow?",
        area="Glasgow",
        time_horizon="Next 24 hours",
        overall_risk="unknown",
        summary="Forecast evidence is unavailable.",
        limitations=["No forecast dataset was returned."],
    )
    model = TestModel(
        call_tools=[],
        custom_output_args={
            "message": "The forecast risk is unknown.",
            "risk_report": report.model_dump_json(),
        },
    )

    output = await run_spatial_agent(
        "What is the flood risk tomorrow?",
        MapSessionState(),
        model=model,
    )

    assert output.state.risk_report is not None
    assert output.state.risk_report.overall_risk == "unknown"
    assert output.state.risk_report.time_horizon == "Next 24 hours"


def test_api_requires_a_live_semantic_model(monkeypatch) -> None:
    monkeypatch.setenv("HYDROMIND_MODEL", "test")
    response = TestClient(app).post(
        "/agent/turn",
        json={
            "prompt": "格拉斯哥计算机学院",
            "state": {
                "locations": [],
                "visible_location_ids": [],
                "active_location_id": None,
                "hazard_layer_visible": False,
                "last_task": None,
            },
        },
    )
    assert response.status_code == 503


def test_api_allows_the_local_file_frontend_origin() -> None:
    response = TestClient(app).options(
        "/agent/turn",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "null"


def test_api_identifies_an_unavailable_hazard_service(monkeypatch) -> None:
    async def fail_on_hazard(*args, **kwargs):
        request = httpx.Request(
            "GET",
            "http://127.0.0.1:8080/geoserver/glasgow_flood/wms",
        )
        raise httpx.ConnectError("GeoServer is unavailable", request=request)

    monkeypatch.setenv("HYDROMIND_MODEL", "mimo-v2.5-pro")
    monkeypatch.setenv("HYDROMIND_MODEL_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setattr("hydromind.api.run_spatial_agent", fail_on_hazard)
    response = TestClient(app).post(
        "/agent/turn",
        json={"prompt": "Flood risk at G2 8JB"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "The hazard map service is unavailable."


def test_mimo_settings_build_an_openai_compatible_model() -> None:
    settings = Settings(
        model="mimo-v2.5-pro",
        model_provider="mimo",
        mimo_api_key=SecretStr("test-key"),
        mimo_base_url="https://token-plan-cn.xiaomimimo.com/v1",
    )
    model = _select_model(None, settings)
    assert model.model_name == "mimo-v2.5-pro"
    assert settings.semantic_model_configured is True


def test_mimo_settings_require_a_key() -> None:
    settings = Settings(model="mimo-v2.5-pro", model_provider="mimo")
    try:
        _select_model(None, settings)
    except RuntimeError as error:
        assert "MIMO_API_KEY" in str(error)
    else:
        raise AssertionError("MiMo configuration accepted a missing API key")


def test_vllm_settings_build_a_strict_openai_compatible_model() -> None:
    settings = Settings(
        model="qwen3.8-27b",
        model_provider="vllm",
        openai_base_url="http://127.0.0.1:8001/v1",
        openai_api_key=SecretStr("EMPTY"),
    )
    model = _select_model(None, settings)
    assert model.model_name == "qwen3.8-27b"
    assert model.profile["openai_chat_supports_multiple_system_messages"] is False
    assert settings.semantic_model_configured is True
