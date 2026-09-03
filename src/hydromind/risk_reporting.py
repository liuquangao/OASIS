"""Plain-language, auditable reports for deterministic priority runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio.features import bounds as geometry_bounds, geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds, transform as window_transform

from hydromind.models.analysis import AnalysisRunSummary
from hydromind.models.assessment import AssessmentPreferences
from hydromind.models.map_conversation import (
    RiskReport,
    RiskReportCalculation,
    RiskReportContribution,
    RiskReportDriver,
    RiskReportEvidence,
    RiskReportFinding,
)


RunLoader = Callable[[str], dict[str, Any]]


def build_priority_risk_report(
    *,
    question: str,
    result: AnalysisRunSummary,
    quality: dict[str, Any],
    preferences: AssessmentPreferences,
    load_run: RunLoader,
) -> RiskReport:
    """Turn persisted calculation inputs into a readable decision report."""

    result_payload = load_run(result.run_id)
    source_payload = _source_assessment(result_payload, load_run)
    top = result.summary.get("top_areas", [])[:5]
    rows = _assessment_rows(source_payload)
    weights = preferences.weights.model_dump()
    all_hazards_id = source_payload.get("provenance", {}).get("all_hazards_run_id")
    hazards = load_run(str(all_hazards_id)) if all_hazards_id else None
    scenario = str(source_payload.get("summary", {}).get("scenario", preferences.scenario))
    static_facts = _area_static_facts(source_payload, hazards, scenario, top) if hazards else {}
    findings = [
        _finding(
            item,
            rows.get(str(item.get("id"))),
            weights,
            static_facts.get(str(item.get("id"))),
        )
        for item in top
    ]
    quality_label = str(quality.get("status", "unknown"))

    return RiskReport(
        title="Glasgow flood risk and social-priority assessment",
        question=question,
        area="Glasgow",
        time_horizon=_time_horizon(preferences, scenario),
        overall_risk="mixed" if quality_label != "fail" else "unknown",
        summary=(
            "This relative intervention ranking combines the SEPA mapped flood background, "
            "latest observations and forecast rainfall with exposure and vulnerability. "
            f"Quality review: {quality_label}."
        ),
        key_findings=[finding.explanation for finding in findings],
        drivers=_hazard_drivers(hazards, scenario) if hazards else [],
        findings=findings,
        calculation=RiskReportCalculation(
            lens=_lens_label(preferences.priority_scenario),
            formula="priority = hazard × weight + exposure × weight + vulnerability × weight",
            weights=weights,
        ),
        evidence=[
            RiskReportEvidence(
                label="Deterministic analysis run",
                value=result.run_id,
                source=f"HydroMind {result.analysis_type}",
            ),
            RiskReportEvidence(
                label="Quality gate",
                value=quality_label,
                source="quality_report.json",
            ),
        ],
        limitations=[
            "Priority is a relative intervention ranking, not a flood probability or official warning.",
            "Light, moderate and heavy rainfall are explanatory bands used by this model, not Met Office warning categories.",
        ],
    )


def _source_assessment(payload: dict[str, Any], load_run: RunLoader) -> dict[str, Any]:
    if payload.get("analysis_type") != "priority_rerank":
        return payload
    return load_run(str(payload["provenance"]["source_run_id"]))


def _assessment_rows(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    path = Path(payload.get("outputs", {}).get("data_zone_assessment", ""))
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {str(row["id"]): row for row in csv.DictReader(handle)}


def _finding(
    item: dict[str, Any],
    row: dict[str, str] | None,
    weights: dict[str, float],
    static_fact: str | None,
) -> RiskReportFinding:
    scores = {
        component: float(item[f"{component}_score"])
        for component in ("hazard", "exposure", "vulnerability")
    }
    contributions = [
        RiskReportContribution(
            component=component,
            score=score,
            weight=float(weights[component]),
            contribution=score * float(weights[component]),
        )
        for component, score in scores.items()
    ]
    dominant = max(contributions, key=lambda value: value.contribution)
    facts = ([static_fact] if static_fact else []) + _area_facts(row)
    reason = {
        "hazard": "the mapped flood hazard",
        "exposure": "the number of people, buildings and critical facilities exposed",
        "vulnerability": "the relative social vulnerability indicators",
    }[dominant.component]
    hazard_fraction = _number(row.get("hazardous_area_fraction")) if row else None
    hazard_detail = (
        f"After observations and forecast inputs are added, {hazard_fraction:.0%} of its classified area is in flood class 2 or 3. "
        if hazard_fraction is not None else ""
    )
    background = f"{static_fact} " if static_fact else ""
    return RiskReportFinding(
        area_id=str(item.get("id")),
        name=str(item.get("name") or item.get("id")),
        rank=int(item.get("rank")),
        priority_score=float(item.get("priority_score")),
        explanation=f"{background}{hazard_detail}It ranks #{item.get('rank')} mainly because of {reason}.",
        facts=facts,
        contributions=contributions,
    )


def _area_facts(row: dict[str, str] | None) -> list[str]:
    if not row:
        return []
    facts: list[str] = []
    fraction = _number(row.get("hazardous_area_fraction"))
    if fraction is not None:
        facts.append(f"{fraction:.0%} of its classified area is in flood class 2 or 3.")
    population = _number(row.get("estimated_exposed_population"))
    if population is not None:
        facts.append(f"About {population:,.0f} residents are estimated to be exposed.")
    exposed_buildings = _number(row.get("exposed_building_count"))
    buildings = _number(row.get("building_count"))
    if exposed_buildings is not None and buildings is not None:
        facts.append(f"{exposed_buildings:,.0f} of {buildings:,.0f} mapped buildings are exposed.")
    exposed_facilities = _number(row.get("exposed_critical_facility_count"))
    facilities = _number(row.get("critical_facility_count"))
    if exposed_facilities is not None and facilities is not None:
        facts.append(f"{exposed_facilities:,.0f} of {facilities:,.0f} critical facilities are exposed.")
    return facts


def _area_static_facts(
    assessment: dict[str, Any],
    hazards: dict[str, Any],
    scenario: str,
    top: list[dict[str, Any]],
) -> dict[str, str]:
    geojson_path = Path(assessment.get("outputs", {}).get("priority_by_data_zone", ""))
    if not geojson_path.is_file():
        return {}
    outputs = hazards.get("outputs", {})
    fluvial = _metadata_for(outputs.get("fluvial_future_hazard_class"))
    coastal = _metadata_for(outputs.get("coastal_future_hazard_class"))
    baseline_key = "baseline_low" if scenario == "future" else "baseline_high"
    fluvial_path = _baseline_path(fluvial, baseline_key)
    coastal_path = _baseline_path(coastal, baseline_key)
    if not fluvial_path and not coastal_path:
        return {}

    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    geometries = {
        str(feature.get("properties", {}).get("id")): feature.get("geometry")
        for feature in payload.get("features", [])
        if feature.get("geometry")
    }
    likelihood = "low-likelihood" if scenario == "future" else "high-likelihood"
    facts: dict[str, str] = {}
    for item in top:
        area_id = str(item.get("id"))
        geometry = geometries.get(area_id)
        if not geometry:
            continue
        parts = []
        if fluvial_path:
            parts.append(f"river {_coverage(fluvial_path, geometry):.0%}")
        if coastal_path:
            parts.append(f"coastal {_coverage(coastal_path, geometry):.0%}")
        facts[area_id] = (
            f"In the SEPA {likelihood} static flood envelope: " + ", ".join(parts) + "."
        )
    return facts


def _baseline_path(metadata: dict[str, Any], key: str) -> str | None:
    entry = metadata.get("static_baseline", {}).get(key, {})
    if not isinstance(entry, dict):
        return None
    path = entry.get("metadata", {}).get("path")
    return str(path) if path else None


def _coverage(path: str, geometry: dict[str, Any]) -> float:
    with rasterio.open(path) as dataset:
        projected = transform_geom("EPSG:4326", str(dataset.crs), geometry)
        left, bottom, right, top = geometry_bounds(projected)
        raw = from_bounds(left, bottom, right, top, dataset.transform)
        column_start = max(0, int(np.floor(raw.col_off)))
        row_start = max(0, int(np.floor(raw.row_off)))
        column_stop = min(dataset.width, int(np.ceil(raw.col_off + raw.width)))
        row_stop = min(dataset.height, int(np.ceil(raw.row_off + raw.height)))
        window = Window(
            column_start,
            row_start,
            column_stop - column_start,
            row_stop - row_start,
        )
        values = dataset.read(1, window=window, masked=True)
        inside = geometry_mask(
            [projected],
            out_shape=values.shape,
            transform=window_transform(window, dataset.transform),
            invert=True,
        )
        valid = inside & ~np.ma.getmaskarray(values)
        return 0.0 if not np.any(valid) else float(np.count_nonzero(np.isin(values.data[valid], (1, 2, 3))) / np.count_nonzero(valid))


def _hazard_drivers(hazards: dict[str, Any], scenario: str) -> list[RiskReportDriver]:
    outputs = hazards.get("outputs", {})
    fluvial = _metadata_for(outputs.get("fluvial_future_hazard_class"))
    coastal = _metadata_for(outputs.get("coastal_future_hazard_class"))
    pluvial = _metadata_for(outputs.get("pluvial_future_hazard_class"))
    drivers: list[RiskReportDriver] = []
    static_driver = _static_driver(fluvial, coastal, scenario)
    if static_driver:
        drivers.append(static_driver)

    observed = pluvial.get("observed_rainfall_source", {}).get("metadata", {}).get("observations", {})
    observed_driver = _rainfall_driver(
        label="Current observed rainfall",
        observations=observed,
        source="SEPA rainfall stations",
        forecast=False,
        explanation="Used in current pluvial and fluvial hazard; the future calculation also carries the current hazard state forward.",
    )
    if observed_driver:
        drivers.append(observed_driver)

    river = fluvial.get("current_forcings", {}).get("river_level_observation", {}).get("metadata", {}).get("observations", {})
    river_driver = _water_level_driver(river)
    if river_driver:
        drivers.append(river_driver)

    forecast = pluvial.get("forecast_rainfall_source", {}).get("metadata", {}).get("observations", {})
    forecast_driver = _rainfall_driver(
        label="Forecast rainfall",
        observations=forecast,
        source="Met Office Site-Specific Forecast",
        forecast=True,
        explanation="Used directly in the future pluvial and fluvial calculations and as a coastal future rainfall factor.",
    )
    if forecast_driver:
        drivers.append(forecast_driver)

    drivers.append(
        RiskReportDriver(
            label="Combined flood hazard",
            value="Highest class wins at each 5 m cell",
            explanation="Pluvial, fluvial and coastal outputs are combined by pixelwise maximum, avoiding double-counting where hazards overlap.",
            source="HydroMind deterministic all-hazards workflow",
        )
    )
    return drivers


def _static_driver(
    fluvial: dict[str, Any], coastal: dict[str, Any], scenario: str
) -> RiskReportDriver | None:
    fluvial_weights = fluvial.get("weights", {}).get(f"{scenario}_weights", {})
    coastal_weights = coastal.get("weights", {}).get(f"{scenario}_weights", {})
    fluvial_static = sum(
        float(value) for name, value in fluvial_weights.items() if name.startswith("baseline_")
    )
    coastal_static = sum(
        float(value) for name, value in coastal_weights.items() if name.startswith("baseline_")
    )
    if not fluvial_weights and not coastal_weights:
        return None
    return RiskReportDriver(
        label="SEPA mapped flood background",
        value=f"Fluvial {fluvial_static:.0%} · Coastal {coastal_static:.0%} of each model",
        explanation="SEPA high-, medium- and low-likelihood river and coastal flood maps provide the static background before observations and forecast rainfall are added.",
        source="SEPA Flood Maps",
    )


def _rainfall_driver(
    *,
    label: str,
    observations: dict[str, Any],
    source: str,
    forecast: bool,
    explanation: str,
) -> RiskReportDriver | None:
    stations = observations.get("stations", [])
    if forecast:
        rates = [_number(station.get("rainfall_mm_per_hour")) for station in stations]
        observed_at = observations.get("retrieved_at")
    else:
        rates = [
            _rate(station.get("rainfall_mm"), station.get("accumulation_hours"))
            for station in stations
        ]
        observed_at = observations.get("retrieved_at")
    values = [value for value in rates if value is not None]
    if not values:
        return None
    bands = {_rainfall_band(value) for value in values}
    band = next(iter(bands)) if len(bands) == 1 else f"{_rainfall_band(min(values))} to {_rainfall_band(max(values))}"
    return RiskReportDriver(
        label=label,
        value=f"{min(values):.2f}–{max(values):.2f} mm/h · {band}",
        explanation=explanation,
        source=source,
        observed_at=observed_at,
    )


def _water_level_driver(observations: dict[str, Any]) -> RiskReportDriver | None:
    stations = observations.get("stations", [])
    thresholds = observations.get("risk_thresholds_m", [0.0, 1.0, 3.0, 5.0])
    levels = [_number(station.get("level_m")) for station in stations]
    values = [value for value in levels if value is not None]
    if not values:
        return None
    bands = [_water_level_band(value, thresholds) for value in values]
    counts = {band: bands.count(band) for band in ("low", "moderate", "high")}
    count_text = ", ".join(f"{count} {band}" for band, count in counts.items() if count)
    return RiskReportDriver(
        label="Current SEPA water levels",
        value=f"{len(values)} stations · {count_text}",
        explanation="Converted to the current fluvial water-level forcing; the future fluvial model carries this information through its current-state term.",
        source="SEPA river/tidal level API",
        observed_at=observations.get("retrieved_at"),
    )


def _metadata_for(hazard_class_path: Any) -> dict[str, Any]:
    if not hazard_class_path:
        return {}
    path = Path(str(hazard_class_path)).with_name("analysis_metadata.json")
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _rainfall_band(value: float) -> str:
    if value <= 0.05:
        return "dry"
    if value < 5.0:
        return "light rain"
    if value < 15.0:
        return "moderate rain"
    return "heavy rain"


def _water_level_band(value: float, thresholds: list[float]) -> str:
    if value < float(thresholds[1]):
        return "low"
    if value < float(thresholds[2]):
        return "moderate"
    return "high"


def _rate(value: Any, hours: Any) -> float | None:
    amount = _number(value)
    duration = _number(hours)
    if amount is None or duration is None or duration <= 0:
        return None
    return amount / duration


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _time_horizon(preferences: AssessmentPreferences, scenario: str) -> str:
    if scenario == "future":
        return f"Next {preferences.forecast_horizon_hours} hours"
    if scenario == "current":
        return "Current conditions"
    return "Historical scenario"


def _lens_label(value: str) -> str:
    return {
        "life_safety": "Life safety",
        "social_equity": "Social equity",
        "economic_protection": "Economic protection",
        "custom": "Custom",
    }.get(value, value.replace("_", " ").title())
