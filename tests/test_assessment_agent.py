from __future__ import annotations

import csv
from datetime import UTC, datetime
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from core_analyst.historical_validation import _forecast_from_directory, _forecast_reference_time
from hydromind.agent import tool_names_for_intent
from hydromind.assessment import create_assessment_plan, load_assessment_plan
from hydromind.integrations.core_analysis import CoreAnalystAnalysisService
from hydromind.models.analysis import DataReadinessItem, DataReadinessSummary, PriorityWeights
from hydromind.models.assessment import AnalysisIntent
from hydromind.models.current_hazard import CurrentHazardSnapshot


class FakeCurrentHazard:
    async def refresh(self) -> CurrentHazardSnapshot:
        return CurrentHazardSnapshot(available=False)


def _service(tmp_path: Path) -> CoreAnalystAnalysisService:
    return CoreAnalystAnalysisService(
        input_dir=tmp_path / "Input",
        output_dir=tmp_path / "outputs",
        config_dir=Path("analysis/core-analyst/config"),
        current_hazard=FakeCurrentHazard(),  # type: ignore[arg-type]
        current_hazard_raster_path=tmp_path / "current.tif",
    )


def test_each_intent_exposes_at_most_eight_relevant_tools() -> None:
    categories = (
        "point_risk",
        "rainfall_water",
        "route_nearby",
        "city_hazard",
        "integrated_risk",
        "historical_validation",
        "setup_help",
    )
    for category in categories:
        names = tool_names_for_intent(category)  # type: ignore[arg-type]
        assert 1 <= len(names) <= 8
    assert "run_core_flood_priority_assessment" in tool_names_for_intent("integrated_risk")
    assert "run_historical_flood_validation" in tool_names_for_intent("historical_validation")


def test_plan_is_persisted_without_executing_analysis(tmp_path: Path) -> None:
    readiness = DataReadinessSummary(
        status_counts={"available": 1, "unavailable": 1},
        available=[DataReadinessItem(dataset="population", category="exposure", status="available")],
        incomplete=[DataReadinessItem(dataset="radar", category="hazard", status="unavailable")],
    )
    intent = AnalysisIntent(
        category="integrated_risk",
        rationale="The request combines physical hazard and social equity.",
    )

    plan = create_assessment_plan(
        question="Which communities should be prioritised tomorrow?",
        intent=intent,
        readiness=readiness,
        output_dir=tmp_path,
    )

    assert plan.status == "awaiting_confirmation"
    assert plan.preferences.weights == PriorityWeights(
        hazard=0.25, exposure=0.25, vulnerability=0.50
    )
    assert plan.missing_datasets == ["radar"]
    assert not (tmp_path / "runs").exists()
    assert load_assessment_plan(tmp_path, plan.plan_id) == plan


