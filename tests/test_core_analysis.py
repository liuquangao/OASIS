from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from core_analyst.analysts.exposure_analysis import (
    InMemoryVectorSource,
    run_exposure_analysis,
)
from core_analyst.analysts.vulnerability_analysis import run_vulnerability_analysis
from core_analyst.data_sources import RasterGrid
from core_analyst.tools.agent_tools import ToolInputError, run_priority_analysis
from core_analyst.workflows.multi_hazard import combine_hazard_maps
from oasis.agent import spatial_agent
from oasis.integrations.core_analysis import CoreAnalystAnalysisService
from oasis.models.analysis import ExtensionFactor, HazardExtensionSpec, PriorityScenarioInput, PriorityUnitInput, PriorityWeights
from oasis.models.current_hazard import CurrentHazardSnapshot


class FakeCurrentHazard:
    async def refresh(self) -> CurrentHazardSnapshot:
        return CurrentHazardSnapshot(
            available=True,
            generated_at="2026-08-27T22:00:00Z",
            observation_start="2026-08-27T21:00:00Z",
            observation_end="2026-08-27T21:00:00Z",
            station_count=4,
            dataset="test-current-hazard",
            warnings=["prototype"],
        )


def _service(tmp_path: Path) -> CoreAnalystAnalysisService:
    return CoreAnalystAnalysisService(
        input_dir=tmp_path / "Input",
        output_dir=tmp_path / "outputs",
        config_dir=Path("analysis/core-analyst/config"),
        current_hazard=FakeCurrentHazard(),  # type: ignore[arg-type]
        current_hazard_raster_path=tmp_path / "current.tif",
    )


def _write_raster(path: Path, data: np.ndarray, dtype: str) -> None:
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": "EPSG:27700",
        "transform": from_origin(0, data.shape[0], 1, 1),
        "nodata": 0 if dtype == "uint8" else np.nan,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(data.astype(dtype), 1)


def _feature(geometry, **properties):
    return {"geometry": mapping(geometry), "properties": properties}


def _hazard_grid() -> RasterGrid:
    values = np.array([[1, 1, 1], [1, 3, 1], [1, 2, 1]], dtype="float32")
    return RasterGrid(
        "hazard_class",
        values,
        {
            "driver": "GTiff",
            "height": 3,
            "width": 3,
            "count": 1,
            "dtype": "uint8",
            "crs": "EPSG:27700",
            "transform": from_origin(0, 3, 1, 1),
            "nodata": 0,
        },
        "analysis_output",
    )


def test_spatial_agent_exposes_extended_core_analysis_tools() -> None:
    tools = {
        name
        for toolset in spatial_agent.toolsets
        for name in getattr(toolset, "tools", {})
    }
    assert {
        "get_core_analysis_data_readiness",
        "run_core_hazard_analysis",
        "run_core_exposure_analysis",
        "run_core_vulnerability_analysis",
        "run_core_priority_analysis",
        "compare_core_priority_scenarios",
        "run_core_priority_sensitivity",
        "compare_core_analysis_runs",
        "combine_core_hazard_analyses",
        "get_core_coastal_dynamic_evidence",
        "run_all_core_hazards",
        "list_nrfa_historical_stations",
        "query_nrfa_historical_series",
        "plan_generalized_core_analysis",
        "register_core_hazard_extension",
        "run_registered_core_hazard",
    } <= tools


async def test_data_readiness_does_not_fabricate_missing_exposure_data(tmp_path: Path) -> None:
    readiness = await _service(tmp_path).data_readiness("exposure")

    assert readiness.available == []
    assert readiness.incomplete
    assert all(item.status in {"partial", "unavailable"} for item in readiness.incomplete)


