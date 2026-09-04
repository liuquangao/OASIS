"""Acquire and build the source-faithful Glasgow 5 m analysis inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import httpx
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject
import shapefile
from shapely.geometry import mapping, shape

from core_analyst.input_layout import (
    datazone2022_dir,
    datazone_boundaries2011_dir,
    nrfa_flow_source,
    nrfa_rainfall_source,
    polygon_dir,
    raster_dir,
    simd_source,
)
from core_analyst.official_facilities import prepare_official_facilities

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_LOCK_PATH = PROJECT_ROOT / "data" / "glasgow-5m-sources.json"
STUDY_AREA_PATH = PROJECT_ROOT / "data" / "glasgow-city-1km-buffer.geojson"
DTM_EDGE_PATCH_PATH = PROJECT_ROOT / "data" / "glasgow-dtm-edge-patch.csv"
NRFA_ROOT = "CSV-20260825T012052Z-1-001"
SEPA_LAYERS = {
    "SEPA_River_High_Flood_5m_.tif": 0,
    "SEPA_River_Medium_Flood_5.tif": 1,
    "SEPA_River_Low_Flood_5m_r.tif": 2,
    "SEPA_Coastal_High_Flood_5.tif": 6,
    "SEPA_Coastal_Medium_Flood.tif": 7,
    "SEPA_Coastal_Low_Flood_5m.tif": 8,
}


@dataclass(frozen=True)
class ExactDataResult:
    status: str
    profile: str
    input_dir: str
    cache_dir: str
    manifest: str | None
    downloaded_bytes: int
    generated_files: list[str]
    notes: list[str]


def prepare_risk_inputs(
    input_dir: str | Path,
    *,
    cache_dir: str | Path | None = None,
    force: bool = False,
    user_agent: str = "hydromind/0.1",
) -> dict:
    """Download locked social-risk sources and build deterministic adapters."""

    lock = load_source_lock()
    input_dir = Path(input_dir).resolve()
    cache_dir = Path(cache_dir).resolve() if cache_dir else input_dir.parent / ".hydromind-data-cache"
    input_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(600.0),
        headers={"User-Agent": user_agent},
    ) as client:
        downloaded, raw = _download_risk_sources(client, input_dir, cache_dir, lock, force)
    facilities_dir = input_dir / "processed" / "facilities"
    facility_result = prepare_official_facilities(
        postcode_directory=raw["postcode_grid_references"],
        facility_sources={name: raw[name] for name in ("hospital", "school", "care_home", "emergency_service")},
        study_area=STUDY_AREA_PATH,
        output_path=facilities_dir / "critical_services.geojson",
        quality_report_path=facilities_dir / "facility_data_quality.json",
    )
    from core_analyst.real_data_inputs import prepare_real_exposure_vulnerability_inputs

    prepared = prepare_real_exposure_vulnerability_inputs(input_dir)
    return {
        "status": "success" if not prepared.unavailable and facility_result["feature_count"] else "partial",
        "downloaded_bytes": downloaded,
        "prepared": asdict(prepared),
        "facilities": facility_result,
        "source_lock": str(SOURCE_LOCK_PATH),
    }


def load_source_lock() -> dict:
    return json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8"))


def _edge_patch_error(patch: dict) -> str | None:
    """Explain why the versioned DTM edge patch disagrees with the source lock."""

    raw = DTM_EDGE_PATCH_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() == patch["sha256"]:
        return None
    if b"\r\n" in raw and hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == patch["sha256"]:
        return (
            f"{DTM_EDGE_PATCH_PATH} was checked out with CRLF line endings, so its "
            "SHA-256 no longer matches the source lock. Git for Windows enables "
            "core.autocrlf by default; this repository ships a .gitattributes that "
            "prevents the rewrite. Restore the file with: "
            "git config core.autocrlf false && git rm --cached -r . && git reset --hard"
        )
    return (
        f"{DTM_EDGE_PATCH_PATH} does not match the source lock "
        f"(expected sha256 {patch['sha256']}, found {hashlib.sha256(raw).hexdigest()}). "
        "Restore it from Git before rebuilding."
    )


def preflight_exact_data(
    lcm2019: str | Path | None,
    *,
    accept_licences: bool,
) -> dict:
    lock = load_source_lock()
    item = lock["sources"]["land_cover"]
    errors = []
    patch = lock["legacy_edge_patch"]
    patch_error = _edge_patch_error(patch)
    if patch_error:
        errors.append(patch_error)
    if not accept_licences:
        errors.append("Pass --accept-licences after reading the UKCEH LCM and NRFA terms.")
    lcm_path = _find_lcm(Path(lcm2019)) if lcm2019 else None
    if lcm_path is None:
        errors.append(
            "Download UKCEH LCM 2019 (25 m rasterised land parcels, GB) and pass "
            f"--lcm2019 PATH. Required file: {item['required_filename']}; order: {item['order_url']}"
        )
    return {
        "ok": not errors,
        "profile": lock["profile"],
        "lcm2019": str(lcm_path) if lcm_path else None,
        "errors": errors,
        "download_bytes": sum(
            value[0]
            for group in ("tiles", "supplemental_tiles")
            for value in lock["sources"]["terrain"][group].values()
        ),
        "api_keys_required_for_data_build": [],
    }


def rebuild_glasgow_5m(
    input_dir: str | Path,
    *,
    lcm2019: str | Path,
    cache_dir: str | Path | None = None,
    accept_licences: bool = False,
    force: bool = False,
    user_agent: str = "hydromind/0.1",
) -> ExactDataResult:
    check = preflight_exact_data(lcm2019, accept_licences=accept_licences)
    if not check["ok"]:
        raise ValueError("\n".join(check["errors"]))

    lock = load_source_lock()
    input_dir = Path(input_dir).resolve()
    cache_dir = Path(cache_dir).resolve() if cache_dir else input_dir.parent / ".hydromind-data-cache"
    input_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    lcm_path = Path(check["lcm2019"])
    downloaded = 0

    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(600.0),
        headers={"User-Agent": user_agent},
    ) as client:
        lidar, lidar_bytes = _download_lidar(client, cache_dir, lock, force)
        downloaded += lidar_bytes
        os_roots, os_bytes = _download_os(client, cache_dir, lock, force)
        downloaded += os_bytes
        sepa, sepa_bytes = _download_sepa(client, cache_dir, lock, force)
        downloaded += sepa_bytes
        national_bytes = _download_national(client, input_dir, cache_dir, lock, force)
        downloaded += national_bytes
        risk_bytes, _ = _download_risk_sources(client, input_dir, cache_dir, lock, force)
        downloaded += risk_bytes
        nrfa_bytes = _download_nrfa(client, input_dir, lock, force)
        downloaded += nrfa_bytes

    _write_study_area(input_dir)
    _install_os_buildings(os_roots["OpenMapLocal"], input_dir)
    generated = _build_rasters(input_dir, lidar, lcm_path, os_roots, sepa, lock)

    risk_raw = _risk_source_paths(cache_dir)
    facilities_dir = input_dir / "processed" / "facilities"
    prepare_official_facilities(
        postcode_directory=risk_raw["postcode_grid_references"],
        facility_sources={name: risk_raw[name] for name in ("hospital", "school", "care_home", "emergency_service")},
        study_area=STUDY_AREA_PATH,
        output_path=facilities_dir / "critical_services.geojson",
        quality_report_path=facilities_dir / "facility_data_quality.json",
    )

    from core_analyst.real_data_inputs import prepare_real_exposure_vulnerability_inputs

    prepared = prepare_real_exposure_vulnerability_inputs(input_dir)
    generated.extend(
        Path(value)
        for key, value in asdict(prepared).items()
        if key != "unavailable" and value is not None and Path(value).exists()
    )

    sources = [
        _file_record("downloaded official source", path, base=cache_dir)
        for path in sorted(cache_dir.rglob("*"))
        if path.is_file() and path.suffix != ".part"
    ]
    sources.append(_file_record("UKCEH Land Cover Map 2019", lcm_path))
    sources.append(_file_record("legacy DTM edge compatibility patch", DTM_EDGE_PATCH_PATH))
    nrfa_dir = input_dir / NRFA_ROOT / "CSV"
    sources.extend(
        _file_record("National River Flow Archive", path, base=input_dir)
        for path in sorted(nrfa_dir.glob("*.zip"))
    )

    manifest_path = input_dir / "REPRODUCIBILITY_MANIFEST.json"
    manifest = {
        "schema_version": 1,
        "profile": lock["profile"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_lock": str(SOURCE_LOCK_PATH),
        "analysis_grid": lock["analysis_grid"],
        "sources": sources,
        "outputs": [_file_record("generated", Path(path), base=input_dir) for path in generated],
        "licence_note": (
            "UKCEH LCM 2019 and NRFA raw data remain local because their terms do not permit "
            "redistribution in a public repository. Every user obtains them from the official provider."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = verify_glasgow_5m(input_dir)
    if not report["ok"]:
        raise ValueError("Generated data failed verification: " + "; ".join(report["errors"]))
    return ExactDataResult(
        status="success",
        profile=lock["profile"],
        input_dir=str(input_dir),
        cache_dir=str(cache_dir),
        manifest=str(manifest_path),
        downloaded_bytes=downloaded,
        generated_files=[str(path) for path in generated],
        notes=["All raster outputs use the locked Glasgow EPSG:27700 5 m grid."],
    )


def verify_glasgow_5m(input_dir: str | Path) -> dict:
    input_dir = Path(input_dir)
    lock = load_source_lock()
    grid = lock["analysis_grid"]
    raster_root = raster_dir(input_dir)
    names = [
        "DTM_5m_res.tif",
        "Slope_degrees_DTM_5m.tif",
        "FlowAcc_DTM_5m.tif",
        "UKCEH_Landcover_5m_res.tif",
        "OS_Built_Up_Areas_5m_res.tif",
        "OS_Greenspace_5m_res.tif",
        "OS_Rivers_5m_res.tif",
        *SEPA_LAYERS,
        "SEPA_Combined_High_Flood_5m.tif",
        "SEPA_Combined_Medium_Flood_5m.tif",
        "SEPA_Combined_Low_Flood_5m.tif",
    ]
    errors = []
    checked = []
    expected_transform = from_origin(grid["bounds"][0], grid["bounds"][3], 5.0, 5.0)
    for name in names:
        path = raster_root / name
        if not path.exists():
            errors.append(f"missing {path}")
            continue
        with rasterio.open(path) as dataset:
            if dataset.crs != rasterio.crs.CRS.from_epsg(27700):
                errors.append(f"{name}: CRS is {dataset.crs}, expected EPSG:27700")
            if (dataset.width, dataset.height) != (grid["width"], grid["height"]):
                errors.append(f"{name}: shape is {dataset.width}x{dataset.height}")
            if not dataset.transform.almost_equals(expected_transform):
                errors.append(f"{name}: grid transform does not match the locked grid")
        checked.append(str(path))
    required = [
        datazone2022_dir(input_dir) / "Table UV102b - Age (20) by sex.csv",
        datazone2022_dir(input_dir) / "Table UV405 - Car or van availability.csv",
        simd_source(input_dir),
        datazone_boundaries2011_dir(input_dir) / "SG_DataZone_Bdry_2011.shp",
        input_dir / "processed" / "data_zone" / "glasgow_data_zones_2022_enriched_simd.geojson",
        input_dir / "processed" / "facilities" / "critical_services.geojson",
        input_dir / "processed" / "facilities" / "facility_data_quality.json",
        nrfa_flow_source(input_dir),
        nrfa_rainfall_source(input_dir),
        polygon_dir(input_dir) / "Glasgow_City_1km_buffer.shp",
    ]
    for path in required:
        if not path.exists():
            errors.append(f"missing {path}")
    for path, minimum in (
        (input_dir / "processed" / "data_zone" / "glasgow_data_zones_2022_enriched_simd.geojson", 1),
        (input_dir / "processed" / "facilities" / "critical_services.geojson", 4),
    ):
        if path.exists():
            feature_count = len(json.loads(path.read_text(encoding="utf-8")).get("features", []))
            if feature_count < minimum:
                errors.append(f"{path}: only {feature_count} features")
    return {"ok": not errors, "profile": lock["profile"], "checked": checked, "errors": errors}


def _download_lidar(client: httpx.Client, cache: Path, lock: dict, force: bool) -> tuple[list[Path], int]:
    spec = lock["sources"]["terrain"]
    root = cache / "lidar-phase-5"
    paths = []
    downloaded = 0
    for key, (size, etag) in spec["supplemental_tiles"].items():
        path = root / Path(key).name
        downloaded += _download(
            client, spec["base_url"] + key, path, force=force,
            expected_size=size, expected_etag=etag,
        )
        paths.append(path)
    for tile, (size, etag) in spec["tiles"].items():
        name = f"{tile}_50CM_DTM_PHASE5.tif"
        path = root / name
        url = spec["base_url"] + spec["prefix"] + name
        downloaded += _download(
            client, url, path, force=force, expected_size=size, expected_etag=etag,
        )
        paths.append(path)
    return paths, downloaded


def _download_os(client: httpx.Client, cache: Path, lock: dict, force: bool) -> tuple[dict[str, Path], int]:
    api = lock["sources"]["os"]["api"]
    roots = {}
    downloaded = 0
    for product, wanted in lock["sources"]["os"]["products"].items():
        entries = client.get(f"{api}/{product}/downloads").json()
        entry = next(x for x in entries if x["area"] == wanted["area"] and x["format"] == wanted["format"])
        if entry["md5"] != wanted["md5"] or entry["size"] != wanted["bytes"]:
            raise ValueError(
                f"OS {product} no longer matches locked version {wanted['version']}. "
                "Update the source lock only after reviewing the new release."
            )
        archive = cache / "os" / entry["fileName"]
        downloaded += _download(client, entry["url"], archive, force=force, expected_size=wanted["bytes"])
        if _hash(archive, "md5") != entry["md5"]:
            raise ValueError(f"OS checksum mismatch: {archive}")
        root = cache / "os" / product
        if force or not root.exists():
            if root.exists():
                shutil.rmtree(root)
            _extract_zip(archive, root)
        roots[product] = root
    return roots, downloaded


def _download_sepa(client: httpx.Client, cache: Path, lock: dict, force: bool) -> tuple[dict[str, Path], int]:
    bounds = lock["analysis_grid"]["bounds"]
    query_url = lock["sources"]["sepa"]["query_url"]
    root = cache / "sepa"
    result = {}
    downloaded = 0
    for name, layer in SEPA_LAYERS.items():
        path = root / name.replace(".tif", ".geojson")
        if force or not path.exists():
            features = []
            offset = 0
            while True:
                response = client.get(
                    query_url.format(layer=layer),
                    params={
                        "where": "1=1", "geometry": ",".join(map(str, bounds)),
                        "geometryType": "esriGeometryEnvelope", "inSR": "27700", "outSR": "27700",
                        "spatialRel": "esriSpatialRelIntersects", "outFields": "OBJECTID",
                        "returnGeometry": "true", "resultOffset": offset, "resultRecordCount": 1000, "f": "geojson",
                    },
                )
                response.raise_for_status()
                batch = response.json()["features"]
                features.extend(batch)
                if len(batch) < 1000:
                    break
                offset += len(batch)
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
            path.write_text(raw, encoding="utf-8")
            downloaded += len(raw.encode("utf-8"))
        result[name] = path
    return result, downloaded


def _download_national(client: httpx.Client, input_dir: Path, cache: Path, lock: dict, force: bool) -> int:
    sources = lock["sources"]
    total = 0
    census_zip = cache / "national" / "Datazone2022.zip"
    total += _download(client, sources["census_2022"]["url"], census_zip, force=force, expected_size=sources["census_2022"]["bytes"])
    census_dir = input_dir / "Datazone2022"
    if force or not census_dir.exists():
        if census_dir.exists():
            shutil.rmtree(census_dir)
        _extract_zip(census_zip, census_dir)
        _flatten_single_directory(census_dir)

    simd = input_dir / "SIMD+2020v2+-+indicators.xlsx"
    total += _download(client, sources["simd_2020v2"]["url"], simd, force=force, expected_size=sources["simd_2020v2"]["bytes"])

    dz_zip = cache / "national" / "SG_DataZoneBdry_2022.zip"
    total += _download(
        client, sources["data_zone_boundaries_2022"]["url"], dz_zip, force=force,
        expected_size=sources["data_zone_boundaries_2022"]["bytes"],
        headers={"Referer": "https://www.data.gov.uk/", "User-Agent": "Mozilla/5.0"},
    )
    dz_root = cache / "national" / "data-zone-boundaries"
    if force or not dz_root.exists():
        if dz_root.exists():
            shutil.rmtree(dz_root)
        _extract_zip(dz_zip, dz_root)
    _clip_data_zones(dz_root, input_dir / "DataZoneBoundaries2022" / "glasgow_data_zones_2022.geojson")

    lookup_zip = cache / "national" / "oa22_dz22_iz22.zip"
    total += _download(client, sources["oa_dz_lookup_2022"]["url"], lookup_zip, force=force, expected_size=sources["oa_dz_lookup_2022"]["bytes"])
    lookup_root = input_dir / "Output Area 2022 to Data Zone 2022 and Intermediate Zone 2022"
    if force or not lookup_root.exists():
        if lookup_root.exists():
            shutil.rmtree(lookup_root)
        _extract_zip(lookup_zip, lookup_root)
        _rename_first_csv(lookup_root, "DZ22_Lookup.csv")

    population = input_dir / "Output Area 2022 Total Population.csv"
    total += _download(client, sources["oa_population_2022"]["url"], population, force=force, expected_size=sources["oa_population_2022"]["bytes"])
    return total


def _download_risk_sources(
    client: httpx.Client,
    input_dir: Path,
    cache: Path,
    lock: dict,
    force: bool,
) -> tuple[int, dict[str, Path]]:
    total = 0
    data_zone_spec = lock["sources"]["data_zone_boundaries_2011"]
    data_zone_archive = cache / "national" / "SG_DataZoneBdry_2011.zip"
    total += _download(
        client,
        data_zone_spec["url"],
        data_zone_archive,
        force=force,
        expected_size=data_zone_spec["bytes"],
        expected_sha256=data_zone_spec["sha256"],
        headers={"Referer": "https://www.data.gov.uk/", "User-Agent": "Mozilla/5.0"},
    )
    data_zone_root = input_dir / "DataZoneBoundaries2011"
    if force or not data_zone_root.exists():
        if data_zone_root.exists():
            shutil.rmtree(data_zone_root)
        _extract_zip(data_zone_archive, data_zone_root)

    paths = _risk_source_paths(cache)
    for name, spec in lock["sources"]["official_facilities"].items():
        total += _download(
            client,
            spec["url"],
            paths[name],
            force=force,
            expected_size=spec["bytes"],
            expected_sha256=spec["sha256"],
        )
    return total, paths


def _risk_source_paths(cache: Path) -> dict[str, Path]:
    root = cache / "official-risk"
    return {
        "postcode_grid_references": root / "spd_postcode_index_26_2.zip",
        "hospital": root / "hospitals_2026-08.csv",
        "school": root / "schools_2026-01.xlsx",
        "care_home": root / "care_inspectorate_2026-07.csv",
        "emergency_service": root / "fire_stations_2025-26.xlsx",
    }


def _download_nrfa(client: httpx.Client, input_dir: Path, lock: dict, force: bool) -> int:
    root = input_dir / NRFA_ROOT / "CSV"
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for filename, data_type, suffix in (
        ("Gauged_daily_flow.zip", "gdf", "gdf"),
        ("Rainfall.zip", "cdr", "cdr"),
    ):
        target = root / filename
        if target.exists() and not force:
            continue
        temporary = target.with_suffix(".zip.part")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for station in lock["sources"]["nrfa"]["stations"]:
                response = client.get(
                    lock["sources"]["nrfa"]["api"],
                    params={"format": "nrfa-csv", "data-type": data_type, "station": station},
                )
                response.raise_for_status()
                bundle.writestr(f"{station}_{suffix}.csv", response.content)
                total += len(response.content)
        temporary.replace(target)
    return total


def _write_study_area(input_dir: Path) -> None:
    feature = json.loads(STUDY_AREA_PATH.read_text(encoding="utf-8"))["features"][0]
    geometry = shape(feature["geometry"])
    root = input_dir / "HYDROMIND_Polygon" / "HYDROMIND_Polygon"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "Glasgow_City_1km_buffer.shp"
    with shapefile.Writer(str(target), shapeType=shapefile.POLYGON) as writer:
        writer.field("Name", "C", size=80)
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        parts = []
        for polygon in polygons:
            parts.append(list(polygon.exterior.coords))
            parts.extend(list(ring.coords) for ring in polygon.interiors)
        writer.poly(parts)
        writer.record("Glasgow City 1 km buffer")
    target.with_suffix(".prj").write_text(
        'PROJCS["OSGB_1936_British_National_Grid",GEOGCS["GCS_OSGB_1936",DATUM["D_OSGB_1936",SPHEROID["Airy_1830",6377563.396,299.3249646]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",400000],PARAMETER["False_Northing",-100000],PARAMETER["Central_Meridian",-2],PARAMETER["Scale_Factor",0.9996012717],PARAMETER["Latitude_Of_Origin",49],UNIT["Meter",1]]',
        encoding="ascii",
    )
    target.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")


def _install_os_buildings(source_root: Path, input_dir: Path) -> None:
    source = _find_shapefile(source_root, ("ns_building",))
    target_root = input_dir / "opmplc_essh_ns" / "OS OpenMap Local (ESRI Shape File) NS" / "data"
    target_root.mkdir(parents=True, exist_ok=True)
    for path in source.parent.glob(f"{source.stem}.*"):
        target = target_root / path.name
        if not target.exists() or target.stat().st_size != path.stat().st_size:
            shutil.copy2(path, target)


def _build_rasters(
    input_dir: Path,
    lidar: list[Path],
    lcm_path: Path,
    os_roots: dict[str, Path],
    sepa: dict[str, Path],
    lock: dict,
) -> list[Path]:
    grid = lock["analysis_grid"]
    transform = from_origin(grid["bounds"][0], grid["bounds"][3], 5.0, 5.0)
    shape_ = (grid["height"], grid["width"])
    boundary = shape(json.loads(STUDY_AREA_PATH.read_text(encoding="utf-8"))["features"][0]["geometry"])
    mask = rasterize([(mapping(boundary), 1)], out_shape=shape_, transform=transform, fill=0, dtype="uint8")
    root = input_dir / "HYDROMIND_Rasters" / "HYDROMIND_Rasters"
    root.mkdir(parents=True, exist_ok=True)
    generated = []

    dem = _warp(lidar, shape_, transform, "float32", 0, Resampling.nearest)
    dem[mask == 0] = 0
    _apply_dtm_edge_patch(dem)
    dtm = root / "DTM_5m_res.tif"
    _write_raster(dtm, dem, transform, nodata=0)
    generated.append(dtm)

    gy, gx = np.gradient(dem, 5.0, 5.0)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
    slope[mask == 0] = 0
    slope_path = root / "Slope_degrees_DTM_5m.tif"
    _write_raster(slope_path, slope, transform, nodata=0)
    generated.append(slope_path)

    from pysheds.grid import Grid

    # pysheds 0.5 still calls NumPy's former name for the same operation.
    np.in1d = np.isin

    hydro_grid = Grid.from_raster(str(dtm))
    hydro_dem = hydro_grid.read_raster(str(dtm))
    filled = hydro_grid.fill_pits(hydro_dem)
    filled = hydro_grid.fill_depressions(filled)
    filled = hydro_grid.resolve_flats(filled)
    flow_direction = hydro_grid.flowdir(filled)
    accumulation = np.asarray(hydro_grid.accumulation(flow_direction), dtype="float32")
    accumulation[mask == 0] = 0
    flow_path = root / "FlowAcc_DTM_5m.tif"
    _write_raster(flow_path, accumulation, transform, nodata=0)
    generated.append(flow_path)

    landcover = _warp([lcm_path], shape_, transform, "uint8", 0, Resampling.nearest)
    landcover[mask == 0] = 0
    lc_path = root / "UKCEH_Landcover_5m_res.tif"
    _write_raster(lc_path, landcover, transform, nodata=0)
    generated.append(lc_path)

    vector_specs = {
        "OS_Built_Up_Areas_5m_res.tif": (os_roots["OpenMapLocal"], ("building",), False),
        "OS_Greenspace_5m_res.tif": (os_roots["OpenGreenspace"], ("greenspace",), False),
        "OS_Rivers_5m_res.tif": (os_roots["OpenRivers"], ("watercourselink", "watercourse_link"), True),
    }
    for filename, (folder, tokens, all_touched) in vector_specs.items():
        shp = _find_shapefile(folder, tokens)
        values = _rasterize_shapefile(shp, shape_, transform, grid["bounds"], all_touched)
        path = root / filename
        _write_raster(path, values, transform, nodata=0)
        generated.append(path)

    flood_arrays = {}
    for filename, source in sepa.items():
        feature_collection = json.loads(source.read_text(encoding="utf-8"))
        values = rasterize(
            ((item["geometry"], 1) for item in feature_collection["features"]),
            out_shape=shape_, transform=transform, fill=0, dtype="uint8", all_touched=False,
        )
        path = root / filename
        _write_raster(path, values, transform, nodata=0)
        generated.append(path)
        flood_arrays[filename] = values
    for probability, river_name, coastal_name in (
        ("High", "SEPA_River_High_Flood_5m_.tif", "SEPA_Coastal_High_Flood_5.tif"),
        ("Medium", "SEPA_River_Medium_Flood_5.tif", "SEPA_Coastal_Medium_Flood.tif"),
        ("Low", "SEPA_River_Low_Flood_5m_r.tif", "SEPA_Coastal_Low_Flood_5m.tif"),
    ):
        values = np.maximum(flood_arrays[river_name], flood_arrays[coastal_name])
        path = root / f"SEPA_Combined_{probability}_Flood_5m.tif"
        _write_raster(path, values, transform, nodata=0)
        generated.append(path)
    return generated


def _warp(paths: list[Path], out_shape: tuple[int, int], transform, dtype: str, nodata, resampling) -> np.ndarray:
    destination = np.full(out_shape, nodata, dtype=dtype)
    for path in paths:
        tile = np.full(out_shape, nodata, dtype=dtype)
        with rasterio.open(path) as source:
            reproject(
                rasterio.band(source, 1), tile,
                src_transform=source.transform, src_crs=source.crs, src_nodata=source.nodata,
                dst_transform=transform, dst_crs="EPSG:27700", dst_nodata=nodata,
                resampling=resampling, init_dest_nodata=False,
            )
        valid = tile != nodata
        destination[valid] = tile[valid]
    return destination


def _apply_dtm_edge_patch(dem: np.ndarray) -> None:
    patch = np.loadtxt(DTM_EDGE_PATCH_PATH, delimiter=",", skiprows=1)
    dem[patch[:, 0].astype(int), patch[:, 1].astype(int)] = patch[:, 2]


def _rasterize_shapefile(path: Path, out_shape, transform, bounds, all_touched: bool) -> np.ndarray:
    def geometries():
        with shapefile.Reader(str(path)) as reader:
            for item in reader.iterShapes(bbox=tuple(bounds)):
                geometry = item.__geo_interface__
                if geometry:
                    yield geometry, 1
    return rasterize(
        geometries(), out_shape=out_shape, transform=transform, fill=0,
        dtype="uint8", all_touched=all_touched,
    )


def _clip_data_zones(source_root: Path, target: Path) -> None:
    shp = _find_shapefile(source_root, ("datazone", "data_zone", "dz2022"))
    boundary = shape(json.loads(STUDY_AREA_PATH.read_text(encoding="utf-8"))["features"][0]["geometry"])
    features = []
    with shapefile.Reader(str(shp)) as reader:
        fields = [field[0] for field in reader.fields[1:]]
        for item in reader.iterShapeRecords(bbox=boundary.bounds):
            geometry = shape(item.shape.__geo_interface__)
            if geometry.intersects(boundary):
                features.append({
                    "type": "Feature", "properties": dict(zip(fields, item.record)),
                    "geometry": mapping(geometry),
                })
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def _find_shapefile(root: Path, tokens: tuple[str, ...]) -> Path:
    candidates = list(root.rglob("*.shp"))
    return next(path for path in candidates if any(token in path.stem.lower() for token in tokens))


def _find_lcm(path: Path) -> Path | None:
    if path.is_file() and path.name.lower() == "gb2019lcm25m.tif":
        return path.resolve()
    if path.is_dir():
        return next(path.rglob("gb2019lcm25m.tif"), None)
    return None


def _download(
    client: httpx.Client,
    url: str,
    path: Path,
    *,
    force: bool,
    expected_size: int | None = None,
    expected_etag: str | None = None,
    expected_sha256: str | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    if path.exists() and not force:
        if expected_size is not None and path.stat().st_size != expected_size:
            raise ValueError(f"Cached file size mismatch: {path}")
        if expected_etag is not None:
            remote_etag = client.head(url, headers=headers).headers.get("etag", "").strip('"')
            if remote_etag != expected_etag:
                raise ValueError(f"Remote file ETag no longer matches the source lock: {url}")
        if expected_sha256 is not None and _hash(path, "sha256") != expected_sha256:
            raise ValueError(f"Cached file checksum mismatch: {path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        remote_etag = response.headers.get("etag", "").strip('"')
        if expected_etag is not None and remote_etag != expected_etag:
            raise ValueError(f"Remote file ETag does not match the source lock: {url}")
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    size = temporary.stat().st_size
    if expected_size is not None and size != expected_size:
        temporary.unlink()
        raise ValueError(f"Downloaded file size mismatch: {url}")
    if expected_sha256 is not None and _hash(temporary, "sha256") != expected_sha256:
        temporary.unlink()
        raise ValueError(f"Downloaded file checksum mismatch: {url}")
    temporary.replace(path)
    return size


def _extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def _flatten_single_directory(root: Path) -> None:
    children = list(root.iterdir())
    if len(children) == 1 and children[0].is_dir():
        nested = children[0]
        for item in nested.iterdir():
            shutil.move(str(item), root / item.name)
        nested.rmdir()


def _rename_first_csv(root: Path, name: str) -> None:
    target = root / name
    if not target.exists():
        next(root.rglob("*.csv")).replace(target)


def _write_raster(path: Path, values: np.ndarray, transform, *, nodata) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
        count=1, dtype=str(values.dtype), crs="EPSG:27700", transform=transform,
        nodata=nodata, compress="deflate", tiled=True, blockxsize=256, blockysize=256,
    ) as dataset:
        dataset.write(values, 1)


def _file_record(dataset: str, path: Path, base: Path | None = None) -> dict:
    return {
        "dataset": dataset,
        "path": str(path.relative_to(base)) if base else str(path),
        "bytes": path.stat().st_size,
        "sha256": _hash(path, "sha256"),
    }


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def result_dict(result: ExactDataResult) -> dict:
    return asdict(result)
