from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from hydromind.settings import Settings
from hydromind.setup import SetupCoordinator, configuration_status


def _verification(*errors: str) -> dict:
    return {
        "ok": not errors,
        "profile": "glasgow-5m-exact",
        "checked": ["raster.tif"] if not errors else [],
        "errors": list(errors),
    }


def test_setup_reports_required_and_optional_configuration(monkeypatch) -> None:
    monkeypatch.delenv("METOFFICE_SITE_API_KEY", raising=False)
    monkeypatch.delenv("ADMIRALTY_API_KEY", raising=False)
    monkeypatch.delenv("CEDA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CEDA_USERNAME", raising=False)
    monkeypatch.delenv("CEDA_PASSWORD", raising=False)
    settings = Settings(model="openai:gpt-5-mini")

    items = {item["id"]: item for item in configuration_status(settings)}

    assert items["agent_model"]["configured"] is False
    assert items["agent_model"]["environment_variables"] == ["HYDROMIND_MODEL", "OPENAI_API_KEY"]
    assert items["metoffice_forecast"]["configured"] is False
    assert items["admiralty_tides"]["importance"] == "optional"
    assert items["ceda_historical_forecast"]["configured"] is False
    assert "CEDA_ACCESS_TOKEN" in items["ceda_historical_forecast"]["environment_variables"]


def test_openai_model_requires_its_api_key() -> None:
    assert Settings(model="openai:gpt-5-mini").semantic_model_configured is False
    assert Settings(
        model="openai:gpt-5-mini",
        openai_api_key=SecretStr("configured"),
    ).semantic_model_configured is True


def test_setup_can_automatically_repair_only_missing_risk_inputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "hydromind.setup.verify_glasgow_5m",
        lambda _: _verification("missing processed/facilities/critical_services.geojson"),
    )
    coordinator = SetupCoordinator()
    settings = Settings(
        model="openai:gpt-5-mini",
        openai_api_key=SecretStr("configured"),
        core_analyst_input_dir=tmp_path,
    )

    status = coordinator.status(settings)

    assert status["data"]["automatic_action"] == "prepare_risk"
    assert status["can_use_agent"] is False


def test_full_rebuild_waits_for_the_licensed_lcm(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "hydromind.setup.verify_glasgow_5m",
        lambda _: _verification("missing HYDROMIND_Rasters/HYDROMIND_Rasters/DTM_5m_res.tif"),
    )
    coordinator = SetupCoordinator()
    lcm_path = tmp_path / "gb2019lcm25m.tif"
    lcm_path.touch()

    blocked = coordinator.status(Settings(core_analyst_input_dir=tmp_path))
    enabled = coordinator.status(Settings(
        core_analyst_input_dir=tmp_path,
        lcm2019_path=lcm_path,
        accept_data_licences=True,
    ))

    assert blocked["data"]["automatic_action"] == "configure_lcm"
    assert enabled["data"]["automatic_action"] == "rebuild_all"


async def test_initialize_runs_risk_preparation_once_and_reverifies(monkeypatch, tmp_path: Path) -> None:
    prepared = False

    def verify(_: Path) -> dict:
        if prepared:
            return _verification()
        return _verification("missing processed/data_zone/enriched.geojson")

    def prepare(*args, **kwargs) -> dict:
        nonlocal prepared
        prepared = True
        return {"status": "success"}

    monkeypatch.setattr("hydromind.setup.verify_glasgow_5m", verify)
    monkeypatch.setattr("hydromind.setup.prepare_risk_inputs", prepare)
    coordinator = SetupCoordinator()
    settings = Settings(
        model="openai:gpt-5-mini",
        openai_api_key=SecretStr("configured"),
        core_analyst_input_dir=tmp_path,
    )

    started = coordinator.initialize(settings)
    assert started["job"]["state"] == "running"
    assert started["job"]["action"] == "prepare_risk"
    assert coordinator._task is not None
    await coordinator._task

    complete = coordinator.status(settings)
    assert complete["job"]["state"] == "complete"
    assert complete["data"]["status"] == "complete"
    assert complete["can_use_agent"] is True
