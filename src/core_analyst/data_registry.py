from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import json
from pathlib import Path
from typing import Any

import rasterio

ADMIRALTY_ENV_KEYS = ("ADMIRALTY_API_KEY", "UKHO_API_KEY", "ADMIRALTY_TIDAL_API_KEY", "ADMIRALTY_SUBSCRIPTION_KEY")


@dataclass(frozen=True)
class DataAvailabilityRecord:
    dataset: str
    category: str
    flood_type: str | None
    temporal_state: str | None
    evidence_type: str | None
    status: str
    type: str
    source: str | None
    local_path: str | None = None
    crs: str | None = None
    spatial_resolution: str | None = None
    temporal_resolution: str | None = None
    reason_if_unavailable: str | None = None
    priority: str | None = None
    analytical_role: str | None = None
    official_source_url: str | None = None
    record_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_core_data_registry(input_dir: str | Path = "Input") -> list[dict[str, Any]]:
    input_dir = Path(input_dir)
    gdb_path = input_dir / "HYDROMIND_raster.gdb" / "HYDROMIND_raster.gdb"
    tif_dir = input_dir / "HYDROMIND_Rasters" / "HYDROMIND_Rasters"
    csv_dir = input_dir / "CSV-20260825T012052Z-1-001" / "CSV"
    polygons_dir = input_dir / "HYDROMIND_Polygon" / "HYDROMIND_Polygon"

    gdb_layers = _gdb_layers(gdb_path)
    records: list[DataAvailabilityRecord] = []

    static_layers = [
        ("DEM", "DTM_5m_res", "pluvial,fluvial,coastal", "all", "static", "observed", "Local HydroMind raster geodatabase", "P0", "Shared terrain condition."),
        ("Slope", "Slope_degrees_DTM_5m", "pluvial", "all", "static", "observed", "Local HydroMind raster geodatabase", "P0", "Static terrain factor."),
        ("FlowAcc_DTM_5m", "FlowAcc_DTM_5m", "pluvial", "all", "static", "observed", "Local HydroMind raster geodatabase", "P0", "Primary flow accumulation layer; DEM-derived proxy is retained only as fallback."),
        ("Land Cover", "UKCEH_Landcover_5m_res", "pluvial", "all", "static", "observed", "UKCEH / local HydroMind raster geodatabase", "P0", "Runoff surface context."),
        ("Imperviousness", "OS_Built_Up_Areas_5m_res", "pluvial", "all", "static", "proxy", "Derived from OS built-up, OS greenspace, and UKCEH land-cover rasters", "P0", "Runoff susceptibility proxy; not measured impervious percentage."),
        ("River Network", "OS_Rivers_5m_res", "fluvial", "all", "static", "observed", "OS Rivers / local HydroMind raster geodatabase", "P0", "River-network presence factor."),
        ("SEPA River Flood Maps", "SEPA_River_High_Flood_5m_res", "fluvial", "all", "static", "observed", "SEPA flood hazard maps", "P0", "High/medium/low river flood reference maps are stored in the geodatabase."),
        ("SEPA Coastal Flood Maps", "SEPA_Coastal_High_Flood_5m_res", "coastal", "all", "static", "observed", "SEPA flood hazard maps", "P0", "High/medium/low coastal flood reference maps are stored in the geodatabase."),
        ("Glasgow City 1km Buffer", "Glasgow_City_1km_buffer.shp", None, None, "static", "observed", "Local HydroMind polygon input", "P0", "Study-area boundary for Glasgow station discovery."),
    ]
    for dataset, layer, flood_type, temporal_state, evidence_type, dtype, source, priority, role in static_layers:
        tif_names = {
            "DTM_5m_res": "DTM_5m_res.tif",
            "Slope_degrees_DTM_5m": "Slope_degrees_DTM_5m.tif",
            "FlowAcc_DTM_5m": "FlowAcc_DTM_5m.tif",
            "UKCEH_Landcover_5m_res": "UKCEH_Landcover_5m_res.tif",
            "OS_Built_Up_Areas_5m_res": "OS_Built_Up_Areas_5m_res.tif",
            "OS_Rivers_5m_res": "OS_Rivers_5m_res.tif",
            "SEPA_River_High_Flood_5m_res": "SEPA_River_High_Flood_5m_.tif",
            "SEPA_Coastal_High_Flood_5m_res": "SEPA_Coastal_High_Flood_5.tif",
        }
        if layer.endswith(".shp"):
            local_path = polygons_dir / layer
            available = local_path.exists()
        else:
            tif_path = tif_dir / tif_names.get(layer, f"{layer}.tif")
            local_path = gdb_path if layer in gdb_layers else tif_path
            available = layer in gdb_layers or tif_path.exists()
            if not available and layer in {"Slope_degrees_DTM_5m", "FlowAcc_DTM_5m"}:
                dem_path = tif_dir / "DTM_5m_res.tif"
                if dem_path.exists():
                    local_path = dem_path
                    available = True
                    dtype = "derived proxy"
                    source = "Derived from the downloaded DEM at analysis time"
        records.append(
            DataAvailabilityRecord(
                dataset=dataset,
                category="hazard" if dataset != "Glasgow City 1km Buffer" else "study_area",
                flood_type=flood_type,
                temporal_state=temporal_state,
                evidence_type=evidence_type,
                status="available" if available else "unavailable",
                type=dtype,
                source=source if available else None,
                local_path=str(local_path) if available else None,
                crs="EPSG:27700" if available else None,
                spatial_resolution="5m raster" if available and not layer.endswith(".shp") else ("polygon boundary" if available else None),
                temporal_resolution="static",
                reason_if_unavailable=None if available else f"{layer} was not found in the current Input folder.",
                priority=priority,
                analytical_role=role,
            )
        )

    dynamic_records = [
        DataAvailabilityRecord("SEPA current rainfall", "hazard", "pluvial", "current", "dynamic", "available", "observed", "SEPA Rainfall API", None, None, "station observations gridded to DEM", "latest observation", None, "P0", "Current pluvial rainfall forcing", "https://www2.sepa.org.uk/Rainfall/"),
        DataAvailabilityRecord("SEPA current river/tidal level", "hazard", "fluvial,coastal", "current", "dynamic", "available", "observed", "SEPA KiWIS Time Series API", None, None, "station observations gridded to DEM", "latest observation", None, "P0", "Current level evidence where a station is discovered in the study area.", "https://timeseries.sepa.org.uk/KiWIS/KiWIS"),
        DataAvailabilityRecord(
            "Met Office rainfall forecast",
            "hazard",
            "pluvial,fluvial",
            "future",
            "dynamic",
            "available" if _metoffice_credentials_configured() else "unavailable",
            "forecast",
            "Met Office SiteSpecificForecast" if _metoffice_credentials_configured() else None,
            None,
            None,
            "point forecast samples gridded to DEM",
            "forecast horizon",
            None if _metoffice_credentials_configured() else "METOFFICE_SITE_API_KEY is not configured.",
            "P0",
            "Future rainfall forcing.",
            "https://datahub.metoffice.gov.uk/",
        ),
        DataAvailabilityRecord("EA Tide Gauge historical observations", "hazard", "coastal", "historical", "dynamic", "available", "observed", "Environment Agency Tide Gauge API", None, None, "station time series", "15 minute readings where available", None, "P0", "Historical coastal tide observations; station-level evidence only, not raster forcing.", "https://environment.data.gov.uk/flood-monitoring/doc/tidegauge"),
        DataAvailabilityRecord("EA Tide Gauge current observation", "hazard", "coastal", "current", "dynamic", "available", "observed", "Environment Agency Tide Gauge API", None, None, "station latest reading", "latest reading", None, "P0", "Current coastal tide observation; station-level evidence only, not raster forcing.", "https://environment.data.gov.uk/flood-monitoring/doc/tidegauge"),
        DataAvailabilityRecord("EA Flood Monitoring current evidence", "hazard", "coastal", "current", "dynamic", "available", "observed", "Environment Agency Flood Monitoring API", None, None, "flood warning/alert areas and operational records", "current", None, "P0", "Supplementary coastal operational evidence; warning/alert semantics remain separate from water level.", "https://environment.data.gov.uk/flood-monitoring/doc/reference"),
        DataAvailabilityRecord(
            "ADMIRALTY tidal prediction",
            "hazard",
            "coastal",
            "future",
            "dynamic",
            "available" if _admiralty_credentials_configured() else "unavailable",
            "forecast",
            "ADMIRALTY Tidal API" if _admiralty_credentials_configured() else None,
            None,
            None,
            "station tidal prediction",
            "forecast/prediction",
            None if _admiralty_credentials_configured() else "credentials_not_configured",
            "P0",
            "Future predicted tidal height; not storm surge, coastal flood forecast, or flood extent forecast.",
            "https://developer.admiralty.co.uk/",
        ),
    ]
    records.extend(dynamic_records)

    flow_zip = csv_dir / "Gauged_daily_flow.zip"
    rainfall_zip = csv_dir / "Rainfall.zip"
    records.extend(
        [
            DataAvailabilityRecord("NRFA historical river flow", "hazard", "fluvial", "historical", "dynamic", "available" if flow_zip.exists() else "unavailable", "observed", "National River Flow Archive" if flow_zip.exists() else None, str(flow_zip) if flow_zip.exists() else None, None, "station/catchment daily time series", "daily", None if flow_zip.exists() else "Gauged_daily_flow.zip is not present.", "P1", "Historical fluvial dynamic evidence; not used in current/future formula without calibration.", "https://nrfa.ceh.ac.uk/data/search"),
            DataAvailabilityRecord("NRFA historical rainfall", "hazard", "pluvial,fluvial", "historical", "dynamic", "available" if rainfall_zip.exists() else "unavailable", "observed", "National River Flow Archive" if rainfall_zip.exists() else None, str(rainfall_zip) if rainfall_zip.exists() else None, None, "station/catchment daily time series", "daily", None if rainfall_zip.exists() else "Rainfall.zip is not present.", "P1", "Historical rainfall evidence; not used in current/future formula without calibration.", "https://nrfa.ceh.ac.uk/data/search"),
        ]
    )

    records.extend(_exposure_and_vulnerability_records(input_dir))
    records.extend(_known_unavailable_p2_records())
    return [record.to_dict() for record in records]


