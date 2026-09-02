from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, box, mapping

from core_analyst.analysts.data_zone_assessment import (
    area_weight_polygon_values,
    run_data_zone_flood_priority_assessment,
)
from core_analyst.official_facilities import normalize_postcode, prepare_official_facilities
from core_analyst.real_data_inputs import build_enriched_data_zone_geography


def _geojson(path: Path, features: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:27700"}},
                "features": features,
            }
        ),
        encoding="utf-8",
    )
    return path


def _feature(geometry, **properties):
    return {"type": "Feature", "geometry": mapping(geometry), "properties": properties}


def test_case_insensitive_data_zone_join_and_zero_match_failure(tmp_path: Path) -> None:
    attributes = tmp_path / "attributes.csv"
    attributes.write_text(
        "id,name,population,elderly_count,elderly_prop,occupied_households,no_car_households,no_car_household_prop\n"
        "S01000001,Zone,100,20,0.2,50,10,0.2\n",
        encoding="utf-8",
    )
    boundary = _geojson(
        tmp_path / "zones.geojson",
        [_feature(box(0, 0, 1, 1), dzcode="S01000001", dzname="Original")],
    )
    output = build_enriched_data_zone_geography(boundary, attributes, tmp_path / "enriched.geojson")
    payload = json.loads(output.read_text())
    assert payload["features"][0]["properties"]["population"] == 100.0
    assert payload["metadata"]["matched_feature_count"] == 1

    unmatched = _geojson(
        tmp_path / "unmatched.geojson",
        [_feature(box(0, 0, 1, 1), DZCODE="S01099999")],
    )
    try:
        build_enriched_data_zone_geography(unmatched, attributes, tmp_path / "bad.geojson")
    except ValueError as exc:
        assert "no features matched" in str(exc)
    else:
        raise AssertionError("A non-empty zero-match join must fail")


def test_area_weighted_simd_transfer() -> None:
    source = [
        _feature(box(0, 0, 1, 1), deprivation_score=0.2),
        _feature(box(1, 0, 2, 1), deprivation_score=0.8),
    ]
    target = [_feature(box(0, 0, 2, 1), id="target")]
    assert area_weight_polygon_values(source, target)["target"] == 0.5


def test_official_facilities_use_postcodes_and_direct_coordinates(tmp_path: Path) -> None:
    postcodes = tmp_path / "postcodes.csv"
    postcodes.write_text(
        "Postcode,GridReferenceEasting,GridReferenceNorthing\nG2 8JB,1,1\n",
        encoding="utf-8",
    )
    hospital = tmp_path / "hospital.csv"
    hospital.write_text("HospitalName,Postcode\nTest Hospital,G28JB\n", encoding="utf-8")
    fire = tmp_path / "fire.csv"
    fire.write_text("Station,Easting,Northing\nTest Fire,2,2\n", encoding="utf-8")
    study = _geojson(tmp_path / "study.geojson", [_feature(box(0, 0, 3, 3))])

    result = prepare_official_facilities(
        postcode_directory=postcodes,
        facility_sources={"hospital": hospital, "emergency_service": fire},
        study_area=study,
        output_path=tmp_path / "facilities.geojson",
    )

    assert normalize_postcode("G2 8JB") == "G28JB"
    assert result["feature_count"] == 2
    assert result["by_type"]["hospital"]["postcode_matches"] == 1
    assert result["by_type"]["emergency_service"]["located_records"] == 1


def test_end_to_end_data_zone_assessment_writes_all_outputs(tmp_path: Path) -> None:
    raster_path = tmp_path / "hazard.tif"
    values = np.array(
        [[1, 2, 2, 3], [1, 2, 3, 3], [1, 1, 2, 3], [1, 1, 2, 2]],
        dtype="uint8",
    )
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint8",
        crs="EPSG:27700",
        transform=from_origin(0, 4, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)
    zones = _geojson(
        tmp_path / "zones.geojson",
        [
            _feature(
                box(0, 0, 2, 4), id="a", name="A", population=100,
                elderly_prop=0.1, no_car_household_prop=0.2, deprivation_score=0.3,
            ),
            _feature(
                box(2, 0, 4, 4), id="b", name="B", population=200,
                elderly_prop=0.3, no_car_household_prop=0.5, deprivation_score=0.8,
            ),
        ],
    )
    buildings = _geojson(
        tmp_path / "buildings.geojson",
        [_feature(box(0.2, 2.2, 0.8, 2.8)), _feature(box(2.2, 2.2, 2.8, 2.8))],
    )
    facilities = _geojson(
        tmp_path / "facilities.geojson",
        [
            _feature(Point(0.4, 0.4), type="hospital"),
            _feature(Point(3.4, 3.4), type="emergency_service"),
            _feature(Point(2.4, 2.4), type="school"),
        ],
    )

    result = run_data_zone_flood_priority_assessment(
        hazard_raster=raster_path,
        data_zones=zones,
        buildings=buildings,
        critical_services=facilities,
        output_dir=tmp_path / "output",
    )

    assert result["status"] == "success"
    assert result["summary"]["data_zone_count"] == 2
    assert result["summary"]["complete_priority_count"] == 2
    assert result["summary"]["top_areas"][0]["id"] == "b"
    assert {
        "hazard_by_data_zone", "exposure_by_data_zone", "vulnerability_by_data_zone",
        "priority_by_data_zone", "data_zone_assessment", "priority_scenarios",
        "sensitivity", "four_panel_map", "sensitivity_figure",
    } <= result["outputs"].keys()
    assert all(Path(path).is_file() for path in result["outputs"].values())
    with Path(result["outputs"]["data_zone_assessment"]).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert all(row["priority_score"] and row["priority_rank"] for row in rows)
