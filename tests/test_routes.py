from pathlib import Path

import httpx
import numpy as np
import rasterio
from rasterio.transform import from_origin

from oasis.integrations.osrm import OsrmRoutingClient
from oasis.integrations.raster_route_hazard import RasterRouteHazardAnalyzer


async def test_osrm_returns_candidate_route_geometry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["alternatives"] == "3"
        assert request.url.params["geometries"] == "geojson"
        return httpx.Response(
            200,
            request=request,
            json={
                "code": "Ok",
                "routes": [
                    {
                        "distance": 1200.0,
                        "duration": 180.0,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-4.25, 55.86], [-4.28, 55.87]],
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        routes = await OsrmRoutingClient(
            client,
            base_url="https://router.test",
        ).candidate_routes(
            origin_latitude=55.86,
            origin_longitude=-4.25,
            destination_latitude=55.87,
            destination_longitude=-4.28,
            alternatives=3,
        )

    assert len(routes) == 1
    assert routes[0].coordinates[-1] == (-4.28, 55.87)
    assert routes[0].distance_m == 1200


async def test_route_hazard_analyser_reports_distance_by_class(tmp_path: Path) -> None:
    raster_path = tmp_path / "hazard.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=4,
        height=1,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(0, 1, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(np.array([[1, 2, 3, 0]], dtype="uint8"), 1)

    result = await RasterRouteHazardAnalyzer(raster_path).analyse(
        [(0, 0.5), (4, 0.5)],
        sample_spacing_m=120_000,
    )

    assert result.high_distance_m > 100_000
    assert result.medium_distance_m > 100_000
    assert result.low_distance_m > 100_000
    assert result.no_data_distance_m > 100_000
    assert result.coverage_percent == 75
    assert result.hazard_index == 2
    assert result.highest_class == "high"
