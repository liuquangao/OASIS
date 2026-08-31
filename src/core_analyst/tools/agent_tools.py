from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core_analyst.analysts.exposure_analysis import run_exposure_analysis as _run_exposure
from core_analyst.analysts.pluvial_prediction import PluvialPredictionAnalyst
from core_analyst.analysts.priority_analysis import (
    compare_priority_scenarios as _compare_priority_scenarios,
    run_priority_analysis as _run_priority,
    run_sensitivity_analysis as _run_sensitivity,
)
from core_analyst.analysts.temporal_reference_flood import TemporalReferenceFloodAnalyst
from core_analyst.analysts.vulnerability_analysis import run_vulnerability_analysis as _run_vulnerability
from core_analyst.coastal_dynamic import CoastalDynamicConfig, build_coastal_dynamic_evidence
from core_analyst.data_sources import MockRainfallAPISource, SEPAWaterLevelAPISource
from core_analyst.study_area import load_glasgow_1km_buffer_bounds
from core_analyst.utils.config import load_config
from core_analyst.workflows.oasis_real_data import (
    build_oasis_input_sources,
    build_reference_flood_sources,
)


SUPPORTED_HAZARDS = {"pluvial", "fluvial", "coastal"}
SUPPORTED_SCENARIOS = {"current", "future"}
VULNERABILITY_DIMENSIONS = ["demographic", "socioeconomic", "accessibility"]


class ToolInputError(ValueError):
    """Raised when an Agent-facing Core Analyst tool receives invalid parameters."""


def run_hazard_analysis(
    *,
    area: str = "glasgow",
    hazard_type: str,
    scenario: str,
    input_dir: str | Path = "Input",
    output_dir: str | Path = "outputs/agent_tools/hazard",
    forecast_horizon: int = 6,
    sepa_buffer_meters: float = 0.0,
    water_level_buffer_meters: float = 0.0,
    use_live_data: bool = False,
) -> dict[str, Any]:
    """Run one existing hazard analyst and return an Agent-safe structured result."""

    _validate_area(area)
    hazard_type = _validate_choice("hazard_type", hazard_type, SUPPORTED_HAZARDS)
    scenario = _validate_choice("scenario", scenario, SUPPORTED_SCENARIOS)
    output_root = Path(output_dir) / hazard_type / scenario
    parameters = {
        "area": area,
        "hazard_type": hazard_type,
        "scenario": scenario,
        "input_dir": str(input_dir),
        "forecast_horizon": forecast_horizon,
        "use_live_data": use_live_data,
    }
    warnings = _dynamic_data_warnings(hazard_type, use_live_data)
    try:
        if hazard_type == "pluvial":
            result = _run_pluvial_hazard(
                input_dir=input_dir,
                output_dir=output_root / "model",
                forecast_horizon=forecast_horizon,
                sepa_buffer_meters=sepa_buffer_meters,
                use_live_data=use_live_data,
            )
        else:
            result = _run_temporal_hazard(
                input_dir=input_dir,
                output_dir=output_root / "model",
                hazard_type=hazard_type,
                forecast_horizon=forecast_horizon,
                water_level_buffer_meters=water_level_buffer_meters,
                use_live_data=use_live_data,
            )
    except Exception as exc:
        return _failure("run_hazard_analysis", parameters, exc)

    return _format_hazard_result(
        hazard_type=hazard_type,
        scenario=scenario,
        result=result,
        parameters=parameters,
        warnings=warnings,
        tool="run_hazard_analysis",
    )


