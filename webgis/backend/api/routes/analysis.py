from fastapi import APIRouter

from analysis.exposure import ExposureRequest, run_exposure_analysis
from analysis.overlay import RasterOverlayRequest, ZonalStatsRequest, raster_overlay, zonal_statistics
from analysis.spatial_query import BufferRequest, IntersectionRequest, buffer_analysis, spatial_intersection

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/intersection")
def intersection(payload: IntersectionRequest) -> dict:
    return spatial_intersection(payload)


@router.post("/buffer")
def buffer(payload: BufferRequest) -> dict:
    return buffer_analysis(payload)


@router.post("/zonal-statistics")
def zonal_stats(payload: ZonalStatsRequest) -> dict:
    return zonal_statistics(payload)


@router.post("/raster-overlay")
def overlay(payload: RasterOverlayRequest) -> dict:
    return raster_overlay(payload)


@router.post("/exposure")
def exposure(payload: ExposureRequest) -> dict:
    return run_exposure_analysis(payload)