def write_core_data_registry(input_dir: str | Path, output_path: str | Path) -> dict[str, Any]:
    output_path = Path(output_path)
    records = build_core_data_registry(input_dir)
    summary = {
        "status_counts": _status_counts(records),
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"path": str(output_path), **summary}


def _exposure_and_vulnerability_records(input_dir: Path) -> list[DataAvailabilityRecord]:
    from core_analyst.real_data_inputs import find_data_zone_boundary, find_simd_source

    processed_data_zone_attributes = input_dir / "processed" / "data_zone" / "data_zone_attributes.csv"
    processed_buildings = (
        input_dir / "processed" / "exposure" / "os_openmap_buildings_glasgow_1km_buffer.geojson"
    )
    processed_simd = input_dir / "processed" / "simd" / "simd_2020v2_indicators.csv"
    data_zone_boundary = find_data_zone_boundary(input_dir)
    simd_source = processed_simd if processed_simd.exists() else find_simd_source(input_dir)
    enriched_data_zones = input_dir / "processed" / "data_zone" / "glasgow_data_zones_2022_enriched_simd.geojson"
    if not _path_has_records(enriched_data_zones):
        enriched_data_zones = input_dir / "processed" / "data_zone" / "glasgow_data_zones_2022_enriched.geojson"
    candidates = {
        "population": enriched_data_zones if _path_has_records(enriched_data_zones) else data_zone_boundary,
        "population_attributes": processed_data_zone_attributes if processed_data_zone_attributes.exists() else None,
        "buildings": processed_buildings if processed_buildings.exists() else _first_existing(input_dir, ["NS_Building.shp", "buildings.geojson", "buildings.json", "buildings.gpkg", "buildings.shp"]),
        "critical_infrastructure": _first_existing(input_dir, ["processed/facilities/critical_services.geojson", "critical_infrastructure.geojson", "critical_infrastructure.json", "critical_infrastructure.gpkg", "critical_infrastructure.shp"]),
        "vulnerability_geography": enriched_data_zones if _path_has_records(enriched_data_zones) else data_zone_boundary,
        "socioeconomic": enriched_data_zones if _path_has_records(enriched_data_zones) else simd_source,
        "critical_services": _first_existing(input_dir, ["processed/facilities/critical_services.geojson", "critical_services.geojson", "critical_services.shp"]),
    }
    official = {
        "population": "National Records of Scotland / Scotland Census 2022 / Glasgow City Council open data",
        "buildings": "Ordnance Survey OpenMap Local buildings or Glasgow City Council open building footprints",
        "critical_infrastructure": "Local authority or authoritative asset records",
        "vulnerability_geography": "National Records of Scotland / Scotland Census 2022 data zones",
        "socioeconomic": "Scottish Government SIMD",
        "critical_services": "NHS/local authority/emergency-service official facility records",
    }
    urls = {
        "population": "https://www.scotlandscensus.gov.uk/",
        "buildings": "https://osdatahub.os.uk/downloads/open/OpenMapLocal",
        "critical_infrastructure": "https://www.opendata.nhs.scot/dataset/hospital-codes",
        "vulnerability_geography": "https://www.scotlandscensus.gov.uk/",
        "socioeconomic": "https://www.gov.scot/collections/scottish-index-of-multiple-deprivation-2020/",
        "critical_services": "https://www.nrscotland.gov.uk/publications/scottish-postcode-directory-20262/",
    }
    records = [
        ("population", "exposure", None, None, "static", "observed", "P0", "Population exposure source; must contain population counts by spatial unit or raster cell."),
        ("buildings", "exposure", None, None, "static", "observed", "P0", "Building footprints or other real building exposure geometry."),
        ("critical_infrastructure", "exposure", None, None, "static", "observed", "P1", "Critical infrastructure exposure; allowed unavailable for MVP."),
        ("vulnerability_geography", "vulnerability", None, None, "static", "observed", "P0", "Base statistical geography carrying elderly_prop where available."),
        ("socioeconomic", "vulnerability", None, None, "static", "observed", "P0", "SIMD/Census attributes carrying deprivation_score and no_car_household_prop where available."),
        ("critical_services", "vulnerability", None, None, "static", "observed", "P1", "Critical service points for accessibility dimension."),
    ]
    output: list[DataAvailabilityRecord] = []
    for key, category, flood_type, temporal_state, evidence_type, dtype, priority, role in records:
        path = candidates[key]
        record_count = _path_record_count(path) if path else None
        if path and record_count == 0:
            path = None
            record_count = 0
        status = "available" if path else "unavailable"
        reason = None if path else "No verified local file was found in Input; do not fabricate values."
        source = official[key] if path else None
        spatial_resolution = "vector spatial unit" if path else None
        if key == "population" and not path and candidates["population_attributes"]:
            path = candidates["population_attributes"]
            status = "partial"
            source = official[key]
            spatial_resolution = "Data Zone 2022 tabular attributes; geometry unavailable"
            reason = (
                "Census Data Zone population attributes are available, but no local Data Zone 2022 "
                "polygon boundary was found for spatial exposure."
            )
        if key == "socioeconomic" and path and not candidates["vulnerability_geography"]:
            status = "partial"
            source = official[key]
            spatial_resolution = "SIMD 2020v2 tabular indicators; native 2011 Data Zone"
            reason = (
                "SIMD 2020v2 indicators are available, but they are native to 2011 Data Zones. "
                "A verified 2011-to-2022 Data Zone crosswalk and Data Zone 2022 geometry are required "
                "for the current vulnerability geography."
            )
        if key == "socioeconomic" and not path and candidates["population_attributes"]:
            path = candidates["population_attributes"]
            status = "partial"
            source = "Scotland Census 2022 Data Zone attributes"
            spatial_resolution = "Data Zone 2022 tabular attributes; SIMD unavailable"
            reason = (
                "Census no-car household attributes are available, but SIMD 2020v2 deprivation "
                "data was not found in Input."
            )
        output.append(
            DataAvailabilityRecord(
                dataset=key,
                category=category,
                flood_type=flood_type,
                temporal_state=temporal_state,
                evidence_type=evidence_type,
                status=status,
                type=dtype,
                source=source,
                local_path=str(path) if path else None,
                crs=None,
                spatial_resolution=spatial_resolution,
                temporal_resolution="static/release-based",
                reason_if_unavailable=reason,
                priority=priority,
                analytical_role=role,
                official_source_url=urls[key],
                record_count=record_count,
            )
        )
    field_paths = {
        "elderly_prop": candidates["population_attributes"],
        "deprivation_score": candidates["socioeconomic"],
        "no_car_household_prop": candidates["population_attributes"],
        "children_prop": None,
        "single_person_household_prop": None,
        "unemployment_prop": None,
    }
    field_reasons = {
        "elderly_prop": "Census Data Zone tabular field is available; spatial use still requires Data Zone geometry.",
        "deprivation_score": (
            "SIMD 2020v2 indicators are available, but native to 2011 Data Zones; a verified "
            "2011-to-2022 Data Zone crosswalk and Data Zone 2022 geometry are required for spatial use."
        ),
        "no_car_household_prop": "Census Data Zone tabular field is available; spatial use still requires Data Zone geometry.",
        "children_prop": "Optional indicator is not prepared from the current real-data integration.",
        "single_person_household_prop": "Optional indicator is not prepared from the current real-data integration.",
        "unemployment_prop": "Optional indicator requires a verified unemployment source; no local source was found.",
    }
    for field, source_key, priority in [
        ("elderly_prop", "vulnerability_geography", "P0"),
        ("deprivation_score", "socioeconomic", "P0"),
        ("no_car_household_prop", "socioeconomic", "P0"),
        ("children_prop", "vulnerability_geography", "P1"),
        ("single_person_household_prop", "vulnerability_geography", "P1"),
        ("unemployment_prop", "socioeconomic", "P1"),
    ]:
        path = field_paths[field]
        status = "available" if path and candidates["vulnerability_geography"] else "unavailable"
        if path and not candidates["vulnerability_geography"]:
            status = "partial"
        output.append(
            DataAvailabilityRecord(
                dataset=field,
                category="vulnerability",
                flood_type=None,
                temporal_state=None,
                evidence_type="static",
                status=status,
                type="observed",
                source=None if not path else (
                    official[source_key] if source_key != "socioeconomic" else (
                        official[source_key] if field == "deprivation_score" else "Scotland Census 2022 Data Zone attributes"
                    )
                ),
                local_path=str(path) if path else None,
                temporal_resolution="static/release-based",
                reason_if_unavailable=None if status == "available" else field_reasons[field],
                priority=priority,
                analytical_role=f"Vulnerability indicator field {field}.",
                official_source_url=urls[source_key],
            )
        )
    return output


