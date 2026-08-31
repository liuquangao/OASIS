"""Safe asynchronous boundary around the deterministic Core Analyst tools."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import numpy as np

from core_analyst.data_registry import build_core_data_registry
from core_analyst.workflows.generalized_analysis import plan_generalized_analysis
from core_analyst.workflows.extensible_hazard import run_extensible_hazard
from core_analyst.coastal_dynamic import (
    CoastalDynamicConfig,
    build_coastal_dynamic_evidence,
)
from core_analyst.real_data_inputs import (
    build_real_exposure_sources,
    build_real_vulnerability_sources,
    prepare_real_exposure_vulnerability_inputs,
)
from core_analyst.tools.agent_tools import (
    compare_priority_scenarios,
    compare_scenarios,
    run_exposure_analysis,
    run_hazard_analysis,
    run_priority_analysis,
    run_sensitivity_analysis,
    run_vulnerability_analysis,
)
from core_analyst.utils.config import load_config
from core_analyst.utils.visualization import create_debug_visualization
from core_analyst.workflows.oasis_real_data import build_historical_hydrological_sources
from core_analyst.workflows.multi_hazard import combine_hazard_maps

from oasis.integrations.current_hazard import CoreAnalystCurrentHazard
from oasis.models.analysis import (
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
from oasis.integrations.geoserver import GeoServerPublisher


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
        publisher: GeoServerPublisher | None = None,
    ) -> None:
        self._input_dir = input_dir
        self._output_dir = output_dir
        self._config_dir = config_dir
        self._current_hazard = current_hazard
        self._current_hazard_raster_path = current_hazard_raster_path
        self._publisher = publisher
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

    async def run_exposure(
        self,
        *,
        hazard_run_id: str,
        exposure_types: list[str] | None = None,
        hazard_threshold: int = 2,
    ) -> AnalysisRunSummary:
        hazard = self._load(hazard_run_id)
        run_id = self._new_run_id()
        sources = await asyncio.to_thread(self._exposure_sources)
        config = load_config(self._config_dir / "exposure_analysis_config.yaml")
        raw = await asyncio.to_thread(
            run_exposure_analysis,
            hazard_result=hazard,
            exposure_sources=sources,
            exposure_types=exposure_types,
            area="glasgow",
            output_dir=self._output_dir / "exposure" / run_id,
            hazard_threshold=hazard_threshold,
            config=config,
        )
        return self._persist(run_id, "exposure", raw)

    async def combine_hazards(
        self,
        *,
        pluvial_run_id: str,
        fluvial_run_id: str,
        coastal_run_id: str,
        scenario: str,
        exposure_threshold: int = 2,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        runs = {
            "pluvial": self._load(pluvial_run_id),
            "fluvial": self._load(fluvial_run_id),
            "coastal": self._load(coastal_run_id),
        }
        for hazard_type, result in runs.items():
            if result.get("hazard_type") != hazard_type:
                raise ValueError(f"{hazard_type}_run_id references the wrong hazard type")
            if result.get("status") not in {"success", "success_with_warnings", "partial"}:
                raise ValueError(f"{hazard_type} run is not usable")
        raw = await asyncio.to_thread(
            combine_hazard_maps,
            runs,
            self._output_dir / "combined" / run_id,
            scenario=scenario,
            exposure_threshold=exposure_threshold,
        )
        return self._persist(run_id, f"hazard_combined_{scenario}", raw)

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

    async def run_vulnerability(
        self,
        *,
        scenario: str = "current",
        dimensions: list[str] | None = None,
    ) -> AnalysisRunSummary:
        run_id = self._new_run_id()
        geography, sources = await asyncio.to_thread(self._vulnerability_sources)
        config = load_config(self._config_dir / "vulnerability_analysis_config.yaml")
        raw = await asyncio.to_thread(
            run_vulnerability_analysis,
            area="glasgow",
            geography_source=geography,
            vulnerability_sources=sources,
            vulnerability_dimensions=dimensions,
            output_dir=self._output_dir / "vulnerability" / run_id,
            scenario=scenario,
            config=config,
        )
        return self._persist(run_id, f"vulnerability_{scenario}", raw)

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
        for hazard_type in ("pluvial", "fluvial", "coastal"):
            for scenario in ("current", "future"):
                result = await asyncio.to_thread(
                    run_hazard_analysis,
                    area="glasgow",
                    hazard_type=hazard_type,
                    scenario=scenario,
                    input_dir=self._input_dir,
                    output_dir=root / "hazard",
                    forecast_horizon=forecast_horizon,
                    use_live_data=use_live_data,
                )
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
        failures = {
            key: result.get("summary", {}).get("error")
            or result.get("summary", {}).get("reason")
            or result.get("status", "unavailable")
            for key, result in hazard_results.items()
            if key not in available
        }
        status = "success" if not failures else "partial" if available else "unavailable"
        outputs = {
            **{f"{key}_hazard_class": value["outputs"]["hazard_class"] for key, value in available.items()},
            **{f"combined_{key}_hazard_class": value["outputs"]["hazard_class"] for key, value in combined.items()},
        }
        if comparison:
            outputs["comparison_image"] = str(comparison)
        raw = {
            "status": status,
            "summary": {
                "available_hazards": list(available),
                "unavailable_hazards": failures,
                "combined_scenarios": list(combined),
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

    def _prepared_payload(self) -> dict[str, Any]:
        manifest = (
            self._input_dir
            / "processed"
            / "real_exposure_vulnerability_inputs_manifest.json"
        )
        if not manifest.is_file():
            return {}
        return json.loads(manifest.read_text(encoding="utf-8")).get("prepared", {})

    def _exposure_sources(self) -> dict[str, Any]:
        sources = build_real_exposure_sources(self._prepared_payload())
        records = {record["dataset"]: record for record in build_core_data_registry(self._input_dir)}
        for key in ("buildings", "critical_infrastructure"):
            record = records.get(key, {})
            if record.get("local_path"):
                sources[key] = record["local_path"]
        return sources

    def _vulnerability_sources(self) -> tuple[str | None, dict[str, Any]]:
        geography, sources = build_real_vulnerability_sources(self._prepared_payload())
        records = {record["dataset"]: record for record in build_core_data_registry(self._input_dir)}
        for key in ("socioeconomic", "critical_services"):
            record = records.get(key, {})
            path = str(record.get("local_path", ""))
            if record.get("status") == "available" and Path(path).suffix.lower() in {".geojson", ".json"}:
                sources[key] = record["local_path"]
        return geography, sources

    def _persist(
        self,
        run_id: str,
        analysis_type: str,
        result: dict[str, Any],
    ) -> AnalysisRunSummary:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        stored = {**result, "run_id": run_id, "analysis_type": analysis_type}
        (run_dir / "result.json").write_text(
            json.dumps(stored, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        status = str(result.get("status", "failed"))
        if status not in _STATUSES:
            status = "success" if status == "available" else "failed"
        map_layers = self._map_layers(run_id, analysis_type, result.get("outputs", {}))
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
                layers.append(
                    self._publisher.publish_raster(
                        path,
                        name=f"{analysis_type}_{run_id}_{key}",
                        label=label,
                    )
                )
            elif path.suffix.lower() == ".geojson":
                layers.append(
                    AnalysisMapLayer(
                        id=f"geojson-{run_id}-{key}",
                        label=label,
                        kind="geojson",
                        url=f"http://127.0.0.1:8000/analysis/runs/{run_id}/artifacts/{key}",
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