async def test_live_current_pluvial_run_reuses_existing_sepa_pipeline(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = await service.run_hazard(
        hazard_type="pluvial",
        scenario="current",
        use_live_data=True,
    )

    assert result.status == "success"
    assert result.analysis_type == "hazard_pluvial_current"
    assert "hazard_class" in result.output_keys
    assert result.warnings[0].message == "prototype"


async def test_all_hazards_reports_unavailable_inputs_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_hazard(**kwargs):
        hazard = f"{kwargs['hazard_type']}_{kwargs['scenario']}"
        return {
            "status": "failed",
            "summary": {"error": f"Forecast input unavailable for {hazard}."},
            "outputs": {},
            "warnings": [{"code": "forecast_unavailable", "message": f"No input for {hazard}."}],
        }

    monkeypatch.setattr("oasis.integrations.core_analysis.run_hazard_analysis", unavailable_hazard)

    result = await _service(tmp_path).run_all_hazards(use_live_data=True, forecast_horizon=24)

    assert result.status == "unavailable"
    assert result.summary["available_hazards"] == []
    assert len(result.summary["unavailable_hazards"]) == 6
    assert result.map_layers == []
    assert len(result.warnings) == 6


async def test_priority_run_requires_explicit_unit_scores_and_persists_result(tmp_path: Path) -> None:
    service = _service(tmp_path)
    units = [
        PriorityUnitInput(id="a", hazard=0.9, exposure=0.8, vulnerability=0.2),
        PriorityUnitInput(id="b", hazard=0.2, exposure=0.3, vulnerability=0.9),
    ]
    weights = PriorityWeights(hazard=0.5, exposure=0.3, vulnerability=0.2)

    result = await service.run_priority(
        units=units,
        weights=weights,
        scenario_name="life_safety",
        top_n=2,
    )

    assert result.status == "success"
    assert result.summary["top_areas"][0]["id"] == "a"
    stored = tmp_path / "outputs" / "runs" / result.run_id / "result.json"
    assert stored.is_file()
    repeated = await service.run_priority(
        units=units,
        weights=weights,
        scenario_name="life_safety",
        top_n=2,
    )
    assert repeated.run_id == result.run_id


async def test_priority_scenario_and_sensitivity_tools_use_explicit_weights(tmp_path: Path) -> None:
    service = _service(tmp_path)
    units = [
        PriorityUnitInput(id="a", hazard=1.0, exposure=0.2, vulnerability=0.1),
        PriorityUnitInput(id="b", hazard=0.1, exposure=0.2, vulnerability=1.0),
    ]
    scenarios = [
        PriorityScenarioInput(
            name="hazard_first",
            weights=PriorityWeights(hazard=0.8, exposure=0.1, vulnerability=0.1),
        ),
        PriorityScenarioInput(
            name="equity_first",
            weights=PriorityWeights(hazard=0.1, exposure=0.1, vulnerability=0.8),
        ),
    ]

    comparison = await service.compare_priority(units=units, scenarios=scenarios)
    sensitivity = await service.run_sensitivity(
        units=units,
        base_scenario=scenarios[0],
        vary_component="hazard",
        values=[0.2, 0.8],
    )

    assert comparison.status == "success"
    assert sensitivity.status == "success"


def test_low_level_priority_rejects_implicit_compatibility_weights(tmp_path: Path) -> None:
    with pytest.raises(ToolInputError):
        run_priority_analysis(
            units=[{"id": "a", "hazard": 0.5, "exposure": 0.5, "vulnerability": 0.5}],
            output_dir=tmp_path,
        )


def test_exposure_analysis_counts_intersecting_buildings(tmp_path: Path) -> None:
    buildings = InMemoryVectorSource(
        "buildings",
        [
            _feature(box(1.1, 1.1, 1.9, 1.9), id="inside"),
            _feature(box(10, 10, 11, 11), id="outside"),
        ],
        crs="EPSG:27700",
    )

    result = run_exposure_analysis(
        _hazard_grid(),
        {"buildings": buildings},
        tmp_path,
        hazard_type="pluvial",
        scenario="current",
    )

    assert result["summary"]["buildings"]["total"] == 2
    assert result["summary"]["buildings"]["exposed"] == 1
    exposure_map = Path(result["outputs"]["buildings_exposure"])
    assert exposure_map.is_file()
    assert '"exposed": true' in exposure_map.read_text(encoding="utf-8")


def test_vulnerability_analysis_normalises_verified_indicator(tmp_path: Path) -> None:
    geography = InMemoryVectorSource(
        "data_zones",
        [
            _feature(box(0, 0, 10, 10), id="a", name="A", elderly_prop=0.1),
            _feature(box(10, 0, 20, 10), id="b", name="B", elderly_prop=0.3),
        ],
        crs="EPSG:27700",
    )
    config = {
        "vulnerability": {
            "geography_id_field": "id",
            "geography_name_field": "name",
            "demographic": {
                "indicators": [
                    {
                        "name": "elderly_population_proportion",
                        "field": "elderly_prop",
                        "source_key": "geography",
                        "definition": "Older population share.",
                        "unit": "proportion",
                        "geographic_level": "data_zone",
                        "direction": "higher",
                    }
                ]
            },
            "socioeconomic": {"indicators": []},
            "accessibility": {"source_key": "missing"},
            "composite": {"enabled": False},
        }
    }

    result = run_vulnerability_analysis(geography, {}, tmp_path, config=config)

    scores = result["dimensions"]["demographic"]["unit_scores"]
    assert scores == {"a": 0.0, "b": 1.0}


def test_combined_hazard_uses_maximum_and_source_flags(tmp_path: Path) -> None:
    hazard_results = {}
    arrays = {
        "pluvial": np.array([[0.2, 0.8], [0.1, 0.4]], dtype="float32"),
        "fluvial": np.array([[0.7, 0.3], [0.2, 0.5]], dtype="float32"),
        "coastal": np.array([[0.1, 0.2], [0.9, 0.4]], dtype="float32"),
    }
    classes = {
        "pluvial": np.array([[1, 3], [1, 2]], dtype="uint8"),
        "fluvial": np.array([[3, 1], [1, 2]], dtype="uint8"),
        "coastal": np.array([[1, 1], [3, 2]], dtype="uint8"),
    }
    for hazard_type in arrays:
        index_path = tmp_path / f"{hazard_type}_index.tif"
        class_path = tmp_path / f"{hazard_type}_class.tif"
        _write_raster(index_path, arrays[hazard_type], "float32")
        _write_raster(class_path, classes[hazard_type], "uint8")
        hazard_results[hazard_type] = {
            "run_id": hazard_type * 4,
            "status": "success",
            "hazard_type": hazard_type,
            "scenario": "current",
            "outputs": {
                "hazard_index": str(index_path),
                "hazard_class": str(class_path),
            },
        }

    result = combine_hazard_maps(
        hazard_results,
        tmp_path / "combined",
        scenario="current",
        exposure_threshold=2,
    )

    with rasterio.open(result["outputs"]["hazard_class"]) as dataset:
        assert dataset.read(1).tolist() == [[3, 3], [3, 2]]
    with rasterio.open(result["outputs"]["hazard_source_flags"]) as dataset:
        assert dataset.read(1).tolist() == [[2, 1], [4, 7]]


def test_run_id_validation_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _service(tmp_path)._load("../../outside")


async def test_nrfa_history_is_available_as_agent_service(tmp_path: Path) -> None:
    csv_dir = tmp_path / "Input" / "CSV-20260825T012052Z-1-001" / "CSV"
    csv_dir.mkdir(parents=True)
    with ZipFile(csv_dir / "Rainfall.zip", "w") as archive:
        archive.writestr(
            "84001_test.csv",
            "station,name,Test Station\ndataType,name,Rainfall\n2020-01-01,1.5\n2020-01-02,2.5\n",
        )
    stations = await _service(tmp_path).nrfa_stations("nrfa_historical_rainfall")
    history = await _service(tmp_path).nrfa_history(
        dataset="nrfa_historical_rainfall",
        station_id="84001",
        start_date="2020-01-01",
        end_date="2020-01-02",
    )
    assert stations.summary["station_ids"] == ["84001"]
    assert history.summary["mean"] == 2.0


async def test_generalized_plan_exposes_extension_points(tmp_path: Path) -> None:
    plan = await _service(tmp_path).generalized_plan(
        area="Leeds",
        hazard_type="heat",
        temporal_scope="future",
    )
    assert plan.executable_now is False
    assert "heat_evidence" in plan.required_datasets
    assert plan.extension_points
    assert plan.discovered_sources


async def test_registered_hazard_extension_runs_shared_raster_pipeline(tmp_path: Path) -> None:
    factor = tmp_path / "Input" / "factor.tif"
    factor.parent.mkdir()
    _write_raster(factor, np.array([[0.0, 1.0], [2.0, 3.0]], dtype="float32"), "float32")
    service = _service(tmp_path)
    await service.register_extension(HazardExtensionSpec(
        hazard_type="heat",
        factors=[ExtensionFactor(name="temperature", weight=1.0)],
        medium_threshold=0.4,
        high_threshold=0.8,
    ))
    result = await service.run_extension(
        hazard_type="heat",
        area="Leeds",
        factor_paths={"temperature": str(factor)},
    )
    assert result.status == "success"
    assert {"hazard_class", "hazard_index"} <= set(result.output_keys)
