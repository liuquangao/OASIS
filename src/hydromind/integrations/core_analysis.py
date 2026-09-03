"""Safe asynchronous boundary around the deterministic Core Analyst tools."""

from __future__ import annotations

import asyncio
import csv
from dataclasses import asdict
from datetime import UTC, date, datetime
import hashlib
import httpx
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import numpy as np
import rasterio

from core_analyst.analysts.data_zone_assessment import run_data_zone_flood_priority_assessment
from core_analyst.historical_validation import (
    CedaCredentials,
    run_historical_flood_validation,
)
from core_analyst.data_registry import build_core_data_registry, write_core_data_registry
from core_analyst.data_sources import MemoizedDataSource
from core_analyst.workflows.generalized_analysis import plan_generalized_analysis
from core_analyst.workflows.extensible_hazard import run_extensible_hazard
from core_analyst.coastal_dynamic import (
    CoastalDynamicConfig,
    build_coastal_dynamic_evidence,
)
from core_analyst.real_data_inputs import prepare_real_exposure_vulnerability_inputs
from core_analyst.tools.agent_tools import (
    compare_priority_scenarios,
    compare_scenarios,
    run_hazard_analysis,
    run_hazard_scenarios,
    run_priority_analysis,
    run_sensitivity_analysis,
)
from core_analyst.utils.config import load_config
from core_analyst.utils.visualization import create_debug_visualization
from core_analyst.workflows.hydromind_real_data import (
    build_historical_hydrological_sources,
    build_hydromind_input_sources,
)
from core_analyst.workflows.multi_hazard import combine_hazard_maps

from hydromind.integrations.current_hazard import CoreAnalystCurrentHazard
from hydromind.models.analysis import (
    AnalysisMapLayer,
    AnalysisRunSummary,
    AnalysisWarning,
    DataReadinessItem,
    DataReadinessSummary,
    PriorityScenarioInput,
    PriorityUnitInput,
    PriorityWeights,
    GeneralizedAnalysisPlan,
    HazardExtensionSpec,
)
from hydromind.integrations.geoserver import GeoServerPublisher


_RUN_ID = re.compile(r"^[a-f0-9]{12}$")
_STATUSES = {
    "success",
    "success_with_warnings",
    "partial",
    "unavailable",
    "failed",
}


