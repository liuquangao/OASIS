from __future__ import annotations

from pathlib import Path


def raster_dir(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("HYDROMIND_Rasters") / "HYDROMIND_Rasters",
            Path("OASIS_Rasters") / "OASIS_Rasters",
        ),
    )


def polygon_dir(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("HYDROMIND_Polygon") / "HYDROMIND_Polygon",
            Path("OASIS_Polygon") / "OASIS_Polygon",
        ),
    )


def datazone2022_dir(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("Datazone2022"),
            Path("DataZone") / "csv2022",
        ),
    )


def datazone_boundaries2011_dir(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("DataZoneBoundaries2011"),
            Path("DataZone") / "shapefile2011",
        ),
    )


def datazone_boundaries2022_geojson(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("DataZoneBoundaries2022") / "glasgow_data_zones_2022.geojson",
            Path("DataZone") / "Geojson2022" / "glasgow_data_zones_2022.geojson",
        ),
    )


def nrfa_csv_dir(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("CSV-20260825T012052Z-1-001") / "CSV",
            Path("OASIS_CSV") / "CSV",
        ),
    )


def nrfa_flow_source(input_dir: str | Path) -> Path:
    root = nrfa_csv_dir(input_dir)
    return _first_existing(root, (Path("Gauged_daily_flow.zip"), Path("Gauged_daily_flow")))


def nrfa_rainfall_source(input_dir: str | Path) -> Path:
    root = nrfa_csv_dir(input_dir)
    return _first_existing(root, (Path("Rainfall.zip"), Path("Rainfall")))


def simd_source(input_dir: str | Path) -> Path:
    return _first_existing(
        Path(input_dir),
        (
            Path("SIMD+2020v2+-+indicators.xlsx"),
            Path("processed") / "simd" / "simd_2020v2_indicators.csv",
        ),
    )


def _first_existing(root: Path, candidates: tuple[Path, ...]) -> Path:
    for candidate in candidates:
        path = root / candidate
        if path.exists():
            return path
    return root / candidates[0]