async def test_rerank_reuses_persisted_components_without_external_calls(tmp_path: Path) -> None:
    root = tmp_path / "assessment"
    root.mkdir()
    csv_path = root / "data_zone_assessment.csv"
    rows = [
        {"id": "a", "name": "A", "hazard_score": "0.9", "exposure_score": "0.2", "vulnerability_score": "0.1", "priority_rank": "1"},
        {"id": "b", "name": "B", "hazard_score": "0.2", "exposure_score": "0.3", "vulnerability_score": "0.9", "priority_rank": "2"},
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    geojson = root / "priority_by_data_zone.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": None, "properties": {"id": row["id"]}}
                    for row in rows
                ],
            }
        ),
        encoding="utf-8",
    )
    sensitivity = root / "sensitivity.json"
    sensitivity.write_text(
        json.dumps(
            {
                "simd_inclusion": [
                    {"id": "a", "vulnerability_without_simd": 0.1},
                    {"id": "b", "vulnerability_without_simd": 0.9},
                ]
            }
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path)
    source = service._persist(
        "abcdef123456",
        "flood_priority_assessment",
        {
            "status": "success",
            "summary": {"priority_scenario": "social_equity", "data_zone_count": 2},
            "outputs": {
                "data_zone_assessment": str(csv_path),
                "priority_by_data_zone": str(geojson),
                "sensitivity": str(sensitivity),
            },
            "provenance": {},
            "warnings": [],
        },
    )

    result = await service.rerank_flood_priority(
        source_run_id=source.run_id,
        weights=PriorityWeights(hazard=0.1, exposure=0.1, vulnerability=0.8),
        include_simd=False,
    )

    assert result.summary["top_areas"][0]["id"] == "b"
    stored = service._load(result.run_id)
    assert stored["provenance"]["external_api_calls"] == 0
    assert stored["provenance"]["source_run_id"] == source.run_id


async def test_dominant_medium_hazard_is_flagged_by_quality_gate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    root = tmp_path / "quality"
    root.mkdir()
    features = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {"id": "a"}},
            {"type": "Feature", "geometry": None, "properties": {"id": "b"}},
        ],
    }
    outputs = {}
    for key in (
        "hazard_by_data_zone",
        "exposure_by_data_zone",
        "vulnerability_by_data_zone",
        "priority_by_data_zone",
    ):
        path = root / f"{key}.geojson"
        path.write_text(json.dumps(features), encoding="utf-8")
        outputs[key] = str(path)
    table = root / "data_zone_assessment.csv"
    table.write_text(
        "id,hazard_score,exposure_score,vulnerability_score,priority_rank\n"
        "a,0.5,0.2,0.7,1\n"
        "b,0.5,0.3,0.6,2\n",
        encoding="utf-8",
    )
    outputs["data_zone_assessment"] = str(table)
    hazards = service._persist(
        "111111111111",
        "all_hazards",
        {
            "status": "success",
            "summary": {
                "class_statistics": {
                    "combined_future": {
                        "classes": {
                            "medium": {"percent_of_classified_area": 95.0},
                            "high": {"percent_of_classified_area": 0.0},
                        }
                    }
                }
            },
            "outputs": {},
            "provenance": {},
            "warnings": [],
        },
    )
    assessment = service._persist(
        "222222222222",
        "flood_priority_assessment",
        {
            "status": "success",
            "summary": {
                "scenario": "future",
                "data_zone_count": 2,
                "complete_priority_count": 2,
            },
            "outputs": outputs,
            "provenance": {"all_hazards_run_id": hazards.run_id},
            "warnings": [],
        },
    )

    quality = await service.validate_flood_priority_run(
        assessment.run_id,
        expected_data_zone_count=2,
    )

    check = next(item for item in quality["checks"] if item["code"] == "hazard_distribution")
    assert quality["status"] == "warning"
    assert check["status"] == "warning"
    assert "95.00% Medium" in check["message"]


def test_historical_forecast_reference_time_is_auditable(tmp_path: Path) -> None:
    path = tmp_path / "ukv_precipitation.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=1,
        height=1,
        count=1,
        dtype="float32",
        crs="EPSG:27700",
        transform=from_origin(0, 1, 1, 1),
    ) as dataset:
        dataset.write(np.ones((1, 1), dtype="float32"), 1)
        dataset.update_tags(forecast_reference_time="2023-10-06T06:00:00Z")

    assert _forecast_reference_time(path) == datetime(2023, 10, 6, 6, tzinfo=UTC)


def test_historical_forecast_directory_selects_issue_time_grib(tmp_path: Path) -> None:
    archive = tmp_path / "ukv_202310"
    archive.mkdir()
    (archive / "202310060600_u1096_ng_umqv_Wholesale1.grib.part").write_bytes(b"incomplete")
    expected = archive / "202310060600_u1096_ng_umqv_Wholesale1.grib"
    expected.write_bytes(b"complete")
    (archive / "202310061200_u1096_ng_umqv_Wholesale1.grib").write_bytes(b"later")

    assert _forecast_from_directory(archive, datetime(2023, 10, 6, 6, tzinfo=UTC)) == expected