class CoreAnalystAnalysisService:
    """Persist full results while returning compact, typed Agent responses."""

    def __init__(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        config_dir: Path,
        current_hazard: CoreAnalystCurrentHazard,
        current_hazard_raster_path: Path,
        metoffice_sample_grid_size: int = 5,
        publisher: GeoServerPublisher | None = None,
        ceda_access_token: str | None = None,
        ceda_username: str | None = None,
        ceda_password: str | None = None,
        historical_ukv_path: Path | None = None,
    ) -> None:
        self._input_dir = input_dir
        self._output_dir = output_dir
        self._config_dir = config_dir
        self._current_hazard = current_hazard
        self._current_hazard_raster_path = current_hazard_raster_path
        self._metoffice_sample_grid_size = metoffice_sample_grid_size
        self._publisher = publisher
        self._ceda_credentials = CedaCredentials(
            access_token=ceda_access_token,
            username=ceda_username,
            password=ceda_password,
        )
        self._historical_ukv_path = historical_ukv_path
        self._memo: dict[str, AnalysisRunSummary] = {}

    @property
    def current_hazard(self) -> CoreAnalystCurrentHazard:
        return self._current_hazard

    async def data_readiness(self, category: str | None = None) -> DataReadinessSummary:
        records = await asyncio.to_thread(build_core_data_registry, self._input_dir)
        if category:
            records = [record for record in records if record.get("category") == category]
        counts: dict[str, int] = {}
        available: list[DataReadinessItem] = []
        incomplete: list[DataReadinessItem] = []
        for record in records:
            status = str(record.get("status", "unavailable"))
            counts[status] = counts.get(status, 0) + 1
            item = DataReadinessItem(
                dataset=str(record.get("dataset", "unknown")),
                category=str(record.get("category", "unknown")),
                status=status,
                reason=record.get("reason_if_unavailable"),
            )
            (available if status == "available" else incomplete).append(item)
        return DataReadinessSummary(
            status_counts=counts,
            available=available,
            incomplete=incomplete,
            guidance=[
                "Unavailable data is not replaced with zero or an unlabelled proxy.",
                "Exposure and vulnerability outputs may remain partial until verified local datasets are prepared.",
            ],
        )

    async def prepare_inputs(self) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        prepared = await asyncio.to_thread(
            prepare_real_exposure_vulnerability_inputs,
            self._input_dir,
            processed_dir=self._input_dir / "processed",
        )
        payload = asdict(prepared)
        warnings = [
            {"code": "dataset_unavailable", "message": item["reason"]}
            for item in prepared.unavailable
        ]
        raw = {
            "status": "partial" if warnings else "success",
            "summary": payload,
            "outputs": {"manifest": prepared.manifest},
            "provenance": {
                "tool": "prepare_real_exposure_vulnerability_inputs",
                "input_dir": str(self._input_dir),
            },
            "warnings": warnings,
        }
        return self._persist(run_id, "input_preparation", raw)

    async def run_hazard(
        self,
        *,
        hazard_type: str,
        scenario: str,
        use_live_data: bool,
        forecast_horizon: int = 6,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        if hazard_type == "pluvial" and scenario == "current" and use_live_data:
            snapshot = await self._current_hazard.refresh()
            raw = {
                "status": "success" if snapshot.available else "unavailable",
                "hazard_type": "pluvial",
                "scenario": "current",
                "summary": snapshot.model_dump(mode="json"),
                "outputs": {"hazard_class": str(self._current_hazard_raster_path)},
                "provenance": {
                    "tool": "CoreAnalystCurrentHazard.refresh",
                    "dynamic_source": "SEPA latest rainfall observations",
                },
                "warnings": snapshot.warnings,
            }
        else:
            raw = await asyncio.to_thread(
                run_hazard_analysis,
                area="glasgow",
                hazard_type=hazard_type,
                scenario=scenario,
                input_dir=self._input_dir,
                output_dir=self._output_dir / "hazard" / run_id,
                forecast_horizon=forecast_horizon,
                use_live_data=use_live_data,
            )
        return self._persist(run_id, f"hazard_{hazard_type}_{scenario}", raw)

    async def coastal_dynamic_evidence(
        self,
        *,
        historical_hours: int = 24,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            build_coastal_dynamic_evidence,
            CoastalDynamicConfig(
                input_dir=self._input_dir,
                historical_hours=historical_hours,
                search_radius_km=120.0,
                candidate_station_reference="E74039",
            ),
        )
        return self._persist(run_id, "coastal_dynamic_evidence", raw)

    async def run_priority(
        self,
        *,
        units: list[PriorityUnitInput],
        weights: PriorityWeights,
        scenario_name: str,
        top_n: int = 10,
    ) -> AnalysisRunSummary:
        memo_key = json.dumps(
            {
                "tool": "priority",
                "units": [unit.model_dump() for unit in units],
                "weights": weights.model_dump(),
                "scenario_name": scenario_name,
                "top_n": top_n,
            },
            sort_keys=True,
        )
        if memo_key in self._memo:
            return self._memo[memo_key]
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            run_priority_analysis,
            units=[unit.model_dump() for unit in units],
            scenario=scenario_name,
            weights=weights.model_dump(),
            output_dir=self._output_dir / "priority" / run_id,
            top_n=top_n,
            config=load_config(self._config_dir / "priority_analysis_config.yaml"),
        )
        geometry = {unit.id: unit.geometry for unit in units if unit.geometry}
        if geometry:
            features = []
            for item in raw.get("top_areas", raw.get("summary", {}).get("top_areas", [])):
                if item["id"] in geometry:
                    features.append({"type": "Feature", "geometry": geometry[item["id"]], "properties": item})
            priority_map = self._output_dir / "priority" / run_id / "priority.geojson"
            priority_map.parent.mkdir(parents=True, exist_ok=True)
            priority_map.write_text(
                json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
                encoding="utf-8",
            )
            raw.setdefault("outputs", {})["priority_map"] = str(priority_map)
        summary = self._persist(run_id, "priority", raw)
        self._memo[memo_key] = summary
        return summary

    async def compare_priority(
        self,
        *,
        units: list[PriorityUnitInput],
        scenarios: list[PriorityScenarioInput],
        top_n: int = 10,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            compare_priority_scenarios,
            units=[unit.model_dump() for unit in units],
            scenarios=[scenario.model_dump() for scenario in scenarios],
            output_dir=self._output_dir / "priority_comparison" / run_id,
            top_n=top_n,
            config=load_config(self._config_dir / "priority_analysis_config.yaml"),
        )
        return self._persist(run_id, "priority_comparison", raw)

    async def run_sensitivity(
        self,
        *,
        units: list[PriorityUnitInput],
        base_scenario: PriorityScenarioInput,
        vary_component: str,
        values: list[float],
        top_n: int = 10,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            run_sensitivity_analysis,
            units=[unit.model_dump() for unit in units],
            base_scenario=base_scenario.model_dump(),
            vary_component=vary_component,
            values=values,
            output_dir=self._output_dir / "sensitivity" / run_id,
            top_n=top_n,
            config=load_config(self._config_dir / "priority_analysis_config.yaml"),
        )
        return self._persist(run_id, "priority_sensitivity", raw)

    async def compare_runs(
        self,
        *,
        baseline_run_id: str,
        comparison_run_id: str,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            compare_scenarios,
            baseline_result=self._load(baseline_run_id),
            comparison_result=self._load(comparison_run_id),
            output_dir=self._output_dir / "scenario_comparison" / run_id,
        )
        return self._persist(run_id, "scenario_comparison", raw)

    async def nrfa_stations(self, dataset: str) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        source = build_historical_hydrological_sources(self._input_dir)[dataset]
        station_ids = await asyncio.to_thread(source.station_ids)
        raw = {
            "status": "success",
            "summary": {
                "dataset": dataset,
                "station_count": len(station_ids),
                "station_ids": station_ids,
                "analytical_position": source.analytical_position,
            },
            "outputs": {},
            "provenance": {"source": "National River Flow Archive"},
            "warnings": [],
        }
        return self._persist(run_id, "nrfa_stations", raw)

    async def nrfa_history(
        self,
        *,
        dataset: str,
        station_id: str,
        start_date: str | None,
        end_date: str | None,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        source = build_historical_hydrological_sources(self._input_dir)[dataset]
        series = await asyncio.to_thread(
            source.daily_values,
            station_id,
            start_date=start_date,
            end_date=end_date,
        )
        values = [row["value"] for row in series["values"] if row["value"] is not None]
        raw = {
            "status": "success",
            "summary": {
                "dataset": dataset,
                "station_id": station_id,
                "start_date": start_date,
                "end_date": end_date,
                "record_count": series["record_count"],
                "missing_value_count": series["missing_value_count"],
                "minimum": min(values) if values else None,
                "maximum": max(values) if values else None,
                "mean": sum(values) / len(values) if values else None,
            },
            "records": series["values"],
            "outputs": {},
            "provenance": series["provenance"],
            "warnings": [],
        }
        return self._persist(run_id, f"nrfa_history_{dataset}", raw)

    async def run_historical_validation(
        self,
        *,
        issue_time: datetime,
        forecast_horizon: int = 24,
        hazard_threshold: int = 2,
        priority_scenario: str = "social_equity",
    ) -> AnalysisRunSummary:
        """Run the no-leakage October 2023 forecast validation workflow."""

        run_id = self._new_run_id()
        if self._historical_ukv_path is None and not (
            self._ceda_credentials.access_token
            or (
                self._ceda_credentials.username
                and self._ceda_credentials.password
            )
        ):
            return self._persist(
                run_id,
                "historical_flood_validation",
                {
                    "status": "unavailable",
                    "summary": {
                        "issue_time": issue_time.isoformat(),
                        "reason": (
                            "CEDA archive access is not configured. Set CEDA_ACCESS_TOKEN, "
                            "CEDA_USERNAME/CEDA_PASSWORD, or HYDROMIND_HISTORICAL_UKV_PATH."
                        ),
                    },
                    "outputs": {},
                    "provenance": {"credentials_persisted": False},
                    "warnings": [
                        {
                            "code": "ceda_access_unavailable",
                            "message": "Historical UKV forecast input is unavailable.",
                        }
                    ],
                },
            )
        try:
            raw = await asyncio.to_thread(
                run_historical_flood_validation,
                input_dir=self._input_dir,
                config_dir=self._config_dir,
                output_dir=self._output_dir / "historical_validation" / run_id,
                issue_time=issue_time,
                horizon_hours=forecast_horizon,
                forecast_path=self._historical_ukv_path,
                credentials=self._ceda_credentials,
                hazard_threshold=hazard_threshold,
                priority_scenario=priority_scenario,
            )
        except (FileNotFoundError, httpx.HTTPError, ValueError) as exc:
            raw = {
                "status": "unavailable",
                "summary": {
                    "issue_time": issue_time.isoformat(),
                    "reason": str(exc),
                    "interpretation": (
                        "Historical validation did not substitute a later forecast or "
                        "unlabelled proxy."
                    ),
                },
                "outputs": {},
                "provenance": {"credentials_persisted": False},
                "warnings": [
                    {"code": "historical_input_unavailable", "message": str(exc)}
                ],
            }
        return self._persist(run_id, "historical_flood_validation", raw)

    async def generalized_plan(
        self,
        *,
        area: str,
        hazard_type: str,
        temporal_scope: str,
    ) -> GeneralizedAnalysisPlan:
        registry = await asyncio.to_thread(build_core_data_registry, self._input_dir)
        return GeneralizedAnalysisPlan.model_validate(
            plan_generalized_analysis(
                area=area,
                hazard_type=hazard_type,
                temporal_scope=temporal_scope,
                registry=registry,
            )
        )

    async def run_all_hazards(
        self,
        *,
        use_live_data: bool,
        forecast_horizon: int = 6,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        root = self._output_dir / "all_hazards" / run_id
        root.mkdir(parents=True, exist_ok=False)
        hazard_results: dict[str, dict[str, Any]] = {}
        rainfall_observation = None
        rainfall_forecast = None
        if use_live_data:
            rainfall_observation = MemoizedDataSource(build_hydromind_input_sources(
                self._input_dir,
                rainfall_source="sepa",
                sepa_station_numbers=["auto"],
            )["rainfall"])
            rainfall_forecast = MemoizedDataSource(build_hydromind_input_sources(
                self._input_dir,
                rainfall_source="metoffice-site",
                metoffice_horizon_hours=forecast_horizon,
                metoffice_sample_grid_size=self._metoffice_sample_grid_size,
            )["rainfall"])
        for hazard_type in ("pluvial", "fluvial", "coastal"):
            scenario_results = await asyncio.to_thread(
                run_hazard_scenarios,
                area="glasgow",
                hazard_type=hazard_type,
                input_dir=self._input_dir,
                output_dir=root / "hazard",
                forecast_horizon=forecast_horizon,
                use_live_data=use_live_data,
                rainfall_observation_source=rainfall_observation,
                rainfall_forecast_source=rainfall_forecast,
            )
            for scenario, result in scenario_results.items():
                hazard_results[f"{hazard_type}_{scenario}"] = result

        available = {
            key: result
            for key, result in hazard_results.items()
            if result.get("status") in {"success", "success_with_warnings"}
            and result.get("outputs", {}).get("hazard_class")
        }
        combined: dict[str, dict[str, Any]] = {}
        for scenario in ("current", "future"):
            scenario_results = {
                name: available[f"{name}_{scenario}"]
                for name in ("pluvial", "fluvial", "coastal")
                if f"{name}_{scenario}" in available
            }
            if len(scenario_results) == 3:
                combined[scenario] = await asyncio.to_thread(
                    combine_hazard_maps,
                    scenario_results,
                    root / "combined" / scenario,
                    scenario=scenario,
                )
        rasters = {
            key.replace("_", " ").title(): result["outputs"]["hazard_class"]
            for key, result in available.items()
        }
        rasters.update({
            f"Combined {scenario.title()}": combined[scenario]["outputs"]["hazard_class"]
            for scenario in combined
        })
        comparison = root / "all_hazards_classes.png" if rasters else None
        if comparison:
            await asyncio.to_thread(create_debug_visualization, rasters, comparison)
        registry = await asyncio.to_thread(build_core_data_registry, self._input_dir)
        historical = await asyncio.to_thread(build_historical_hydrological_sources, self._input_dir)
        registry_artifact = await asyncio.to_thread(
            write_core_data_registry,
            self._input_dir,
            root / "data_registry.json",
        )
        historical_artifact = root / "historical_sources.json"
        historical_artifact.write_text(
            json.dumps(
                {
                    name: {
                        "analytical_position": source.analytical_position,
                        "station_ids": source.station_ids() if source.zip_path.is_file() else [],
                        "status": "available" if source.zip_path.is_file() else "unavailable",
                    }
                    for name, source in historical.items()
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        failures = {
            key: result.get("summary", {}).get("error")
            or result.get("summary", {}).get("reason")
            or result.get("status", "unavailable")
            for key, result in hazard_results.items()
            if key not in available
        }
        status = "success" if not failures else "partial" if available else "unavailable"
        class_statistics = {
            key: self._hazard_class_statistics(value["outputs"]["hazard_class"])
            for key, value in available.items()
        }
        class_statistics.update({
            f"combined_{key}": self._hazard_class_statistics(value["outputs"]["hazard_class"])
            for key, value in combined.items()
        })
        outputs = {
            **{
                f"{key}_hazard_class": value["outputs"]["hazard_class"]
                for key, value in available.items()
                if key.endswith("_future")
            },
            **{f"combined_{key}_hazard_class": value["outputs"]["hazard_class"] for key, value in combined.items()},
        }
        if comparison:
            outputs["comparison_image"] = str(comparison)
        outputs["data_registry"] = registry_artifact["path"]
        outputs["historical_sources"] = str(historical_artifact)
        raw = {
            "status": status,
            "summary": {
                "available_hazards": list(available),
                "unavailable_hazards": failures,
                "combined_scenarios": list(combined),
                "class_statistics": class_statistics,
                "data_registry_status_counts": {
                    status: sum(item.get("status") == status for item in registry)
                    for status in sorted({str(item.get("status")) for item in registry})
                },
                "historical_sources": sorted(historical),
            },
            "hazard_results": hazard_results,
            "combined_results": combined,
            "outputs": outputs,
            "provenance": {"tool": "run_all_hazards", "use_live_data": use_live_data},
            "warnings": [warning for result in hazard_results.values() for warning in result.get("warnings", [])],
        }
        summary_path = root / "all_hazards_summary.json"
        summary_path.write_text(json.dumps(raw, indent=2, default=self._json_default), encoding="utf-8")
        raw["outputs"]["summary_report"] = str(summary_path)
        return self._persist(run_id, "all_hazards", raw)

    async def run_flood_priority_assessment(
        self,
        *,
        scenario: str = "future",
        use_live_data: bool = True,
        forecast_horizon: int = 24,
        hazard_threshold: int = 2,
        priority_scenario: str = "social_equity",
        all_hazards_run_id: str | None = None,
    ) -> AnalysisRunSummary:
        """Run one deterministic Data Zone assessment from one all-hazards run."""

        if all_hazards_run_id:
            hazards = self._load(all_hazards_run_id)
            if hazards.get("analysis_type") != "all_hazards":
                raise ValueError("all_hazards_run_id does not reference an all-hazards run")
        else:
            hazard_summary = await self.run_all_hazards(
                use_live_data=use_live_data,
                forecast_horizon=forecast_horizon,
            )
            all_hazards_run_id = hazard_summary.run_id
            hazards = self._load(all_hazards_run_id)
        hazard_path = hazards.get("outputs", {}).get(f"combined_{scenario}_hazard_class")
        if not hazard_path:
            run_id = self._new_run_id()
            reason = (
                f"All-hazards run {all_hazards_run_id} has no combined {scenario} "
                "hazard raster because one or more component hazards are unavailable."
            )
            return self._persist(
                run_id,
                "flood_priority_assessment",
                {
                    "status": "unavailable",
                    "summary": {
                        "assessment_type": "data_zone_flood_priority",
                        "scenario": scenario,
                        "reason": reason,
                        "all_hazards_run_id": all_hazards_run_id,
                        "available_hazards": hazards.get("summary", {}).get("available_hazards", []),
                        "unavailable_hazards": hazards.get("summary", {}).get("unavailable_hazards", {}),
                    },
                    "outputs": {},
                    "provenance": {
                        "all_hazards_run_id": all_hazards_run_id,
                        "use_live_data": use_live_data,
                        "forecast_horizon_hours": forecast_horizon,
                    },
                    "warnings": [
                        {
                            "code": "combined_hazard_unavailable",
                            "message": reason,
                        }
                    ],
                },
            )

        prepared = await asyncio.to_thread(
            prepare_real_exposure_vulnerability_inputs,
            self._input_dir,
            processed_dir=self._input_dir / "processed",
        )
        if not prepared.data_zone_geography:
            raise ValueError("Prepared Census 2022 Data Zone geography is unavailable")
        run_id = self._new_run_id()
        raw = await asyncio.to_thread(
            run_data_zone_flood_priority_assessment,
            hazard_raster=hazard_path,
            data_zones=prepared.data_zone_geography,
            buildings=prepared.buildings,
            critical_services=prepared.critical_services,
            output_dir=self._output_dir / "priority_assessment" / run_id,
            scenario=scenario,
            hazard_threshold=hazard_threshold,
            priority_scenario=priority_scenario,
            provenance={
                "all_hazards_run_id": all_hazards_run_id,
                "use_live_data": use_live_data,
                "forecast_horizon_hours": forecast_horizon,
                "prepared_inputs_manifest": prepared.manifest,
            },
        )
        return self._persist(run_id, "flood_priority_assessment", raw)

    async def rerank_flood_priority(
        self,
        *,
        source_run_id: str,
        weights: PriorityWeights,
        include_simd: bool = True,
        scenario_name: str = "custom",
    ) -> AnalysisRunSummary:
        """Re-rank persisted Data Zone components without re-running hazards."""

        source = self._load(source_run_id)
        if source.get("analysis_type") == "priority_rerank":
            source_run_id = str(source["provenance"]["source_run_id"])
            source = self._load(source_run_id)
        if source.get("analysis_type") != "flood_priority_assessment":
            raise ValueError("source_run_id must reference a flood priority assessment")

        outputs = source.get("outputs", {})
        csv_path = Path(outputs["data_zone_assessment"])
        geojson_path = Path(outputs["priority_by_data_zone"])
        sensitivity_path = Path(outputs["sensitivity"])
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        without_simd = {
            str(item["id"]): item.get("vulnerability_without_simd")
            for item in json.loads(sensitivity_path.read_text(encoding="utf-8"))[
                "simd_inclusion"
            ]
        }
        base_scenario = str(source.get("summary", {}).get("priority_scenario", "social_equity"))
        scored: list[dict[str, Any]] = []
        for row in rows:
            vulnerability = (
                self._optional_float(row.get("vulnerability_score"))
                if include_simd
                else self._optional_float(without_simd.get(str(row["id"])))
            )
            hazard = self._optional_float(row.get("hazard_score"))
            exposure = self._optional_float(row.get("exposure_score"))
            score = None
            if hazard is not None and exposure is not None and vulnerability is not None:
                score = (
                    hazard * weights.hazard
                    + exposure * weights.exposure
                    + vulnerability * weights.vulnerability
                )
            scored.append(
                {
                    **row,
                    "hazard_score": hazard,
                    "exposure_score": exposure,
                    "vulnerability_score": vulnerability,
                    "priority_score": score,
                    "base_rank": self._optional_int(row.get(f"rank_{base_scenario}"))
                    or self._optional_int(row.get("priority_rank")),
                }
            )
        ranked = sorted(
            (row for row in scored if row["priority_score"] is not None),
            key=lambda row: (-row["priority_score"], str(row["id"])),
        )
        for rank, row in enumerate(ranked, 1):
            row["priority_rank"] = rank
            row["rank_change"] = (
                None if row["base_rank"] is None else row["base_rank"] - rank
            )

        run_id = self._new_run_id()
        root = self._output_dir / "priority_rerank" / run_id
        root.mkdir(parents=True, exist_ok=False)
        payload = json.loads(geojson_path.read_text(encoding="utf-8"))
        by_id = {str(row["id"]): row for row in scored}
        for feature in payload.get("features", []):
            unit = by_id[str(feature.get("properties", {}).get("id"))]
            feature["properties"].update(
                {
                    "priority_score": unit["priority_score"],
                    "priority_rank": unit.get("priority_rank"),
                    "priority_scenario": scenario_name,
                    "hazard_score": unit["hazard_score"],
                    "exposure_score": unit["exposure_score"],
                    "vulnerability_score": unit["vulnerability_score"],
                    "include_simd": include_simd,
                    "rank_change": unit.get("rank_change"),
                }
            )
        map_path = root / "priority_by_data_zone.geojson"
        map_path.write_text(json.dumps(payload), encoding="utf-8")
        summary = {
            "assessment_type": "data_zone_priority_rerank",
            "source_run_id": source_run_id,
            "priority_scenario": scenario_name,
            "weights": weights.model_dump(),
            "include_simd": include_simd,
            "data_zone_count": len(scored),
            "complete_priority_count": len(ranked),
            "top_areas": [
                {
                    "id": row["id"],
                    "name": row.get("name"),
                    "priority_score": row["priority_score"],
                    "rank": row["priority_rank"],
                    "rank_change": row.get("rank_change"),
                    "hazard_score": row["hazard_score"],
                    "exposure_score": row["exposure_score"],
                    "vulnerability_score": row["vulnerability_score"],
                }
                for row in ranked[:10]
            ],
            "interpretation": (
                "Priority is a relative value-dependent ranking. This re-rank reused "
                "persisted deterministic components and made no weather API calls."
            ),
        }
        summary_path = root / "rerank_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return self._persist(
            run_id,
            "priority_rerank",
            {
                "status": "success" if len(ranked) == len(scored) else "partial",
                "summary": summary,
                "outputs": {
                    "priority_by_data_zone": str(map_path),
                    "rerank_summary": str(summary_path),
                },
                "provenance": {
                    "source_run_id": source_run_id,
                    "weights": weights.model_dump(),
                    "include_simd": include_simd,
                    "external_api_calls": 0,
                },
                "warnings": [],
            },
        )

    async def validate_flood_priority_run(
        self,
        run_id: str,
        *,
        expected_data_zone_count: int | None = None,
        analysis_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Validate persisted spatial evidence before a report is presented."""

        result = self._load(run_id)
        source = result
        if result.get("analysis_type") == "priority_rerank":
            source = self._load(str(result["provenance"]["source_run_id"]))
        outputs = source.get("outputs", {})
        checks: list[dict[str, Any]] = []

        required = (
            "hazard_by_data_zone",
            "exposure_by_data_zone",
            "vulnerability_by_data_zone",
            "priority_by_data_zone",
            "data_zone_assessment",
        )
        missing = [key for key in required if not Path(outputs.get(key, "")).is_file()]
        checks.append(self._quality_check(
            "required_artifacts",
            not missing,
            "All required deterministic artifacts exist." if not missing else "Missing artifacts: " + ", ".join(missing),
            {"missing": missing},
        ))
        if missing:
            return self._write_quality_report(run_id, checks, analysis_time)

        with Path(outputs["data_zone_assessment"]).open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        expected = expected_data_zone_count or int(
            source.get("summary", {}).get("data_zone_count", len(rows))
        )
        checks.append(self._quality_check(
            "data_zone_count",
            len(rows) == expected,
            f"Found {len(rows):,} Data Zones; expected {expected:,}.",
            {"actual": len(rows), "expected": expected},
        ))

        geojson_counts: dict[str, int] = {}
        invalid_coordinates = 0
        for key in required[:4]:
            payload = json.loads(Path(outputs[key]).read_text(encoding="utf-8"))
            features = payload.get("features", [])
            geojson_counts[key] = len(features)
            for feature in features:
                bounds = self._coordinate_bounds(feature.get("geometry"))
                if bounds and not (
                    -180 <= bounds[0] <= 180
                    and -90 <= bounds[1] <= 90
                    and -180 <= bounds[2] <= 180
                    and -90 <= bounds[3] <= 90
                ):
                    invalid_coordinates += 1
        checks.append(self._quality_check(
            "geojson_alignment",
            all(count == len(rows) for count in geojson_counts.values()) and not invalid_coordinates,
            "All decision GeoJSON layers use plausible EPSG:4326 coordinates and cover the same Data Zones.",
            {"feature_counts": geojson_counts, "invalid_coordinate_features": invalid_coordinates},
        ))

        score_fields = ("hazard_score", "exposure_score", "vulnerability_score")
        bad_scores = []
        for row in rows:
            for field in score_fields:
                value = self._optional_float(row.get(field))
                if value is not None and not 0 <= value <= 1:
                    bad_scores.append({"id": row.get("id"), "field": field, "value": value})
        checks.append(self._quality_check(
            "score_ranges",
            not bad_scores,
            "All available component scores are within 0–1." if not bad_scores else "One or more component scores fall outside 0–1.",
            {"invalid_count": len(bad_scores), "examples": bad_scores[:5]},
        ))

        ranks = [
            self._optional_int(row.get("priority_rank"))
            for row in rows
            if self._optional_int(row.get("priority_rank")) is not None
        ]
        checks.append(self._quality_check(
            "ranking_consistency",
            sorted(ranks) == list(range(1, len(ranks) + 1)),
            "Priority ranks are unique and contiguous." if ranks else "No complete priority ranks were produced.",
            {"rank_count": len(ranks), "unique_rank_count": len(set(ranks))},
        ))

        complete = int(source.get("summary", {}).get("complete_priority_count", 0))
        checks.append({
            "code": "priority_completeness",
            "status": "pass" if complete == len(rows) else "warning" if complete else "fail",
            "message": f"{complete:,} of {len(rows):,} Data Zones have complete priority scores.",
            "evidence": {"complete": complete, "total": len(rows)},
        })

        hazard_publication: dict[str, Any] | None = None
        hazard_outputs: dict[str, Any] = {}
        all_hazards_id = source.get("provenance", {}).get("all_hazards_run_id")
        if all_hazards_id:
            hazards = self._load(str(all_hazards_id))
            hazard_publication = hazards.get("publication", {})
            hazard_outputs = hazards.get("outputs", {})
            raster_paths = [
                Path(value)
                for key, value in hazards.get("outputs", {}).items()
                if key.endswith("hazard_class") and isinstance(value, str) and Path(value).is_file()
            ]
            grids = []
            for path in raster_paths:
                with rasterio.open(path) as dataset:
                    grids.append(
                        {
                            "path": str(path),
                            "crs": str(dataset.crs),
                            "shape": [dataset.height, dataset.width],
                            "transform": list(dataset.transform)[:6],
                            "resolution": list(dataset.res),
                        }
                    )
            aligned = not grids or all(
                item["crs"] == grids[0]["crs"]
                and item["shape"] == grids[0]["shape"]
                and item["transform"] == grids[0]["transform"]
                for item in grids[1:]
            )
            checks.append(self._quality_check(
                "hazard_raster_alignment",
                aligned,
                "All available hazard rasters share CRS, extent, resolution and transform.",
                {"rasters": grids},
            ))
            scenario = str(source.get("summary", {}).get("scenario", "future"))
            statistics = hazards.get("summary", {}).get("class_statistics", {}).get(
                f"combined_{scenario}"
            )
            if statistics:
                medium = float(statistics["classes"]["medium"]["percent_of_classified_area"])
                high = float(statistics["classes"]["high"]["percent_of_classified_area"])
                dominated = medium >= 90 and high < 1
                checks.append({
                    "code": "hazard_distribution",
                    "status": "warning" if dominated else "pass",
                    "message": (
                        f"Combined {scenario} hazard is {medium:.2f}% Medium and {high:.2f}% High."
                        + (" This unusually dominant Medium distribution requires review." if dominated else "")
                    ),
                    "evidence": statistics,
                })

        cutoff = analysis_time or datetime.now(UTC)
        future_sources = self._future_source_times(source.get("provenance", {}), cutoff)
        checks.append(self._quality_check(
            "source_time_cutoff",
            not future_sources,
            "All auditable source timestamps are no later than the analysis time.",
            {"analysis_time": cutoff.isoformat(), "future_sources": future_sources},
        ))

        publications = [(source.get("publication", {}), outputs)]
        if hazard_publication is not None:
            publications.append((hazard_publication, hazard_outputs))
        recorded_count = 0
        changed = {}
        for publication, publication_outputs in publications:
            recorded = publication.get("source_checksums", {})
            recorded_count += len(recorded)
            for key, digest in recorded.items():
                path = Path(publication_outputs.get(key, ""))
                if path.is_file() and self._sha256(path) != digest:
                    changed[key] = {"recorded": digest, "current": self._sha256(path)}
        checks.append(self._quality_check(
            "publication_checksum",
            bool(recorded_count) and not changed,
            "Published-layer source checksums match the current local artifacts."
            if recorded_count and not changed else "Published-layer checksum evidence is missing or inconsistent.",
            {"recorded_count": recorded_count, "changed": changed},
        ))
        wms_layers = [
            item["layer_name"]
            for publication, _ in publications
            for item in publication.get("map_layers", [])
            if item.get("kind") == "wms" and item.get("layer_name")
        ]
        if wms_layers and self._publisher:
            try:
                missing_layers = [
                    layer for layer in wms_layers
                    if not await asyncio.to_thread(self._publisher.layer_exists, layer)
                ]
                checks.append(self._quality_check(
                    "geoserver_layer_presence",
                    not missing_layers,
                    "All GeoServer layers associated with the run are present.",
                    {"missing_layers": missing_layers},
                ))
            except httpx.HTTPError as exc:
                checks.append({
                    "code": "geoserver_layer_presence",
                    "status": "warning",
                    "message": "GeoServer publication could not be verified during validation.",
                    "evidence": {"error": str(exc)},
                })

        checks.append({
            "code": "artifact_checksums",
            "status": "pass",
            "message": "SHA-256 checksums recorded for all assessment artifacts.",
            "evidence": {
                key: self._sha256(Path(value))
                for key, value in outputs.items()
                if isinstance(value, str) and Path(value).is_file()
            },
        })
        return self._write_quality_report(run_id, checks, analysis_time)

    def attach_run_artifacts(self, run_id: str, artifacts: dict[str, str]) -> None:
        """Add audit artifacts to an existing persisted run."""

        path = self._run_dir(run_id) / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("outputs", {}).update(artifacts)
        path.write_text(json.dumps(payload, indent=2, default=self._json_default), encoding="utf-8")

    @staticmethod
    def _hazard_class_statistics(path: str | Path) -> dict[str, Any]:
        """Summarise class area without turning a mixed city into one risk label."""

        with rasterio.open(path) as dataset:
            values = dataset.read(1)
            valid = dataset.read_masks(1) > 0
            pixel_area_km2 = abs(
                dataset.transform.a * dataset.transform.e
                - dataset.transform.b * dataset.transform.d
            ) / 1_000_000

        labels = {1: "low", 2: "medium", 3: "high"}
        counts = {
            class_value: int(np.count_nonzero(valid & (values == class_value)))
            for class_value in labels
        }
        classified_pixels = sum(counts.values())
        present = [class_value for class_value, count in counts.items() if count]
        dominant = max(present, key=lambda value: (counts[value], value)) if present else None
        highest = max(present) if present else None
        return {
            "classified_pixels": classified_pixels,
            "classified_area_km2": round(classified_pixels * pixel_area_km2, 6),
            "dominant_class": labels.get(dominant),
            "highest_class_present": labels.get(highest),
            "classes": {
                label: {
                    "class_value": class_value,
                    "pixel_count": counts[class_value],
                    "area_km2": round(counts[class_value] * pixel_area_km2, 6),
                    "percent_of_classified_area": round(
                        100 * counts[class_value] / classified_pixels, 2
                    )
                    if classified_pixels
                    else 0.0,
                }
                for class_value, label in labels.items()
            },
        }

    def _write_quality_report(
        self,
        run_id: str,
        checks: list[dict[str, Any]],
        analysis_time: datetime | None,
    ) -> dict[str, Any]:
        status = "fail" if any(item["status"] == "fail" for item in checks) else (
            "warning" if any(item["status"] == "warning" for item in checks) else "pass"
        )
        report = {
            "run_id": run_id,
            "status": status,
            "analysis_time": (analysis_time or datetime.now(UTC)).isoformat(),
            "checks": checks,
        }
        path = self._run_dir(run_id) / "quality_report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.attach_run_artifacts(run_id, {"quality_report": str(path)})
        return report

    @staticmethod
    def _quality_check(
        code: str,
        passed: bool,
        message: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "code": code,
            "status": "pass" if passed else "fail",
            "message": message,
            "evidence": evidence,
        }

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in {None, ""}:
            return None
        return float(value)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in {None, ""}:
            return None
        return int(float(value))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def _coordinate_bounds(cls, geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
        if not geometry:
            return None
        coordinates = geometry.get("coordinates", [])
        points: list[tuple[float, float]] = []

        def visit(value: Any) -> None:
            if isinstance(value, list) and len(value) >= 2 and all(
                isinstance(item, (int, float)) for item in value[:2]
            ):
                points.append((float(value[0]), float(value[1])))
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(coordinates)
        if not points:
            return None
        xs, ys = zip(*points)
        return min(xs), min(ys), max(xs), max(ys)

    @classmethod
    def _future_source_times(
        cls,
        payload: Any,
        cutoff: datetime,
        path: str = "provenance",
    ) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                found.extend(cls._future_source_times(value, cutoff, f"{path}.{key}"))
        elif isinstance(payload, list):
            for index, value in enumerate(payload):
                found.extend(cls._future_source_times(value, cutoff, f"{path}[{index}]"))
        elif isinstance(payload, str) and any(
            token in path.lower() for token in ("time", "observed_at", "retrieved_at")
        ):
            try:
                parsed = datetime.fromisoformat(payload.replace("Z", "+00:00"))
                parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
                if parsed > cutoff:
                    found.append({"field": path, "value": payload})
            except ValueError:
                pass
        return found

    async def register_extension(self, spec: HazardExtensionSpec) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        registry = self._output_dir / "extensions"
        registry.mkdir(parents=True, exist_ok=True)
        path = registry / f"{spec.hazard_type}.json"
        path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        return self._persist(run_id, "framework_extension", {
            "status": "success",
            "summary": spec.model_dump(),
            "outputs": {"extension_spec": str(path)},
            "provenance": {"tool": "register_extension"},
            "warnings": [],
        })

    async def run_extension(
        self,
        *,
        hazard_type: str,
        area: str,
        factor_paths: dict[str, str],
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        spec = json.loads((self._output_dir / "extensions" / f"{hazard_type}.json").read_text(encoding="utf-8"))
        root = self._input_dir.resolve()
        resolved_paths = {}
        for name, value in factor_paths.items():
            path = Path(value)
            path = (root / path).resolve() if not path.is_absolute() else path.resolve()
            if root not in path.parents:
                raise ValueError("Extension factor rasters must be stored under the Core Analyst Input directory.")
            resolved_paths[name] = str(path)
        raw = await asyncio.to_thread(
            run_extensible_hazard,
            spec,
            resolved_paths,
            self._output_dir / "extended_hazard" / run_id,
            area,
        )
        return self._persist(run_id, f"hazard_{hazard_type}_custom", raw)

    def _persist(
        self,
        run_id: str,
        analysis_type: str,
        result: dict[str, Any],
    ) -> AnalysisRunSummary:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        stored = {**result, "run_id": run_id, "analysis_type": analysis_type}
        status = str(result.get("status", "failed"))
        if status not in _STATUSES:
            status = "success" if status == "available" else "failed"
        map_layers = self._map_layers(run_id, analysis_type, result.get("outputs", {}))
        source_checksums = {
            key: self._sha256(Path(value))
            for key, value in result.get("outputs", {}).items()
            if isinstance(value, str) and Path(value).is_file()
            and Path(value).suffix.lower() in {".tif", ".tiff", ".geojson"}
        }
        stored["publication"] = {
            "map_layers": [layer.model_dump(mode="json") for layer in map_layers],
            "source_checksums": source_checksums,
        }
        (run_dir / "result.json").write_text(
            json.dumps(stored, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        return AnalysisRunSummary(
            run_id=run_id,
            analysis_type=analysis_type,
            status=status,
            summary=self._compact_summary(result),
            output_keys=sorted(
                key for key, value in result.get("outputs", {}).items() if value
            ),
            map_layers=map_layers,
            warnings=self._warnings(result.get("warnings", [])),
            requires_human_review=True,
        )

    def _map_layers(
        self,
        run_id: str,
        analysis_type: str,
        outputs: dict[str, Any],
    ) -> list[AnalysisMapLayer]:
        layers: list[AnalysisMapLayer] = []
        for key, value in outputs.items():
            if not isinstance(value, str):
                continue
            path = Path(value)
            label = f"{analysis_type.replace('_', ' ').title()} · {key.replace('_', ' ').title()}"
            if path.suffix.lower() in {".tif", ".tiff"} and self._publisher:
                if "hazard_class" not in key and not key.endswith("_class"):
                    continue
                layers.append(
                    self._publisher.publish_raster(
                        path,
                        name=f"{analysis_type}_{run_id}_{key}",
                        label=label,
                    )
                )
            elif path.suffix.lower() == ".geojson":
                visible = not (
                    analysis_type == "flood_priority_assessment"
                    and key != "priority_by_data_zone"
                )
                style = key.removesuffix("_by_data_zone")
                layers.append(
                    AnalysisMapLayer(
                        id=f"geojson-{run_id}-{key}",
                        label=label,
                        kind="geojson",
                        url=f"http://127.0.0.1:8000/analysis/runs/{run_id}/artifacts/{key}",
                        style=style,
                        visible=visible,
                    )
                )
        return layers

    def _load(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "result.json"
        if not path.is_file():
            raise ValueError(f"Unknown Core Analyst run id: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _run_dir(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid Core Analyst run id.")
        root = (self._output_dir / "runs").resolve()
        path = (root / run_id).resolve()
        if path.parent != root:
            raise ValueError("Invalid Core Analyst run path.")
        return path

    def _new_run_id(self) -> str:
        return uuid4().hex[:12]

    def _compact_summary(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = dict(result.get("summary", {}))
        for large_key in ("ranked_units", "rankings", "units", "records"):
            value = summary.pop(large_key, None)
            if isinstance(value, list):
                summary[f"{large_key}_count"] = len(value)
        if result.get("top_areas") is not None:
            summary["top_areas"] = result.get("top_areas", [])[:10]
        if result.get("rank_comparison") is not None:
            summary["rank_comparison"] = result.get("rank_comparison", [])[:20]
        if not summary and result.get("metadata"):
            metadata = result["metadata"]
            summary = {
                key: metadata[key]
                for key in ("analysis_method", "scenario", "hazard_type")
                if key in metadata
            }
        return self._agent_safe(summary)

    def _agent_safe(self, value: Any) -> Any:
        """Remove server paths and bound large collections in tool responses."""

        if isinstance(value, dict):
            return {
                str(key): self._agent_safe(item)
                for key, item in list(value.items())[:50]
            }
        if isinstance(value, list):
            return [self._agent_safe(item) for item in value[:20]]
        if isinstance(value, str):
            normalized = value.replace("\\", "/")
            suffixes = (".tif", ".tiff", ".json", ".csv", ".geojson", ".shp", ".gpkg", ".xlsx", ".xls")
            if "/" in normalized and normalized.lower().endswith(suffixes):
                return Path(normalized).name
        return value

    def _warnings(self, values: list[Any]) -> list[AnalysisWarning]:
        warnings: list[AnalysisWarning] = []
        for value in values:
            if isinstance(value, dict):
                warnings.append(
                    AnalysisWarning(
                        code=str(value.get("code", "analysis_warning")),
                        message=str(value.get("message", value)),
                    )
                )
            else:
                warnings.append(AnalysisWarning(message=str(value)))
        return warnings

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
