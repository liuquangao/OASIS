from pydantic import BaseModel, Field

from metadata.repository import get_dataset


class ZonalStatsRequest(BaseModel):
    raster_layer: str = Field(..., examples=["sepa_flood_maps"])
    zone_layer: str = Field(..., examples=["population"])
    statistic: str = Field(default="mean", examples=["mean"])


class RasterOverlayRequest(BaseModel):
    raster_layers: list[str] = Field(..., examples=[["dem", "land_cover", "sepa_flood_maps"]])
    method: str = Field(default="weighted_sum", examples=["weighted_sum"])
    weights: dict[str, float] | None = None


def zonal_statistics(payload: ZonalStatsRequest) -> dict:
    raster = get_dataset(payload.raster_layer)
    zones = get_dataset(payload.zone_layer)
    return {
        "operation": "zonal_statistics",
        "status": "configured",
        "raster_layer": raster["name"],
        "zone_layer": zones["name"],
        "statistic": payload.statistic,
        "implementation_note": (
            "This API is reserved for raster-zone processing. Store flood-depth rasters "
            "as PostGIS rasters or add a GDAL/rasterio worker to compute statistics from GeoTIFF."
        ),
    }


def raster_overlay(payload: RasterOverlayRequest) -> dict:
    datasets = [get_dataset(layer_name) for layer_name in payload.raster_layers]
    return {
        "operation": "raster_overlay",
        "status": "configured",
        "method": payload.method,
        "weights": payload.weights or {},
        "layers": [dataset["name"] for dataset in datasets],
        "implementation_note": (
            "This reusable tool endpoint defines the overlay contract for the future agent. "
            "Add a raster processing worker when the MVP moves beyond GeoTIFF visualization."
        ),
    }