def run_hazard_scenarios(
    *,
    area: str = "glasgow",
    hazard_type: str,
    input_dir: str | Path = "Input",
    output_dir: str | Path = "outputs/agent_tools/hazard",
    forecast_horizon: int = 6,
    sepa_buffer_meters: float = 0.0,
    water_level_buffer_meters: float = 0.0,
    use_live_data: bool = False,
) -> dict[str, dict[str, Any]]:
    """Run one hazard model once and return its current and future outputs."""

    _validate_area(area)
    hazard_type = _validate_choice("hazard_type", hazard_type, SUPPORTED_HAZARDS)
    output_root = Path(output_dir) / hazard_type / "model"
    parameters = {
        "area": area,
        "hazard_type": hazard_type,
        "input_dir": str(input_dir),
        "forecast_horizon": forecast_horizon,
        "use_live_data": use_live_data,
    }
    warnings = _dynamic_data_warnings(hazard_type, use_live_data)
    try:
        if hazard_type == "pluvial":
            result = _run_pluvial_hazard(
                input_dir=input_dir,
                output_dir=output_root,
                forecast_horizon=forecast_horizon,
                sepa_buffer_meters=sepa_buffer_meters,
                use_live_data=use_live_data,
            )
        else:
            result = _run_temporal_hazard(
                input_dir=input_dir,
                output_dir=output_root,
                hazard_type=hazard_type,
                forecast_horizon=forecast_horizon,
                water_level_buffer_meters=water_level_buffer_meters,
                use_live_data=use_live_data,
            )
    except Exception as exc:
        failure = _failure("run_hazard_scenarios", parameters, exc)
        return {scenario: dict(failure) for scenario in sorted(SUPPORTED_SCENARIOS)}

    return {
        scenario: _format_hazard_result(
            hazard_type=hazard_type,
            scenario=scenario,
            result=result,
            parameters=parameters,
            warnings=warnings,
            tool="run_hazard_scenarios",
        )
        for scenario in ("current", "future")
    }


