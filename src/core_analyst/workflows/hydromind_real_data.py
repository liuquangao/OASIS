from __future__ import annotations

from pathlib import Path

from rasterio.enums import Resampling

from core_analyst.data_adapters import (
    AlignedRasterSource,
    DerivedSlopeSource,
    FallbackDataSource,
    ImperviousnessCompositeSource,
    RiverNetworkPresenceSource,
    TopographicFlowProxySource,
)
from core_analyst.data_sources import (
    MetOfficeSiteForecastRainfallSource,
    MockRainfallAPISource,
    NRFAHistoricalRainfallSource,
    NRFAHistoricalRiverFlowSource,
    SEPARainfallAPISource,
)
from core_analyst.study_area import load_glasgow_1km_buffer_bounds


GDB_LAYERS = {
    "dem": "DTM_5m_res",
    "slope": "Slope_degrees_DTM_5m",
    "built_up": "OS_Built_Up_Areas_5m_res",
    "greenspace": "OS_Greenspace_5m_res",
    "landcover": "UKCEH_Landcover_5m_res",
    "rivers": "OS_Rivers_5m_res",
    "flow_accumulation": "FlowAcc_DTM_5m",
    "fluvial_high": "SEPA_River_High_Flood_5m_res",
    "fluvial_medium": "SEPA_River_Medium_Flood_5m_res",
    "fluvial_low": "SEPA_River_Low_Flood_5m_res",
    "coastal_high": "SEPA_Coastal_High_Flood_5m_res",
    "coastal_medium": "SEPA_Coastal_Medium_Flood_5m_res",
    "coastal_low": "SEPA_Coastal_Low_Flood_5m_res",
    "fluvial_current": "SEPA_River_High_Flood_5m_res",
    "fluvial_future": "SEPA_River_Low_Flood_5m_res",
    "coastal_current": "SEPA_Coastal_High_Flood_5m_res",
    "coastal_future": "SEPA_Coastal_Low_Flood_5m_res",
}


def gdb_uri(gdb_path: str | Path, layer_name: str) -> str:
    return f'OpenFileGDB:"{Path(gdb_path)}":{layer_name}'


def build_hydromind_input_sources(
    input_dir: str | Path = "Input",
    rainfall_multiplier: float = 1.0,
    rainfall_source: str = "mock",
    sepa_station_numbers: list[str] | None = None,
    sepa_buffer_meters: float = 0.0,
    sepa_include_hourly_history: bool = False,
    sepa_history_hours: int = 6,
    metoffice_horizon_hours: int = 6,
    metoffice_sample_grid_size: int = 5,
    use_glasgow_1km_buffer_boundary: bool = True,
):
    input_dir = Path(input_dir)
    gdb_path = input_dir / "HYDROMIND_raster.gdb" / "HYDROMIND_raster.gdb"
    tif_dir = input_dir / "HYDROMIND_Rasters" / "HYDROMIND_Rasters"

    if gdb_path.exists():
        dem = AlignedRasterSource("dem", gdb_uri(gdb_path, GDB_LAYERS["dem"]), Resampling.bilinear, use_source_mask=True)
        slope = AlignedRasterSource("slope", gdb_uri(gdb_path, GDB_LAYERS["slope"]), Resampling.bilinear, use_source_mask=True)
        built_up = AlignedRasterSource("built_up", gdb_uri(gdb_path, GDB_LAYERS["built_up"]), Resampling.nearest)
        greenspace = AlignedRasterSource("greenspace", gdb_uri(gdb_path, GDB_LAYERS["greenspace"]), Resampling.nearest)
        landcover = AlignedRasterSource("landcover", gdb_uri(gdb_path, GDB_LAYERS["landcover"]), Resampling.nearest)
        rivers = AlignedRasterSource("rivers", gdb_uri(gdb_path, GDB_LAYERS["rivers"]), Resampling.nearest)
        flow_accumulation = FallbackDataSource(
            "flow_accumulation",
            AlignedRasterSource(
                "flow_accumulation",
                gdb_uri(gdb_path, GDB_LAYERS["flow_accumulation"]),
                Resampling.bilinear,
                use_source_mask=True,
            ),
            TopographicFlowProxySource(dem),
        )
    else:
        dem = AlignedRasterSource("dem", tif_dir / "DTM_5m_res.tif", Resampling.bilinear)
        slope_path = tif_dir / "Slope_degrees_DTM_5m.tif"
        slope = (
            AlignedRasterSource("slope", slope_path, Resampling.bilinear)
            if slope_path.exists()
            else DerivedSlopeSource(dem)
        )
        built_up = AlignedRasterSource("built_up", tif_dir / "OS_Built_Up_Areas_5m_res.tif", Resampling.nearest)
        greenspace_path = tif_dir / "OS_Greenspace_5m_res.tif"
        greenspace = (
            AlignedRasterSource("greenspace", greenspace_path, Resampling.nearest)
            if greenspace_path.exists()
            else None
        )
        landcover_path = tif_dir / "UKCEH_Landcover_5m_res.tif"
        landcover = (
            AlignedRasterSource("landcover", landcover_path, Resampling.nearest)
            if landcover_path.exists()
            else None
        )
        rivers = AlignedRasterSource("rivers", tif_dir / "OS_Rivers_5m_res.tif", Resampling.nearest)
        flow_accumulation = FallbackDataSource(
            "flow_accumulation",
            AlignedRasterSource("flow_accumulation", tif_dir / "FlowAcc_DTM_5m.tif", Resampling.bilinear),
            TopographicFlowProxySource(dem),
        )

    if rainfall_source == "sepa":
        study_area_bounds = load_glasgow_1km_buffer_bounds(input_dir) if use_glasgow_1km_buffer_boundary else None
        rainfall = SEPARainfallAPISource(
            sepa_station_numbers or ["auto"],
            discovery_buffer_meters=sepa_buffer_meters,
            discovery_bounds=study_area_bounds,
            include_hourly_history=sepa_include_hourly_history,
            history_hours=sepa_history_hours,
        )
    elif rainfall_source == "metoffice-site":
        rainfall = MetOfficeSiteForecastRainfallSource(
            horizon_hours=metoffice_horizon_hours,
            sample_grid_size=metoffice_sample_grid_size,
        )
    else:
        rainfall = MockRainfallAPISource(multiplier=rainfall_multiplier)

    return {
        "dem": dem,
        "slope": slope,
        "flow_accumulation": flow_accumulation,
        "river_network": RiverNetworkPresenceSource(rivers),
        "imperviousness": ImperviousnessCompositeSource(built_up, greenspace, landcover),
        "rainfall": rainfall,
    }