def _path_has_records(path: Path | None) -> bool:
    return bool(path and path.is_file() and (_path_record_count(path) or 0) > 0)


def _path_record_count(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix in {".geojson", ".json"}:
        return len(json.loads(path.read_text(encoding="utf-8")).get("features", []))
    if suffix == ".csv":
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            return max(sum(1 for _ in csv.reader(handle)) - 1, 0)
    if suffix == ".shp":
        import shapefile

        with shapefile.Reader(str(path)) as reader:
            return len(reader)
    return None


def _known_unavailable_p2_records() -> list[DataAvailabilityRecord]:
    items = [
        ("radar_rainfall", "pluvial", "current", "dynamic", "observed"),
        ("drainage_network", "pluvial", "all", "static", "observed"),
        ("drainage_capacity", "pluvial", "all", "static", "observed"),
        ("river_forecast", "fluvial", "future", "dynamic", "forecast"),
        ("storm_surge_observation", "coastal", "current", "dynamic", "observed"),
        ("storm_surge_forecast", "coastal", "future", "dynamic", "forecast"),
        ("tide_forecast", "coastal", "future", "dynamic", "forecast"),
        ("coastal_defence", "coastal", "all", "static", "observed"),
        ("wave_exposure", "coastal", "all", "dynamic", "observed"),
        ("historical_coastal_forcing", "coastal", "historical", "dynamic", "observed"),
        ("future_population_projection", None, "future", "static", "forecast"),
        ("future_building_projection", None, "future", "static", "forecast"),
    ]
    return [
        DataAvailabilityRecord(
            dataset=name,
            category="hazard" if flood_type else "exposure",
            flood_type=flood_type,
            temporal_state=temporal_state,
            evidence_type=evidence_type,
            status="unavailable",
            type=dtype,
            source=None,
            temporal_resolution=temporal_state,
            reason_if_unavailable="Allowed unavailable for the current MVP; no unverified proxy should be substituted.",
            priority="P2",
        )
        for name, flood_type, temporal_state, evidence_type, dtype in items
    ]


def _gdb_layers(gdb_path: Path) -> set[str]:
    if not gdb_path.exists():
        return set()
    try:
        with rasterio.open(gdb_path) as dataset:
            return {Path(item.split(":")[-1]).name for item in dataset.subdatasets}
    except Exception:
        return set()


def _first_existing(root: Path, names: list[str]) -> Path | None:
    for name in names:
        matches = list(root.rglob(name))
        if matches:
            return matches[0]
    return None


def _status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return counts


def _admiralty_credentials_configured() -> bool:
    import os

    return any(os.getenv(key, "").strip() for key in ADMIRALTY_ENV_KEYS)


def _metoffice_credentials_configured() -> bool:
    import os

    return bool(os.getenv("METOFFICE_SITE_API_KEY", "").strip())
