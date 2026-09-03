from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from hydromind.integrations.raster_route_hazard import RasterRouteHazardAnalyzer


async def test_route_analysis_uses_core_analyst_class_order(tmp_path: Path) -> None:
    raster_path = tmp_path / "classes.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=3,
        height=1,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(0, 1, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(np.array([[1, 2, 3]], dtype="uint8"), 1)

    result = await RasterRouteHazardAnalyzer(raster_path).analyse(
        [(0, 0.5), (3, 0.5)],
        sample_spacing_m=120_000,
    )

    assert result.low_distance_m > 0
    assert result.medium_distance_m > 0
    assert result.high_distance_m > 0
    assert result.highest_class == "high"
    assert result.hazard_index == 2
