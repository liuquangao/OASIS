"""Deterministic Data Zone flood priority assessment.

The language model selects this workflow; every spatial value and ranking is
calculated here from persisted evidence.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon as PlotPolygon
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds, transform as window_transform
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree
from shapely.validation import make_valid


PRIORITY_SCENARIOS = {
    "life_safety": {"hazard": 0.45, "exposure": 0.40, "vulnerability": 0.15},
    "social_equity": {"hazard": 0.25, "exposure": 0.25, "vulnerability": 0.50},
    "economic_protection": {"hazard": 0.40, "exposure": 0.45, "vulnerability": 0.15},
}
VULNERABILITY_WEIGHTS = {
    "demographic": 0.34,
    "socioeconomic": 0.33,
    "accessibility": 0.33,
}
SENSITIVITY_WEIGHTS = (0.10, 0.25, 0.40, 0.55, 0.70)


def run_data_zone_flood_priority_assessment(
    *,
    hazard_raster: str | Path,
    data_zones: str | Path,
    output_dir: str | Path,
    scenario: str = "future",
    hazard_threshold: int = 2,
    priority_scenario: str = "social_equity",
    buildings: str | Path | None = None,
    critical_services: str | Path | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate hazard, exposure, vulnerability, and priority per 2022 DZ."""

    if hazard_threshold not in {1, 2, 3}:
        raise ValueError("hazard_threshold must be 1, 2, or 3")
    if priority_scenario not in PRIORITY_SCENARIOS:
        raise ValueError(f"Unknown priority scenario: {priority_scenario}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    zone_payload = json.loads(Path(data_zones).read_text(encoding="utf-8"))
    source_crs = _geojson_crs(zone_payload, "EPSG:4326")
    zones = _zone_records(zone_payload, source_crs)
    if not zones:
        raise ValueError("Data Zone geography contains no usable polygon features")

    with rasterio.open(hazard_raster) as raster:
        raster_crs = str(raster.crs)
        for zone in zones:
            zone["raster_geometry"] = _project_geometry(zone["geometry"], source_crs, raster_crs)
            zone.update(_zonal_hazard(raster, zone["raster_geometry"], hazard_threshold))

        _attach_building_exposure(zones, buildings, raster, raster_crs, hazard_threshold)
        facilities = _attach_facility_exposure(
            zones, critical_services, raster, raster_crs, hazard_threshold
        )

    exposure_inputs = {
        "estimated_exposed_population": {
            zone["id"]: _estimated_exposed_population(zone) for zone in zones
        },
        "exposed_building_count": {
            zone["id"]: zone.get("exposed_building_count") for zone in zones
        },
        "exposed_critical_facility_count": {
            zone["id"]: zone.get("exposed_critical_facility_count") for zone in zones
        },
    }
    exposure_normalized = {
        name: robust_minmax(values) for name, values in exposure_inputs.items()
    }
    for zone in zones:
        values = [values[zone["id"]] for values in exposure_normalized.values()]
        zone["exposure_score"] = _strict_weighted_mean(values, (1 / 3, 1 / 3, 1 / 3))

    _attach_vulnerability(zones, facilities)
    _attach_priority(zones)
    _rank_priority(zones)

    sensitivity = _sensitivity(zones, priority_scenario)
    scenario_summary = _priority_scenario_summary(zones)
    outputs = _write_outputs(
        zones,
        output_dir,
        priority_scenario=priority_scenario,
        scenario_summary=scenario_summary,
        sensitivity=sensitivity,
    )
    missing_components = sorted(
        {
            component
            for zone in zones
            for component in ("hazard_score", "exposure_score", "vulnerability_score")
            if zone.get(component) is None
        }
    )
    complete = [zone for zone in zones if zone.get(f"priority_{priority_scenario}") is not None]
    warnings = []
    if missing_components:
        warnings.append(
            {
                "code": "priority_components_unavailable",
                "message": "Data Zone priority is incomplete because required components are unavailable: "
                + ", ".join(missing_components),
            }
        )
    if not critical_services:
        warnings.append(
            {
                "code": "official_facilities_unavailable",
                "message": "Critical-facility exposure and accessibility require a prepared official facilities file.",
            }
        )
    summary = {
        "assessment_type": "data_zone_flood_priority",
        "spatial_unit": "Scotland Census 2022 Data Zone",
        "scenario": scenario,
        "hazard_threshold": hazard_threshold,
        "priority_scenario": priority_scenario,
        "data_zone_count": len(zones),
        "complete_priority_count": len(complete),
        "estimated_exposed_population": round(
            sum(zone.get("estimated_exposed_population") or 0 for zone in zones)
        ),
        "exposed_buildings": sum(zone.get("exposed_building_count") or 0 for zone in zones),
        "exposed_critical_facilities": sum(
            zone.get("exposed_critical_facility_count") or 0 for zone in zones
        ),
        "top_areas": [
            {
                "id": zone["id"],
                "name": zone["name"],
                "priority_score": zone[f"priority_{priority_scenario}"],
                "rank": zone[f"rank_{priority_scenario}"],
                "hazard_score": zone["hazard_score"],
                "exposure_score": zone["exposure_score"],
                "vulnerability_score": zone["vulnerability_score"],
            }
            for zone in sorted(
                complete,
                key=lambda item: (item[f"rank_{priority_scenario}"], item["id"]),
            )[:10]
        ],
        "interpretation": (
            "Priority is a relative, value-dependent intervention ranking; it is not "
            "a flood probability, operational warning, or individual safety assessment."
        ),
    }
    result = {
        "status": "success" if len(complete) == len(zones) else "partial" if complete else "unavailable",
        "summary": summary,
        "outputs": outputs,
        "provenance": {
            **(provenance or {}),
            "hazard_raster": str(hazard_raster),
            "data_zones": str(data_zones),
            "buildings": str(buildings) if buildings else None,
            "critical_services": str(critical_services) if critical_services else None,
            "methods": {
                "hazard": "zonal class statistics; score=(mean_class-1)/2",
                "population_exposure": "Data Zone population multiplied by hazardous-area fraction",
                "building_assignment": "footprint centroid assigned to exactly one Data Zone",
                "building_exposure": "building footprint intersects a raster cell at or above threshold",
                "normalisation": "5th-95th percentile robust min-max",
                "priority": "explicit weighted sum of hazard, exposure, and vulnerability",
            },
        },
        "warnings": warnings,
    }
    metadata = output_dir / "assessment_metadata.json"
    metadata.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["outputs"]["assessment_metadata"] = str(metadata)
    return result


def robust_minmax(values: dict[str, float | int | None]) -> dict[str, float | None]:
    finite = np.asarray(
        [float(value) for value in values.values() if value is not None and np.isfinite(value)],
        dtype="float64",
    )
    if not finite.size:
        return {key: None for key in values}
    low, high = np.percentile(finite, [5, 95])
    if np.isclose(low, high):
        return {key: 0.0 if value is not None else None for key, value in values.items()}
    return {
        key: None
        if value is None or not np.isfinite(value)
        else float(np.clip((float(value) - low) / (high - low), 0, 1))
        for key, value in values.items()
    }


def area_weight_polygon_values(
    source_features: list[dict[str, Any]],
    target_features: list[dict[str, Any]],
    *,
    source_value_field: str = "deprivation_score",
    target_id_field: str = "id",
) -> dict[str, float | None]:
    """Transfer a polygon value to another geography using overlap area."""

    source_geometries = [make_valid(shape(feature["geometry"])) for feature in source_features]
    tree = STRtree(source_geometries)
    result: dict[str, float | None] = {}
    for feature in target_features:
        target = make_valid(shape(feature["geometry"]))
        weighted_sum = 0.0
        overlap_sum = 0.0
        for index in tree.query(target, predicate="intersects"):
            source = source_geometries[int(index)]
            value = source_features[int(index)].get("properties", {}).get(source_value_field)
            if value in (None, ""):
                continue
            overlap = target.intersection(source).area
            if overlap > 0:
                weighted_sum += float(value) * overlap
                overlap_sum += overlap
        unit_id = str(feature.get("properties", {}).get(target_id_field))
        result[unit_id] = weighted_sum / overlap_sum if overlap_sum else None
    return result


def _zone_records(payload: dict[str, Any], source_crs: str) -> list[dict[str, Any]]:
    records = []
    for feature in payload.get("features", []):
        if not feature.get("geometry"):
            continue
        properties = dict(feature.get("properties", {}))
        unit_id = _property(properties, "id", "dzcode", "data_zone")
        if not unit_id:
            raise ValueError("Data Zone feature is missing an id/dzcode property")
        geometry = make_valid(shape(feature["geometry"]))
        if geometry.is_empty:
            continue
        records.append(
            {
                "id": str(unit_id),
                "name": _property(properties, "name", "dzname") or str(unit_id),
                "geometry": geometry,
                "source_crs": source_crs,
                "population": _number(properties.get("population")),
                "elderly_prop": _number(properties.get("elderly_prop")),
                "no_car_household_prop": _number(properties.get("no_car_household_prop")),
                "deprivation_score": _number(properties.get("deprivation_score")),
            }
        )
    return records


def _zonal_hazard(raster, geometry, threshold: int) -> dict[str, Any]:
    values = _masked_values(raster, geometry)
    valid = values[np.isin(values, (1, 2, 3))]
    counts = {value: int(np.count_nonzero(valid == value)) for value in (1, 2, 3)}
    total = int(valid.size)
    cell_area = abs(raster.transform.a * raster.transform.e - raster.transform.b * raster.transform.d)
    mean_class = float(np.mean(valid)) if total else None
    return {
        "hazard_mean_class": mean_class,
        "hazard_max_class": int(np.max(valid)) if total else None,
        "hazard_score": None if mean_class is None else (mean_class - 1.0) / 2.0,
        "hazardous_area_fraction": float(np.count_nonzero(valid >= threshold) / total) if total else None,
        "low_area_km2": counts[1] * cell_area / 1_000_000,
        "medium_area_km2": counts[2] * cell_area / 1_000_000,
        "high_area_km2": counts[3] * cell_area / 1_000_000,
        "classified_pixel_count": total,
    }


def _attach_building_exposure(zones, path, raster, raster_crs, threshold):
    for zone in zones:
        zone["building_count"] = None if not path else 0
        zone["exposed_building_count"] = None if not path else 0
    if not path:
        return
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    source_crs = _geojson_crs(payload, "EPSG:4326")
    zone_geometries = [zone["raster_geometry"] for zone in zones]
    tree = STRtree(zone_geometries)
    for feature in payload.get("features", []):
        if not feature.get("geometry"):
            continue
        geometry = _project_geometry(make_valid(shape(feature["geometry"])), source_crs, raster_crs)
        matches = tree.query(geometry.centroid, predicate="intersects")
        if not len(matches):
            continue
        index = int(sorted(int(value) for value in matches)[0])
        zones[index]["building_count"] += 1
        if _geometry_has_hazard(raster, geometry, threshold):
            zones[index]["exposed_building_count"] += 1


def _attach_facility_exposure(zones, path, raster, raster_crs, threshold):
    for zone in zones:
        zone["critical_facility_count"] = None if not path else 0
        zone["exposed_critical_facility_count"] = None if not path else 0
        zone["facility_counts_by_type"] = {}
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    source_crs = _geojson_crs(payload, "EPSG:4326")
    zone_geometries = [zone["raster_geometry"] for zone in zones]
    tree = STRtree(zone_geometries)
    facilities = []
    for feature in payload.get("features", []):
        if not feature.get("geometry"):
            continue
        properties = feature.get("properties", {})
        facility_type = str(properties.get("type", "unknown"))
        geometry = _project_geometry(shape(feature["geometry"]), source_crs, raster_crs)
        facilities.append({"type": facility_type, "geometry": geometry})
        matches = tree.query(geometry, predicate="intersects")
        if not len(matches):
            continue
        index = int(sorted(int(value) for value in matches)[0])
        zone = zones[index]
        zone["critical_facility_count"] += 1
        zone["facility_counts_by_type"][facility_type] = (
            zone["facility_counts_by_type"].get(facility_type, 0) + 1
        )
        if _geometry_has_hazard(raster, geometry, threshold):
            zone["exposed_critical_facility_count"] += 1
    return facilities


def _attach_vulnerability(zones, facilities):
    demographic = robust_minmax({zone["id"]: zone["elderly_prop"] for zone in zones})
    no_car = robust_minmax({zone["id"]: zone["no_car_household_prop"] for zone in zones})
    deprivation = robust_minmax({zone["id"]: zone["deprivation_score"] for zone in zones})
    hospital = _nearest_distance(zones, facilities, {"hospital"})
    emergency = _nearest_distance(zones, facilities, {"emergency_service", "fire_station"})
    hospital_norm = robust_minmax(hospital)
    emergency_norm = robust_minmax(emergency)
    for zone in zones:
        unit_id = zone["id"]
        socioeconomic = _strict_weighted_mean(
            [no_car[unit_id], deprivation[unit_id]], [0.5, 0.5]
        )
        accessibility = _strict_weighted_mean(
            [hospital_norm[unit_id], emergency_norm[unit_id]], [0.5, 0.5]
        )
        vulnerability = _strict_weighted_mean(
            [demographic[unit_id], socioeconomic, accessibility],
            list(VULNERABILITY_WEIGHTS.values()),
        )
        zone.update(
            {
                "demographic_vulnerability": demographic[unit_id],
                "socioeconomic_vulnerability": socioeconomic,
                "accessibility_vulnerability": accessibility,
                "distance_to_hospital_m": hospital[unit_id],
                "distance_to_emergency_service_m": emergency[unit_id],
                "vulnerability_score": vulnerability,
                "vulnerability_without_simd": _strict_weighted_mean(
                    [demographic[unit_id], no_car[unit_id], accessibility],
                    list(VULNERABILITY_WEIGHTS.values()),
                ),
            }
        )


def _attach_priority(zones):
    for zone in zones:
        components = [zone["hazard_score"], zone["exposure_score"], zone["vulnerability_score"]]
        for scenario, weights in PRIORITY_SCENARIOS.items():
            zone[f"priority_{scenario}"] = _strict_weighted_mean(
                components, list(weights.values())
            )


def _rank_priority(zones):
    for scenario in PRIORITY_SCENARIOS:
        complete = [zone for zone in zones if zone[f"priority_{scenario}"] is not None]
        complete.sort(key=lambda zone: (-zone[f"priority_{scenario}"], zone["id"]))
        for rank, zone in enumerate(complete, 1):
            zone[f"rank_{scenario}"] = rank
        for zone in zones:
            zone.setdefault(f"rank_{scenario}", None)


def _sensitivity(zones, base_scenario):
    base_weights = PRIORITY_SCENARIOS[base_scenario]
    rows = []
    for vulnerability_weight in SENSITIVITY_WEIGHTS:
        remainder = 1.0 - vulnerability_weight
        denominator = base_weights["hazard"] + base_weights["exposure"]
        weights = {
            "hazard": remainder * base_weights["hazard"] / denominator,
            "exposure": remainder * base_weights["exposure"] / denominator,
            "vulnerability": vulnerability_weight,
        }
        scores = []
        for zone in zones:
            score = _strict_weighted_mean(
                [zone["hazard_score"], zone["exposure_score"], zone["vulnerability_score"]],
                list(weights.values()),
            )
            if score is not None:
                scores.append((zone["id"], score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        rows.append(
            {
                "vulnerability_weight": vulnerability_weight,
                "weights": weights,
                "top_10": [unit_id for unit_id, _ in scores[:10]],
            }
        )
    threshold_rows = {
        str(threshold): {
            zone["id"]: (
                zone["medium_area_km2"] + zone["high_area_km2"]
                if threshold == 2
                else zone["high_area_km2"]
            )
            for zone in zones
        }
        for threshold in (2, 3)
    }
    without_simd = [
        {
            "id": zone["id"],
            "vulnerability_with_simd": zone["vulnerability_score"],
            "vulnerability_without_simd": zone["vulnerability_without_simd"],
        }
        for zone in zones
    ]
    base_top_10 = [
        zone["id"]
        for zone in sorted(
            (zone for zone in zones if zone[f"rank_{base_scenario}"] is not None),
            key=lambda zone: zone[f"rank_{base_scenario}"],
        )[:10]
    ]
    return {
        "interpretation": "Decision sensitivity, not probabilistic uncertainty.",
        "base_scenario": base_scenario,
        "base_top_10": base_top_10,
        "priority_weight_sweep": rows,
        "hazard_threshold_area_km2": threshold_rows,
        "simd_inclusion": without_simd,
    }


def _priority_scenario_summary(zones):
    return {
        scenario: {
            "weights": weights,
            "top_10": [
                zone["id"]
                for zone in sorted(
                    (item for item in zones if item[f"rank_{scenario}"] is not None),
                    key=lambda item: item[f"rank_{scenario}"],
                )[:10]
            ],
        }
        for scenario, weights in PRIORITY_SCENARIOS.items()
    }


def _write_outputs(zones, output_dir, *, priority_scenario, scenario_summary, sensitivity):
    public_fields = [
        "id", "name", "population", "hazard_mean_class", "hazard_max_class", "hazard_score",
        "hazardous_area_fraction", "low_area_km2", "medium_area_km2", "high_area_km2",
        "estimated_exposed_population", "building_count", "exposed_building_count",
        "critical_facility_count", "exposed_critical_facility_count", "exposure_score",
        "elderly_prop", "no_car_household_prop", "deprivation_score",
        "demographic_vulnerability", "socioeconomic_vulnerability", "accessibility_vulnerability",
        "distance_to_hospital_m", "distance_to_emergency_service_m", "vulnerability_score",
        "vulnerability_without_simd", "priority_score", "priority_rank",
        "priority_life_safety", "rank_life_safety", "priority_social_equity", "rank_social_equity",
        "priority_economic_protection", "rank_economic_protection",
    ]
    geojson_specs = {
        "hazard_by_data_zone": ["hazard_score", "hazard_mean_class", "hazard_max_class", "hazardous_area_fraction"],
        "exposure_by_data_zone": ["exposure_score", "estimated_exposed_population", "exposed_building_count", "exposed_critical_facility_count"],
        "vulnerability_by_data_zone": ["vulnerability_score", "demographic_vulnerability", "socioeconomic_vulnerability", "accessibility_vulnerability"],
        "priority_by_data_zone": ["priority_score", "priority_rank", "hazard_score", "exposure_score", "vulnerability_score"],
    }
    outputs = {}
    for name, fields in geojson_specs.items():
        features = []
        for zone in zones:
            properties = {"id": zone["id"], "name": zone["name"]}
            if name == "priority_by_data_zone":
                properties.update(
                    {
                        "priority_score": zone[f"priority_{priority_scenario}"],
                        "priority_rank": zone[f"rank_{priority_scenario}"],
                        "priority_scenario": priority_scenario,
                    }
                )
            properties.update({field: zone.get(field) for field in fields if field not in properties})
            geometry = transform_geom(zone["source_crs"], "EPSG:4326", mapping(zone["geometry"]))
            features.append({"type": "Feature", "geometry": geometry, "properties": properties})
        path = output_dir / f"{name}.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        outputs[name] = str(path)

    csv_path = output_dir / "data_zone_assessment.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=public_fields)
        writer.writeheader()
        for zone in zones:
            row = {field: zone.get(field) for field in public_fields}
            row["priority_score"] = zone[f"priority_{priority_scenario}"]
            row["priority_rank"] = zone[f"rank_{priority_scenario}"]
            writer.writerow(row)
    outputs["data_zone_assessment"] = str(csv_path)

    scenarios_path = output_dir / "priority_scenarios.json"
    scenarios_path.write_text(json.dumps(scenario_summary, indent=2), encoding="utf-8")
    outputs["priority_scenarios"] = str(scenarios_path)
    sensitivity_path = output_dir / "sensitivity.json"
    sensitivity_path.write_text(json.dumps(sensitivity, indent=2), encoding="utf-8")
    outputs["sensitivity"] = str(sensitivity_path)

    map_path = output_dir / "hazard_exposure_vulnerability_priority.png"
    _plot_four_panel(zones, priority_scenario, map_path)
    outputs["four_panel_map"] = str(map_path)
    curve_path = output_dir / "priority_sensitivity.png"
    _plot_sensitivity(sensitivity, curve_path)
    outputs["sensitivity_figure"] = str(curve_path)
    return outputs


def _plot_four_panel(zones, scenario, path):
    panels = (
        ("hazard_score", "Hazard"),
        ("exposure_score", "Exposure"),
        ("vulnerability_score", "Vulnerability"),
        (f"priority_{scenario}", f"Priority · {scenario.replace('_', ' ')}"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(9, 9), constrained_layout=True)
    for axis, (field, title) in zip(axes.flat, panels):
        patches, values = [], []
        for zone in zones:
            value = zone.get(field)
            for polygon in _polygons(zone["geometry"]):
                patches.append(PlotPolygon(np.asarray(polygon.exterior.coords), closed=True))
                values.append(np.nan if value is None else value)
        collection = PatchCollection(patches, cmap="viridis", edgecolor="#334155", linewidth=0.15)
        collection.set_array(np.asarray(values, dtype="float64"))
        collection.set_clim(0, 1)
        axis.add_collection(collection)
        axis.autoscale_view()
        axis.set_aspect("equal")
        axis.axis("off")
        axis.set_title(title)
    fig.colorbar(collection, ax=axes, location="bottom", fraction=0.035, label="Relative score (0–1)")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_sensitivity(sensitivity, path):
    rows = sensitivity["priority_weight_sweep"]
    baseline = set(sensitivity["base_top_10"])
    overlap = [len(baseline & set(row["top_10"])) for row in rows]
    fig, axis = plt.subplots(figsize=(6.4, 3.6), constrained_layout=True)
    axis.plot([row["vulnerability_weight"] for row in rows], overlap, marker="o")
    axis.set(xlabel="Vulnerability weight", ylabel="Top-10 overlap with default scenario", ylim=(0, 10.5))
    axis.grid(alpha=0.25)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _estimated_exposed_population(zone):
    population = zone.get("population")
    fraction = zone.get("hazardous_area_fraction")
    value = None if population is None or fraction is None else population * fraction
    zone["estimated_exposed_population"] = value
    return value


def _nearest_distance(zones, facilities, types):
    geometries = [item["geometry"] for item in facilities if item["type"] in types]
    if not geometries:
        return {zone["id"]: None for zone in zones}
    tree = STRtree(geometries)
    return {
        zone["id"]: float(zone["raster_geometry"].centroid.distance(geometries[tree.nearest(zone["raster_geometry"].centroid)]))
        for zone in zones
    }


def _strict_weighted_mean(values, weights):
    if any(value is None for value in values):
        return None
    return float(sum(float(value) * float(weight) for value, weight in zip(values, weights)))


def _masked_values(raster, geometry):
    left, bottom, right, top = geometry.bounds
    raw = from_bounds(left, bottom, right, top, raster.transform)
    column_start = max(0, int(np.floor(raw.col_off)))
    row_start = max(0, int(np.floor(raw.row_off)))
    column_stop = min(raster.width, int(np.ceil(raw.col_off + raw.width)))
    row_stop = min(raster.height, int(np.ceil(raw.row_off + raw.height)))
    if column_stop <= column_start or row_stop <= row_start:
        return np.asarray([], dtype="float32")
    window = Window(column_start, row_start, column_stop - column_start, row_stop - row_start)
    data = raster.read(1, window=window, masked=True)
    inside = geometry_mask(
        [mapping(geometry)],
        out_shape=data.shape,
        transform=window_transform(window, raster.transform),
        invert=True,
    )
    return np.asarray(data.data[inside & ~np.ma.getmaskarray(data)])


def _geometry_has_hazard(raster, geometry, threshold):
    values = _masked_values(raster, geometry)
    return bool(values.size and np.any(values >= threshold))


def _project_geometry(geometry, source_crs, target_crs):
    if source_crs == target_crs:
        return geometry
    return make_valid(shape(transform_geom(source_crs, target_crs, mapping(geometry))))


def _geojson_crs(payload, default):
    crs = payload.get("crs", {})
    return str(crs.get("properties", {}).get("name") or default)


def _property(properties, *names):
    normalized = {
        "".join(character.lower() for character in str(key) if character.isalnum()): value
        for key, value in properties.items()
    }
    for name in names:
        key = "".join(character.lower() for character in name if character.isalnum())
        value = normalized.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value):
    return None if value in (None, "") else float(value)


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return []
