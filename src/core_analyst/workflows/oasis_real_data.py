from __future__ import annotations

from pathlib import Path

from rasterio.enums import Resampling

from core_analyst.data_adapters import AlignedRasterSource, ImperviousnessCompositeSource, TopographicFlowProxySource
from core_analyst.data_sources import MetOfficeSiteForecastRainfallSource, MockRainfallAPISource, SEPARainfallAPISource


OASIS_RASTER_FILES = {
    "dem": "DTM_5m_res.tif",
    "built_up": "OS_Built_Up_Areas_5m_res.tif",
    "greenspace": "OS_Greenspace_5m_res.tif",
    "rivers": "OS_Rivers_5m_res.tif",
    "landcover": "UKCEH_Landcover_5m_res.tif",
    "sepa_combined_high": "SEPA_Coastal_and_River_Hi.tif",
    "sepa_combined_medium": "SEPA_Coastal_and_River_Me.tif",
    "sepa_combined_low": "SEPA_Coastal_and_River_Lo.tif",
    "slope_degrees": "Slope_degrees_DTM_5m.tif",
    "slope_percent": "Slope_percent_DTM_5m.tif",
}


def build_oasis_real_sources(raster_dir: str | Path, rainfall_multiplier: float = 1.0):
    raster_dir = Path(raster_dir)
    dem = AlignedRasterSource("dem", raster_dir / OASIS_RASTER_FILES["dem"], resampling=Resampling.bilinear)
    built_up = AlignedRasterSource(
        "built_up", raster_dir / OASIS_RASTER_FILES["built_up"], resampling=Resampling.nearest
    )
    greenspace_path = raster_dir / OASIS_RASTER_FILES["greenspace"]
    greenspace = (
        AlignedRasterSource("greenspace", greenspace_path, resampling=Resampling.nearest)
        if greenspace_path.exists()
        else None
    )

    return {
        "dem": dem,
        "slope": AlignedRasterSource(
            "slope", raster_dir / OASIS_RASTER_FILES["slope_degrees"], resampling=Resampling.bilinear
        ),
        "flow_accumulation": TopographicFlowProxySource(dem),
        "imperviousness": ImperviousnessCompositeSource(built_up, greenspace),
        "rainfall": MockRainfallAPISource(multiplier=rainfall_multiplier),
    }


GDB_LAYERS = {
    "dem": "DTM_5m_res",
    "slope": "Slope_degrees_DTM_5m",
    "built_up": "OS_Built_Up_Areas_5m_res",
    "greenspace": "OS_Greenspace_5m_res",
    "landcover": "UKCEH_Landcover_5m_res",
    "fluvial_current": "SEPA_River_High_Flood_5m_res",
    "fluvial_future": "SEPA_River_Low_Flood_5m_res",
    "coastal_current": "SEPA_Coastal_High_Flood_5m_res",
    "coastal_future": "SEPA_Coastal_Low_Flood_5m_res",
}


def gdb_uri(gdb_path: str | Path, layer_name: str) -> str:
    return f'OpenFileGDB:"{Path(gdb_path)}":{layer_name}'


def build_oasis_input_sources(
    input_dir: str | Path = "Input",
    rainfall_multiplier: float = 1.0,
    rainfall_source: str = "mock",
    sepa_station_numbers: list[str] | None = None,
    sepa_buffer_meters: float = 0.0,
    sepa_include_hourly_history: bool = False,
    sepa_history_hours: int = 6,
    metoffice_horizon_hours: int = 6,
):
    input_dir = Path(input_dir)
    gdb_path = input_dir / "OASIS_raster.gdb" / "OASIS_raster.gdb"
    tif_dir = input_dir / "OASIS_Rasters" / "OASIS_Rasters"

    if gdb_path.exists():
        dem = AlignedRasterSource("dem", gdb_uri(gdb_path, GDB_LAYERS["dem"]), Resampling.bilinear, use_source_mask=True)
        slope = AlignedRasterSource("slope", gdb_uri(gdb_path, GDB_LAYERS["slope"]), Resampling.bilinear, use_source_mask=True)
        built_up = AlignedRasterSource("built_up", gdb_uri(gdb_path, GDB_LAYERS["built_up"]), Resampling.nearest)
        greenspace = AlignedRasterSource("greenspace", gdb_uri(gdb_path, GDB_LAYERS["greenspace"]), Resampling.nearest)
        landcover = AlignedRasterSource("landcover", gdb_uri(gdb_path, GDB_LAYERS["landcover"]), Resampling.nearest)
    else:
        dem = AlignedRasterSource("dem", tif_dir / "DTM_5m_res.tif", Resampling.bilinear)
        slope = AlignedRasterSource("slope", tif_dir / "Slope_degrees_DTM_5m.tif", Resampling.bilinear)
        built_up = AlignedRasterSource("built_up", tif_dir / "OS_Built_Up_Areas_5m_res.tif", Resampling.nearest)
        greenspace = AlignedRasterSource("greenspace", tif_dir / "OS_Greenspace_5m_res.tif", Resampling.nearest)
        landcover = AlignedRasterSource("landcover", tif_dir / "UKCEH_Landcover_5m_res.tif", Resampling.nearest)

    if rainfall_source == "sepa":
        rainfall = SEPARainfallAPISource(
            sepa_station_numbers or ["auto"],
            discovery_buffer_meters=sepa_buffer_meters,
            include_hourly_history=sepa_include_hourly_history,
            history_hours=sepa_history_hours,
        )
    elif rainfall_source == "metoffice-site":
        rainfall = MetOfficeSiteForecastRainfallSource(horizon_hours=metoffice_horizon_hours)
    else:
        rainfall = MockRainfallAPISource(multiplier=rainfall_multiplier)

    return {
        "dem": dem,
        "slope": slope,
        "flow_accumulation": TopographicFlowProxySource(dem),
        "imperviousness": ImperviousnessCompositeSource(built_up, greenspace, landcover),
        "rainfall": rainfall,
    }


def build_reference_flood_sources(
    input_dir: str | Path,
    hazard_type: str,
    scenario: str,
):
    input_dir = Path(input_dir)
    gdb_path = input_dir / "OASIS_raster.gdb" / "OASIS_raster.gdb"
    tif_dir = input_dir / "OASIS_Rasters" / "OASIS_Rasters"
    key = f"{hazard_type}_{scenario}"

    if gdb_path.exists():
        dem = AlignedRasterSource("dem", gdb_uri(gdb_path, GDB_LAYERS["dem"]), Resampling.bilinear, use_source_mask=True)
        reference = AlignedRasterSource("reference_flood", gdb_uri(gdb_path, GDB_LAYERS[key]), Resampling.nearest, fill_value=0.0)
    else:
        filenames = {
            "fluvial_current": "SEPA_River_High_Flood_5m_.tif",
            "fluvial_future": "SEPA_River_Low_Flood_5m_r.tif",
            "coastal_current": "SEPA_Coastal_High_Flood_5.tif",
            "coastal_future": "SEPA_Coastal_Low_Flood_5m.tif",
        }
        dem = AlignedRasterSource("dem", tif_dir / OASIS_RASTER_FILES["dem"], Resampling.bilinear)
        reference = AlignedRasterSource("reference_flood", tif_dir / filenames[key], Resampling.nearest, fill_value=0.0)

    return {
        "dem": dem,
        "reference_flood": reference,
    }
