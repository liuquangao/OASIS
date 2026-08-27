from typing import Any

from fastapi import HTTPException
from psycopg import sql
from pydantic import BaseModel, Field

from analysis.db import db_connection
from metadata.repository import get_dataset


class IntersectionRequest(BaseModel):
    source_layer: str = Field(..., examples=["historical_flood_extent"])
    target_layer: str = Field(..., examples=["buildings"])
    limit: int = Field(default=100, ge=1, le=1000)


class BufferRequest(BaseModel):
    layer_name: str = Field(..., examples=["rivers"])
    distance_m: float = Field(..., gt=0, examples=[50])
    limit: int = Field(default=100, ge=1, le=1000)


def _split_table(table_name: str) -> tuple[str, str]:
    parts = table_name.split(".")
    if len(parts) != 2 or not all(parts):
        raise HTTPException(status_code=400, detail=f"Invalid PostGIS table: {table_name}")
    return parts[0], parts[1]


def _vector_dataset(layer_name: str) -> dict[str, Any]:
    dataset = get_dataset(layer_name)
    if dataset.get("type") != "vector":
        raise HTTPException(status_code=400, detail=f"{layer_name} is not a vector dataset")
    if not dataset.get("database_table"):
        raise HTTPException(status_code=400, detail=f"{layer_name} has no PostGIS table metadata")
    return dataset


def _bbox_filter(bbox: str | None) -> tuple[sql.SQL, list[float]]:
    if not bbox:
        return sql.SQL(""), []
    try:
        min_lon, min_lat, max_lon, max_lat = [float(value) for value in bbox.split(",")]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bbox must be min_lon,min_lat,max_lon,max_lat") from exc
    return (
        sql.SQL(
            "AND geom && ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 27700)"
        ),
        [min_lon, min_lat, max_lon, max_lat],
    )


def query_features(layer_name: str, limit: int = 100, bbox: str | None = None) -> dict:
    dataset = _vector_dataset(layer_name)
    schema, table = _split_table(dataset["database_table"])
    bbox_sql, bbox_values = _bbox_filter(bbox)

    query = sql.SQL(
        """
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        ) AS geojson
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(geom, 4326))::jsonb,
                'properties', to_jsonb(row) - 'geom'
            ) AS feature
            FROM (
                SELECT *
                FROM {schema}.{table}
                WHERE geom IS NOT NULL
                {bbox_filter}
                LIMIT %s
            ) AS row
        ) AS features;
        """
    ).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        bbox_filter=bbox_sql,
    )

    with db_connection() as conn:
        result = conn.execute(query, [*bbox_values, limit]).fetchone()
    return result["geojson"] if result else {"type": "FeatureCollection", "features": []}


def spatial_intersection(payload: IntersectionRequest) -> dict:
    source = _vector_dataset(payload.source_layer)
    target = _vector_dataset(payload.target_layer)
    source_schema, source_table = _split_table(source["database_table"])
    target_schema, target_table = _split_table(target["database_table"])

    query = sql.SQL(
        """
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        ) AS geojson
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(t.geom, 4326))::jsonb,
                'properties', to_jsonb(t) - 'geom'
            ) AS feature
            FROM {target_schema}.{target_table} AS t
            WHERE EXISTS (
                SELECT 1
                FROM {source_schema}.{source_table} AS s
                WHERE ST_Intersects(t.geom, s.geom)
            )
            LIMIT %s
        ) AS intersected;
        """
    ).format(
        source_schema=sql.Identifier(source_schema),
        source_table=sql.Identifier(source_table),
        target_schema=sql.Identifier(target_schema),
        target_table=sql.Identifier(target_table),
    )

    with db_connection() as conn:
        result = conn.execute(query, [payload.limit]).fetchone()
    return {
        "operation": "spatial_intersection",
        "source_layer": payload.source_layer,
        "target_layer": payload.target_layer,
        "features": result["geojson"] if result else {"type": "FeatureCollection", "features": []},
    }


def buffer_analysis(payload: BufferRequest) -> dict:
    dataset = _vector_dataset(payload.layer_name)
    schema, table = _split_table(dataset["database_table"])

    query = sql.SQL(
        """
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(feature), '[]'::jsonb)
        ) AS geojson
        FROM (
            SELECT jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(ST_Buffer(geom, %s), 4326))::jsonb,
                'properties', to_jsonb(row) - 'geom' || jsonb_build_object('buffer_m', %s)
            ) AS feature
            FROM (
                SELECT *
                FROM {schema}.{table}
                WHERE geom IS NOT NULL
                LIMIT %s
            ) AS row
        ) AS buffered;
        """
    ).format(schema=sql.Identifier(schema), table=sql.Identifier(table))

    with db_connection() as conn:
        result = conn.execute(
            query, [payload.distance_m, payload.distance_m, payload.limit]
        ).fetchone()
    return {
        "operation": "buffer",
        "layer_name": payload.layer_name,
        "distance_m": payload.distance_m,
        "features": result["geojson"] if result else {"type": "FeatureCollection", "features": []},
    }
