from __future__ import annotations

import csv
import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from hydromind.models.analysis import AnalysisRunSummary, PriorityWeights
from hydromind.models.assessment import AssessmentPreferences
from hydromind.risk_reporting import build_priority_risk_report


def test_priority_report_explains_inputs_and_weighted_contributions(tmp_path) -> None:
    table = tmp_path / "assessment.csv"
    row = {
        "id": "zone-1",
        "hazardous_area_fraction": "0.75",
        "estimated_exposed_population": "300",
        "building_count": "20",
        "exposed_building_count": "15",
        "critical_facility_count": "1",
        "exposed_critical_facility_count": "1",
    }
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)

    pluvial_dir = tmp_path / "pluvial"
    fluvial_dir = tmp_path / "fluvial"
    coastal_dir = tmp_path / "coastal"
    for directory in (pluvial_dir, fluvial_dir, coastal_dir):
        directory.mkdir()
    baseline = tmp_path / "sepa_baseline.tif"
    with rasterio.open(
        baseline,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
    ) as dataset:
        dataset.write(np.asarray([[1, 0], [1, 0]], dtype="uint8"), 1)
    zones = tmp_path / "priority.geojson"
    zones.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"id": "zone-1"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rainfall_observations = {
        "retrieved_at": "2026-09-02T08:00:00Z",
        "stations": [{"rainfall_mm": 2.0, "accumulation_hours": 2.0}],
    }
    rainfall_forecast = {
        "retrieved_at": "2026-09-02T08:05:00Z",
        "stations": [{"rainfall_mm_per_hour": 8.0}],
    }
    (pluvial_dir / "analysis_metadata.json").write_text(
        json.dumps(
            {
                "observed_rainfall_source": {"metadata": {"observations": rainfall_observations}},
                "forecast_rainfall_source": {"metadata": {"observations": rainfall_forecast}},
            }
        ),
        encoding="utf-8",
    )
    (fluvial_dir / "analysis_metadata.json").write_text(
        json.dumps(
            {
                "weights": {"future_weights": {"baseline_low": 0.45, "baseline_medium": 0.15}},
                "static_baseline": {"baseline_low": {"metadata": {"path": str(baseline)}}},
                "current_forcings": {
                    "river_level_observation": {
                        "metadata": {
                            "observations": {
                                "retrieved_at": "2026-09-02T08:01:00Z",
                                "risk_thresholds_m": [0, 1, 3, 5],
                                "stations": [{"level_m": 0.5}, {"level_m": 2.0}],
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (coastal_dir / "analysis_metadata.json").write_text(
        json.dumps(
            {
                "weights": {"future_weights": {"baseline_low": 0.55, "baseline_medium": 0.20}},
                "static_baseline": {"baseline_low": {"metadata": {"path": str(baseline)}}},
            }
        ),
        encoding="utf-8",
    )

    runs = {
        "assessment01": {
            "analysis_type": "flood_priority_assessment",
            "outputs": {
                "data_zone_assessment": str(table),
                "priority_by_data_zone": str(zones),
            },
            "provenance": {"all_hazards_run_id": "hazards000001"},
            "summary": {"scenario": "future"},
        },
        "hazards000001": {
            "analysis_type": "all_hazards",
            "outputs": {
                "pluvial_future_hazard_class": str(pluvial_dir / "future_hazard_class.tif"),
                "fluvial_future_hazard_class": str(fluvial_dir / "future_hazard_class.tif"),
                "coastal_future_hazard_class": str(coastal_dir / "future_hazard_class.tif"),
            },
        },
    }
    result = AnalysisRunSummary(
        run_id="assessment01",
        analysis_type="flood_priority_assessment",
        status="success",
        summary={
            "top_areas": [
                {
                    "id": "zone-1",
                    "name": "Example Zone",
                    "rank": 1,
                    "priority_score": 0.65,
                    "hazard_score": 0.5,
                    "exposure_score": 0.9,
                    "vulnerability_score": 0.3,
                }
            ]
        },
    )
    preferences = AssessmentPreferences(
        scenario="future",
        forecast_horizon_hours=24,
        priority_scenario="economic_protection",
        weights=PriorityWeights(hazard=0.4, exposure=0.45, vulnerability=0.15),
    )

    report = build_priority_risk_report(
        question="What is the flood risk tomorrow?",
        result=result,
        quality={"status": "pass"},
        preferences=preferences,
        load_run=runs.__getitem__,
    )

    assert report.time_horizon == "Next 24 hours"
    assert report.drivers[0].value == "Fluvial 60% · Coastal 75% of each model"
    assert any(driver.value.endswith("moderate rain") for driver in report.drivers)
    assert report.findings[0].facts[0] == (
        "In the SEPA low-likelihood static flood envelope: river 50%, coastal 50%."
    )
    assert report.findings[0].facts[1] == "75% of its classified area is in flood class 2 or 3."
    assert report.findings[0].contributions[1].contribution == 0.405
    assert report.calculation is not None
    assert report.calculation.lens == "Economic protection"