def build_reference_flood_sources(
    input_dir: str | Path,
    hazard_type: str,
    scenario: str,
):
    input_dir = Path(input_dir)
    gdb_path = input_dir / "HYDROMIND_raster.gdb" / "HYDROMIND_raster.gdb"
    tif_dir = input_dir / "HYDROMIND_Rasters" / "HYDROMIND_Rasters"
    scenario_aliases = {
        "current": "high",
        "future": "low",
    }
    key = f"{hazard_type}_{scenario_aliases.get(scenario, scenario)}"

    if gdb_path.exists():
        dem = AlignedRasterSource("dem", gdb_uri(gdb_path, GDB_LAYERS["dem"]), Resampling.bilinear, use_source_mask=True)
        reference = AlignedRasterSource("reference_flood", gdb_uri(gdb_path, GDB_LAYERS[key]), Resampling.nearest, fill_value=0.0)
    else:
        filenames = {
            "fluvial_high": "SEPA_River_High_Flood_5m_.tif",
            "fluvial_medium": "SEPA_River_Medium_Flood_5.tif",
            "fluvial_low": "SEPA_River_Low_Flood_5m_r.tif",
            "coastal_high": "SEPA_Coastal_High_Flood_5.tif",
            "coastal_medium": "SEPA_Coastal_Medium_Flood.tif",
            "coastal_low": "SEPA_Coastal_Low_Flood_5m.tif",
            "fluvial_current": "SEPA_River_High_Flood_5m_.tif",
            "fluvial_future": "SEPA_River_Low_Flood_5m_r.tif",
            "coastal_current": "SEPA_Coastal_High_Flood_5.tif",
            "coastal_future": "SEPA_Coastal_Low_Flood_5m.tif",
        }
        dem = AlignedRasterSource("dem", tif_dir / "DTM_5m_res.tif", Resampling.bilinear)
        reference = AlignedRasterSource("reference_flood", tif_dir / filenames[key], Resampling.nearest, fill_value=0.0)

    return {
        "dem": dem,
        "reference_flood": reference,
    }


def build_historical_hydrological_sources(input_dir: str | Path = "Input"):
    """Return reusable historical dynamic sources when downloaded NRFA ZIPs exist."""

    input_dir = Path(input_dir)
    csv_dir = input_dir / "CSV-20260825T012052Z-1-001" / "CSV"
    sources = {}
    flow_zip = csv_dir / "Gauged_daily_flow.zip"
    rainfall_zip = csv_dir / "Rainfall.zip"
    if flow_zip.exists():
        sources["nrfa_historical_river_flow"] = NRFAHistoricalRiverFlowSource(flow_zip)
    if rainfall_zip.exists():
        sources["nrfa_historical_rainfall"] = NRFAHistoricalRainfallSource(rainfall_zip)
    return sources