def run_exposure_analysis(
    *,
    hazard_result: dict[str, Any],
    exposure_sources: dict[str, Any] | None = None,
    exposure_types: list[str] | None = None,
    area: str = "glasgow",
    output_dir: str | Path = "outputs/agent_tools/exposure",
    hazard_threshold: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose Priority 1 hazard-to-exposure analysis through a stable tool contract."""

    _validate_area(area)
    exposure_sources = exposure_sources or {}
    exposure_types = exposure_types or ["population", "buildings", "critical_infrastructure"]
    hazard_type = hazard_result.get("hazard_type")
    scenario = hazard_result.get("scenario")
    if hazard_type not in SUPPORTED_HAZARDS or scenario not in SUPPORTED_SCENARIOS:
        raise ToolInputError("hazard_result must contain supported hazard_type and scenario.")
    if hazard_result.get("status") != "success":
        return _dependency_unavailable("run_exposure_analysis", "hazard_result", hazard_result)
    hazard_class = hazard_result.get("outputs", {}).get("hazard_class")
    if not hazard_class:
        return _dependency_unavailable("run_exposure_analysis", "hazard_class", hazard_result)

    filtered_sources = {name: exposure_sources.get(name) for name in exposure_types}
    result = _run_exposure(
        hazard_raster=hazard_class,
        exposure_sources=filtered_sources,
        output_dir=Path(output_dir) / hazard_type / scenario,
        hazard_type=hazard_type,
        scenario=scenario,
        hazard_threshold=hazard_threshold,
        config=config,
    )
    result.setdefault("metadata", {
        "analysis_method": "hazard_to_exposure",
        "hazard_type": hazard_type,
        "scenario": scenario,
        "diagnostics": result.get("diagnostics", {}),
    })
    result["provenance"]["upstream_hazard"] = hazard_result.get("provenance", {})
    return result


def run_vulnerability_analysis(
    *,
    area: str = "glasgow",
    geography_source: Any = None,
    vulnerability_sources: dict[str, Any] | None = None,
    vulnerability_dimensions: list[str] | None = None,
    output_dir: str | Path = "outputs/agent_tools/vulnerability",
    scenario: str = "current",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose Priority 2 vulnerability profiling through a stable tool contract."""

    _validate_area(area)
    scenario = _validate_choice("scenario", scenario, SUPPORTED_SCENARIOS)
    vulnerability_sources = vulnerability_sources or {}
    result = _run_vulnerability(
        geography_source=geography_source,
        vulnerability_sources=vulnerability_sources,
        output_dir=Path(output_dir) / scenario,
        scenario=scenario,
        config=_filter_vulnerability_config(config, vulnerability_dimensions),
    )
    result.setdefault("provenance", {})["tool_parameters"] = {
        "area": area,
        "scenario": scenario,
        "vulnerability_dimensions": vulnerability_dimensions or ["demographic", "socioeconomic", "accessibility"],
    }
    return result


def run_priority_analysis(
    *,
    exposure_result: dict[str, Any] | None = None,
    vulnerability_result: dict[str, Any] | None = None,
    hazard_result: dict[str, Any] | None = None,
    units: list[dict[str, Any]] | None = None,
    scenario: str | dict[str, Any] = "custom",
    output_dir: str | Path = "outputs/agent_tools/priority",
    weights: dict[str, float] | None = None,
    top_n: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic Core Analyst priority ranking over explicit component scores."""

    if exposure_result and exposure_result.get("status") == "unavailable":
        return _dependency_unavailable("run_priority_analysis", "exposure_result", exposure_result)
    if vulnerability_result and vulnerability_result.get("status") == "unavailable":
        return _dependency_unavailable("run_priority_analysis", "vulnerability_result", vulnerability_result)

    if weights is None:
        raise ToolInputError("Priority weights must be supplied explicitly.")
    explicit_weights = weights
    result = _run_priority(
        units=units,
        hazard_result=hazard_result,
        exposure_result=exposure_result,
        vulnerability_result=vulnerability_result,
        scenario=scenario,
        weights=explicit_weights,
        output_dir=output_dir,
        top_n=top_n,
        config=config,
    )

    result["summary"] = {
        "priority_proxy_is_value_dependent": True,
        "priority_score_is_value_dependent": True,
        "weights": result["weights"],
        "ranked_units": result["rankings"],
        "top_areas": result["top_areas"],
    }
    return result


def compare_priority_scenarios(
    *,
    scenarios: list[str | dict[str, Any]],
    units: list[dict[str, Any]] | None = None,
    hazard_result: dict[str, Any] | None = None,
    exposure_result: dict[str, Any] | None = None,
    vulnerability_result: dict[str, Any] | None = None,
    output_dir: str | Path = "outputs/agent_tools/priority_comparison",
    top_n: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare multiple explicit Core Analyst priority scenarios."""

    return _compare_priority_scenarios(
        scenarios=scenarios,
        units=units,
        hazard_result=hazard_result,
        exposure_result=exposure_result,
        vulnerability_result=vulnerability_result,
        output_dir=output_dir,
        top_n=top_n,
        config=config,
    )


def run_sensitivity_analysis(
    *,
    base_scenario: str | dict[str, Any],
    vary_component: str,
    values: list[float],
    units: list[dict[str, Any]] | None = None,
    hazard_result: dict[str, Any] | None = None,
    exposure_result: dict[str, Any] | None = None,
    vulnerability_result: dict[str, Any] | None = None,
    output_dir: str | Path = "outputs/agent_tools/priority_sensitivity",
    top_n: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic weight sensitivity analysis in Core Analyst."""

    return _run_sensitivity(
        base_scenario=base_scenario,
        vary_component=vary_component,
        values=values,
        units=units,
        hazard_result=hazard_result,
        exposure_result=exposure_result,
        vulnerability_result=vulnerability_result,
        output_dir=output_dir,
        top_n=top_n,
        config=config,
    )


def compare_scenarios(
    *,
    baseline_result: dict[str, Any],
    comparison_result: dict[str, Any],
    output_dir: str | Path = "outputs/agent_tools/scenario_comparison",
) -> dict[str, Any]:
    """Compare two structured tool results without recomputing spatial models."""

    warnings: list[dict[str, str]] = []
    baseline_summary = baseline_result.get("summary", {})
    comparison_summary = comparison_result.get("summary", {})
    baseline_status = baseline_result.get("status")
    comparison_status = comparison_result.get("status")
    success_states = {"success", "success_with_warnings", "partial", "available"}
    if baseline_status in success_states and comparison_status in success_states:
        status = "success"
    elif baseline_status == "failed" or comparison_status == "failed":
        status = "failed"
    elif baseline_status or comparison_status:
        status = "partial"
    else:
        status = "unavailable"
    result = {
        "status": status,
        "summary": {
            "baseline_status": baseline_status,
            "comparison_status": comparison_status,
            "baseline": baseline_summary,
            "comparison": comparison_summary,
        },
        "metadata": {
            "analysis_method": "structured_scenario_result_comparison",
            "comparison_type": "summary_and_provenance_comparison",
            "recomputed_spatial_analysis": False,
        },
        "outputs": {},
        "provenance": {
            "tool": "compare_scenarios",
            "baseline": baseline_result.get("provenance", {}),
            "comparison": comparison_result.get("provenance", {}),
        },
        "warnings": warnings,
    }
    output_path = Path(output_dir) / "scenario_comparison.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_json(result), encoding="utf-8")
    result["outputs"]["scenario_comparison"] = str(output_path)
    return result


def _run_pluvial_hazard(
    *,
    input_dir: str | Path,
    output_dir: Path,
    forecast_horizon: int,
    sepa_buffer_meters: float,
    use_live_data: bool,
) -> dict[str, Any]:
    config = load_config(_config_path("pluvial_prediction_config.yaml"))
    static_sources = build_oasis_input_sources(input_dir, rainfall_source="mock")
    if use_live_data:
        observed = build_oasis_input_sources(
            input_dir,
            rainfall_source="sepa",
            sepa_station_numbers=["auto"],
            sepa_buffer_meters=sepa_buffer_meters,
        )["rainfall"]
        forecast = build_oasis_input_sources(
            input_dir,
            rainfall_source="metoffice-site",
            metoffice_horizon_hours=forecast_horizon,
        )["rainfall"]
    else:
        observed = MockRainfallAPISource(base_mm_per_hour=12.0)
        forecast = MockRainfallAPISource(base_mm_per_hour=18.0)
    return PluvialPredictionAnalyst(config, output_dir).run(
        static_sources=static_sources,
        observed_rainfall_source=observed,
        forecast_rainfall_source=forecast,
        baseline_sources=None,
        prediction_horizon_hours=forecast_horizon,
    )


def _run_temporal_hazard(
    *,
    input_dir: str | Path,
    output_dir: Path,
    hazard_type: str,
    forecast_horizon: int,
    water_level_buffer_meters: float,
    use_live_data: bool,
) -> dict[str, Any]:
    config = load_config(_config_path(f"{hazard_type}_prediction_config.yaml"))
    high_sources = build_reference_flood_sources(input_dir, hazard_type=hazard_type, scenario="high")
    medium_sources = build_reference_flood_sources(input_dir, hazard_type=hazard_type, scenario="medium")
    low_sources = build_reference_flood_sources(input_dir, hazard_type=hazard_type, scenario="low")
    static_sources = build_oasis_input_sources(input_dir, rainfall_source="mock")
    rainfall_observation = None
    rainfall_forecast = None
    if use_live_data:
        rainfall_observation = build_oasis_input_sources(
            input_dir,
            rainfall_source="sepa",
            sepa_station_numbers=["auto"],
        )["rainfall"]
        rainfall_forecast = build_oasis_input_sources(
            input_dir,
            rainfall_source="metoffice-site",
            metoffice_horizon_hours=forecast_horizon,
        )["rainfall"]
    study_area_bounds = load_glasgow_1km_buffer_bounds(input_dir)
    water_level = (
        SEPAWaterLevelAPISource(
            discovery_buffer_meters=water_level_buffer_meters,
            discovery_bounds=study_area_bounds,
        )
        if use_live_data
        else None
    )

    if hazard_type == "fluvial":
        current_forcings = {}
        if rainfall_observation is not None:
            current_forcings["rainfall_observation"] = rainfall_observation
        if water_level is not None:
            current_forcings["river_level_observation"] = water_level
        static_forcings = {"river_network": static_sources["river_network"]}
        future_forcings = {}
        if rainfall_forecast is not None:
            future_forcings["rainfall_forecast"] = rainfall_forecast
        unavailable = {"river_forecast": "No river forecast endpoint has been configured."}
        if not use_live_data:
            unavailable.update(
                {
                    "rainfall_observation": "Live dynamic data was disabled.",
                    "rainfall_forecast": "Live dynamic data was disabled.",
                }
            )
    else:
        current_forcings = {}
        if water_level is not None:
            current_forcings["tide_sea_level_observation"] = water_level
        static_forcings = {}
        future_forcings = {}
        if rainfall_forecast is not None:
            future_forcings["rainfall_forecast"] = rainfall_forecast
        unavailable = {
            "tide_sea_level_forecast": "No tide/sea-level forecast endpoint has been configured.",
            "storm_surge_forecast": "Storm surge forecast data is not available.",
        }
    dynamic_evidence = (
        build_coastal_dynamic_evidence(
            CoastalDynamicConfig(
                input_dir=input_dir,
                historical_hours=24,
                search_radius_km=120.0,
                candidate_station_reference="E74039",
            )
        )
        if hazard_type == "coastal" and use_live_data
        else None
    )

    return TemporalReferenceFloodAnalyst(hazard_type, config, output_dir).run(
        dem_source=high_sources["dem"],
        baseline_high_source=high_sources["reference_flood"],
        baseline_medium_source=medium_sources["reference_flood"],
        baseline_low_source=low_sources["reference_flood"],
        static_forcings=static_forcings,
        current_forcings=current_forcings,
        future_forcings=future_forcings,
        unavailable=unavailable,
        dynamic_evidence=dynamic_evidence,
    )


def _scenario_outputs(outputs: dict[str, Any], scenario: str) -> dict[str, Any]:
    prefix = f"{scenario}_hazard"
    return {
        "hazard_index": outputs.get(f"{prefix}_index"),
        "hazard_class": outputs.get(f"{prefix}_class"),
        "metadata": outputs.get("metadata"),
        "risk_logic": outputs.get("risk_logic"),
    }


def _dynamic_data_warnings(hazard_type: str, use_live_data: bool) -> list[dict[str, str]]:
    if use_live_data:
        return []
    message = (
        "Live dynamic observations/forecasts were disabled; pluvial output uses explicit demo rainfall."
        if hazard_type == "pluvial"
        else "Live dynamic observations/forecasts were disabled; output uses static reference evidence only."
    )
    return [{"code": "dynamic_data_disabled", "message": message}]


def _format_hazard_result(
    *,
    hazard_type: str,
    scenario: str,
    result: dict[str, Any],
    parameters: dict[str, Any],
    warnings: list[dict[str, str]],
    tool: str,
) -> dict[str, Any]:
    outputs = _scenario_outputs(result["output_paths"], scenario)
    scenario_parameters = {**parameters, "scenario": scenario}
    return {
        "status": "success",
        "hazard_type": hazard_type,
        "scenario": scenario,
        "outputs": outputs,
        "summary": {
            "hazard_type": hazard_type,
            "scenario": scenario,
            "available_outputs": sorted(outputs),
            "analysis_method": result["metadata"].get("analysis_method"),
        },
        "metadata": result["metadata"],
        "provenance": {
            "tool": tool,
            "parameters": scenario_parameters,
            "metadata": result["metadata"],
            "processing": "existing_core_analyst_hazard_pipeline",
        },
        "warnings": list(warnings),
    }


def _config_path(filename: str) -> Path:
    """Resolve packaged project configs without depending on the process CWD."""

    configured = os.getenv("OASIS_CORE_ANALYST_CONFIG_DIR")
    candidates = []
    if configured:
        candidates.append(Path(configured) / filename)
    candidates.extend(
        [
            Path("analysis/core-analyst/config") / filename,
            Path(__file__).resolve().parents[3]
            / "analysis"
            / "core-analyst"
            / "config"
            / filename,
            Path("config") / filename,
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _filter_vulnerability_config(config: dict[str, Any] | None, dimensions: list[str] | None) -> dict[str, Any] | None:
    if not config or not dimensions:
        return config
    filtered = {**config}
    vulnerability = dict(filtered.get("vulnerability", filtered))
    for dimension in VULNERABILITY_DIMENSIONS:
        if dimension not in dimensions and dimension in vulnerability:
            vulnerability[dimension] = {"indicators": []} if dimension != "accessibility" else {"source_key": "__disabled__"}
    filtered["vulnerability"] = vulnerability
    return filtered


def _validate_area(area: str) -> None:
    if area.lower() != "glasgow":
        raise ToolInputError("Only Glasgow is currently supported by the local Core Analyst data contract.")


def _validate_choice(name: str, value: str, choices: set[str]) -> str:
    value = value.lower()
    if value not in choices:
        raise ToolInputError(f"{name} must be one of {sorted(choices)}.")
    return value


def _failure(tool: str, parameters: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "summary": {"error": str(exc)},
        "metadata": {"analysis_method": tool, "failure_type": type(exc).__name__},
        "outputs": {},
        "provenance": {"tool": tool, "parameters": parameters},
        "warnings": [{"code": "tool_execution_failed", "message": str(exc)}],
    }


def _dependency_unavailable(tool: str, dependency: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "summary": {"reason": f"{dependency}_unavailable"},
        "metadata": {"analysis_method": tool, "dependency": dependency},
        "outputs": {},
        "provenance": {"tool": tool, "dependency": dependency, "upstream_status": result.get("status")},
        "warnings": [{"code": f"{dependency}_unavailable", "message": f"{tool} requires an available {dependency}."}],
    }


def _exposure_proxy(exposure_result: dict[str, Any]) -> float | None:
    summary = exposure_result.get("summary", {})
    values = []
    for key in ("population", "buildings"):
        value = summary.get(key, {}).get("exposure_ratio")
        if value is not None:
            values.append(float(value))
    critical = summary.get("critical_infrastructure", {})
    if critical.get("total"):
        values.append(float(critical.get("exposed", 0)) / float(critical["total"]))
    return None if not values else sum(values) / len(values)


def _sum_to_one(weights: dict[str, float]) -> bool:
    return abs(sum(float(value) for value in weights.values()) - 1.0) <= 1e-6


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, indent=2)
