"""Build the Glasgow analysis inputs from redistributable public data services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from math import ceil, floor
from pathlib import Path
import shutil
import zipfile

import httpx
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds


GLASGOW_ANALYSIS_BOUNDS = (249424.0, 655461.5, 271434.0, 674071.5)
COPERNICUS_DEM_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "{tile}/{tile}.tif"
)
WORLD_COVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
    "v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)
SEPA_FLOOD_QUERY_URL = (
    "https://map.sepa.org.uk/server/rest/services/Open/Flood_Maps/MapServer/"
    "{layer}/query"
)
OS_DOWNLOADS_URL = "https://api.os.uk/downloads/v1/products/OpenMapLocal/downloads"

SEPA_LAYERS = {
    "SEPA_River_High_Flood_5m_.tif": 0,
    "SEPA_River_Medium_Flood_5.tif": 1,
    "SEPA_River_Low_Flood_5m_r.tif": 2,
    "SEPA_Coastal_High_Flood_5.tif": 6,
    "SEPA_Coastal_Medium_Flood.tif": 7,
    "SEPA_Coastal_Low_Flood_5m.tif": 8,
}
LAND_COVER_OUTPUTS = {
    "OS_Built_Up_Areas_5m_res.tif": {50},
    "OS_Greenspace_5m_res.tif": {10, 20, 30, 40, 90, 95, 100},
    "OS_Rivers_5m_res.tif": {80},
}


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    profile: str
    input_dir: str
    manifest: str | None
    generated_files: list[str]
    reused_files: list[str]
    downloaded_bytes: int
    exposure_prepared: bool
    notes: list[str]


def bootstrap_glasgow_open_data(
    input_dir: str | Path,
    *,
    resolution: float = 30.0,
    include_exposure: bool = False,
    force: bool = False,
    user_agent: str = "oasis-geoagent/0.1",
) -> BootstrapResult:
    """Create the minimum real-data hazard stack in an empty Input directory."""

    input_dir = Path(input_dir)
    raster_dir = input_dir / "OASIS_Rasters" / "OASIS_Rasters"
    raster_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = input_dir / ".bootstrap-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    targets = [raster_dir / "DTM_5m_res.tif"]
    targets.extend(raster_dir / name for name in LAND_COVER_OUTPUTS)
    targets.extend(raster_dir / name for name in SEPA_LAYERS)
    existing = [path for path in targets if path.exists()]
    if existing and len(existing) != len(targets) and not force:
        raise FileExistsError(
            "The Input directory contains a partial raster stack. Run again with --force "
            "or use an empty Input directory."
        )

    if len(existing) == len(targets) and not force:
        existing_manifest = input_dir / "OPEN_DATA_BOOTSTRAP.json"
        exposure_prepared, downloaded_bytes = _prepare_optional_exposure(
            input_dir,
            include_exposure=include_exposure,
            force=False,
            user_agent=user_agent,
        )
        return BootstrapResult(
            status="unchanged",
            profile="glasgow-open-data",
            input_dir=str(input_dir),
            manifest=str(existing_manifest) if existing_manifest.exists() else None,
            generated_files=[],
            reused_files=[str(path) for path in existing],
            downloaded_bytes=downloaded_bytes,
            exposure_prepared=exposure_prepared,
            notes=["A complete compatible raster stack already exists; it was not overwritten."],
        )

    bounds = GLASGOW_ANALYSIS_BOUNDS
    width = ceil((bounds[2] - bounds[0]) / resolution)
    height = ceil((bounds[3] - bounds[1]) / resolution)
    transform = from_origin(bounds[0], bounds[3], resolution, resolution)
    wgs84_bounds = transform_bounds("EPSG:27700", "EPSG:4326", *bounds)
    generated: list[Path] = []
    source_records: list[dict[str, object]] = []
    downloaded_bytes = 0

    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(300.0),
        headers={"User-Agent": user_agent},
    ) as client:
        dem_paths = []
        for tile in _copernicus_tiles(wgs84_bounds):
            url = COPERNICUS_DEM_URL.format(tile=tile)
            path = cache_dir / f"{tile}.tif"
            downloaded_bytes += _download(client, url, path, force=force)
            dem_paths.append(path)
            source_records.append(_source_record("Copernicus DEM GLO-30", url, path))

        dem = _warp_tiles(
            dem_paths,
            width=width,
            height=height,
            transform=transform,
            resampling=Resampling.bilinear,
            dtype="float32",
            nodata=np.nan,
        )
        dem_path = raster_dir / "DTM_5m_res.tif"
        _write_raster(dem_path, dem, transform, nodata=np.nan)
        generated.append(dem_path)

        cover_paths = []
        for tile in _worldcover_tiles(wgs84_bounds):
            url = WORLD_COVER_URL.format(tile=tile)
            path = cache_dir / f"worldcover-{tile}.tif"
            downloaded_bytes += _download(client, url, path, force=force)
            cover_paths.append(path)
            source_records.append(_source_record("ESA WorldCover 2021 v200", url, path))

        cover = _warp_tiles(
            cover_paths,
            width=width,
            height=height,
            transform=transform,
            resampling=Resampling.nearest,
            dtype="uint8",
            nodata=0,
        )
        for filename, classes in LAND_COVER_OUTPUTS.items():
            path = raster_dir / filename
            values = np.isin(cover, list(classes)).astype("uint8")
            _write_raster(path, values, transform, nodata=255)
            generated.append(path)

        for filename, layer in SEPA_LAYERS.items():
            url = SEPA_FLOOD_QUERY_URL.format(layer=layer)
            features = _sepa_features(client, layer, bounds)
            values = rasterize(
                ((feature["geometry"], 1) for feature in features),
                out_shape=(height, width),
                transform=transform,
                fill=0,
                dtype="uint8",
                all_touched=True,
            )
            path = raster_dir / filename
            _write_raster(path, values, transform, nodata=255)
            generated.append(path)
            source_records.append(
                {
                    "dataset": "SEPA Flood Maps v3.0",
                    "url": url,
                    "layer": layer,
                    "feature_count": len(features),
                }
            )

    manifest_path = input_dir / "OPEN_DATA_BOOTSTRAP.json"
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": "glasgow-open-data",
        "area": {
            "name": "Glasgow analysis extent",
            "crs": "EPSG:27700",
            "bounds": list(bounds),
            "resolution_m": resolution,
            "shape": [height, width],
        },
        "sources": source_records,
        "outputs": [
            {
                "path": str(path.relative_to(input_dir)),
                "sha256": _hash(path, "sha256"),
            }
            for path in generated
        ],
        "derivations": {
            "OS_Built_Up_Areas_5m_res.tif": "ESA WorldCover class 50 (built-up)",
            "OS_Greenspace_5m_res.tif": "ESA WorldCover vegetated classes 10, 20, 30, 40, 90, 95, and 100",
            "OS_Rivers_5m_res.tif": "ESA WorldCover class 80 (permanent water; river/water context proxy)",
            "slope": "Derived in degrees from the DEM at analysis time",
            "flow_accumulation": "Explicit topographic convergence proxy derived from the DEM at analysis time",
        },
        "licence_and_attribution": [
            "SEPA Flood Maps v3.0: © SEPA 2025, Open Government Licence 3.0.",
            "ESA WorldCover 2021: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021).",
            "Copernicus DEM: produced using Copernicus WorldDEM-30, provided under COPERNICUS by the European Union and ESA.",
        ],
        "scientific_note": (
            "This open-data profile is a reproducible lower-resolution replacement for the local "
            "5 m OASIS raster geodatabase. File names retain the legacy interface contract; the "
            "actual resolution is recorded above."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    exposure_prepared, exposure_bytes = _prepare_optional_exposure(
        input_dir,
        include_exposure=include_exposure,
        force=force,
        user_agent=user_agent,
    )
    downloaded_bytes += exposure_bytes
    return BootstrapResult(
        status="success",
        profile="glasgow-open-data",
        input_dir=str(input_dir),
        manifest=str(manifest_path),
        generated_files=[str(path) for path in generated],
        reused_files=[],
        downloaded_bytes=downloaded_bytes,
        exposure_prepared=exposure_prepared,
        notes=[
            "Slope and flow accumulation are derived from the downloaded DEM at analysis time.",
            "Use --include-exposure to add OS OpenMap Local building footprints (a much larger download).",
        ],
    )


def _prepare_optional_exposure(
    input_dir: Path,
    *,
    include_exposure: bool,
    force: bool,
    user_agent: str,
) -> tuple[bool, int]:
    if not include_exposure:
        return False, 0

    archive = input_dir / ".bootstrap-cache" / "opmplc_essh_ns.zip"
    extract_dir = input_dir / "opmplc_essh_ns"
    downloaded = 0
    if force or not extract_dir.exists():
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(300.0),
            headers={"User-Agent": user_agent},
        ) as client:
            downloads = client.get(OS_DOWNLOADS_URL).json()
            item = next(
                entry
                for entry in downloads
                if entry.get("area") == "NS" and entry.get("format") == "ESRI® Shapefile"
            )
            downloaded = _download(client, item["url"], archive, force=force)
        if _hash(archive, "md5") != item["md5"]:
            raise ValueError("OS OpenMap Local archive checksum does not match the Downloads API metadata.")
        if force and extract_dir.exists():
            shutil.rmtree(extract_dir)
        _extract_zip(archive, extract_dir)

    from core_analyst.real_data_inputs import prepare_os_openmap_buildings

    buildings = prepare_os_openmap_buildings(
        input_dir,
        input_dir / "processed" / "exposure" / "os_openmap_buildings_glasgow_1km_buffer.geojson",
    )
    return buildings.exists(), downloaded


def _download(client: httpx.Client, url: str, path: Path, *, force: bool) -> int:
    if path.exists() and not force:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    size = temporary.stat().st_size
    temporary.replace(path)
    return size


def _extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Archive member escapes the extraction directory: {member.filename}")
        bundle.extractall(destination)


def _warp_tiles(
    paths: list[Path],
    *,
    width: int,
    height: int,
    transform,
    resampling: Resampling,
    dtype: str,
    nodata: float | int,
) -> np.ndarray:
    destination = np.full((height, width), nodata, dtype=dtype)
    for path in paths:
        tile = np.full((height, width), nodata, dtype=dtype)
        with rasterio.open(path) as source:
            reproject(
                source=rasterio.band(source, 1),
                destination=tile,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=transform,
                dst_crs="EPSG:27700",
                dst_nodata=nodata,
                resampling=resampling,
            )
        valid = np.isfinite(tile) if np.issubdtype(tile.dtype, np.floating) else tile != nodata
        destination[valid] = tile[valid]
    return destination


def _write_raster(path: Path, values: np.ndarray, transform, *, nodata: float | int) -> None:
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": str(values.dtype),
        "crs": "EPSG:27700",
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values, 1)


def _sepa_features(
    client: httpx.Client,
    layer: int,
    bounds: tuple[float, float, float, float],
) -> list[dict[str, object]]:
    response = client.get(
        SEPA_FLOOD_QUERY_URL.format(layer=layer),
        params={
            "where": "1=1",
            "geometry": ",".join(str(value) for value in bounds),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "27700",
            "outSR": "27700",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "f": "geojson",
        },
    )
    response.raise_for_status()
    return response.json()["features"]


def _copernicus_tiles(bounds: tuple[float, float, float, float]) -> list[str]:
    west, south, east, north = bounds
    tiles = []
    for latitude in range(floor(south), ceil(north)):
        for longitude in range(floor(west), ceil(east)):
            tile = (
                f"Copernicus_DSM_COG_10_{_latitude(latitude)}_00_"
                f"{_longitude(longitude)}_00_DEM"
            )
            tiles.append(tile)
    return tiles


def _worldcover_tiles(bounds: tuple[float, float, float, float]) -> list[str]:
    west, south, east, north = bounds
    tiles = []
    latitude = floor(south / 3) * 3
    while latitude < north:
        longitude = floor(west / 3) * 3
        while longitude < east:
            tiles.append(f"{_latitude(latitude)}{_longitude(longitude)}")
            longitude += 3
        latitude += 3
    return tiles


def _latitude(value: int) -> str:
    return f"{'N' if value >= 0 else 'S'}{abs(value):02d}"


def _longitude(value: int) -> str:
    return f"{'E' if value >= 0 else 'W'}{abs(value):03d}"


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_record(dataset: str, url: str, path: Path) -> dict[str, object]:
    return {
        "dataset": dataset,
        "url": url,
        "cached_file": path.name,
        "bytes": path.stat().st_size,
        "sha256": _hash(path, "sha256"),
    }


def result_dict(result: BootstrapResult) -> dict[str, object]:
    return asdict(result)
