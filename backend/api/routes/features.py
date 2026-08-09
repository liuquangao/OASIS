from fastapi import APIRouter, Query

from analysis.spatial_query import query_features

router = APIRouter(tags=["spatial query"])


@router.get("/features/{layer_name}")
def get_features(
    layer_name: str,
    limit: int = Query(default=100, ge=1, le=1000),
    bbox: str | None = Query(
        default=None,
        description="Optional EPSG:4326 bbox as min_lon,min_lat,max_lon,max_lat.",
    ),
) -> dict:
    return query_features(layer_name=layer_name, limit=limit, bbox=bbox)
