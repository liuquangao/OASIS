from fastapi import HTTPException
from psycopg import sql
from pydantic import BaseModel, Field

from analysis.db import db_connection
from analysis.spatial_query import _split_table, _vector_dataset


class ExposureRequest(BaseModel):
    flood_extent_layer: str = Field(..., examples=["historical_flood_extent"])
    building_layer: str = Field(..., examples=["buildings"])
    population_layer: str = Field(..., examples=["population"])
    population_field: str = Field(default="population", examples=["population"])


def _identifier_from_field(field_name: str) -> sql.Identifier:
    if not field_name.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid population field")
    return sql.Identifier(field_name)


def run_exposure_analysis(payload: ExposureRequest) -> dict:
    flood = _vector_dataset(payload.flood_extent_layer)
    buildings = _vector_dataset(payload.building_layer)
    population = _vector_dataset(payload.population_layer)

    flood_schema, flood_table = _split_table(flood["database_table"])
    building_schema, building_table = _split_table(buildings["database_table"])
    population_schema, population_table = _split_table(population["database_table"])

    query = sql.SQL(
        """
        WITH affected_buildings AS (
            SELECT DISTINCT b.*
            FROM {building_schema}.{building_table} AS b
            JOIN {flood_schema}.{flood_table} AS f
              ON ST_Intersects(b.geom, f.geom)
        ),
        affected_population AS (
            SELECT DISTINCT p.*
            FROM {population_schema}.{population_table} AS p
            JOIN {flood_schema}.{flood_table} AS f
              ON ST_Intersects(p.geom, f.geom)
        )
        SELECT
            (SELECT COUNT(*) FROM affected_buildings) AS affected_buildings,
            (
                SELECT COALESCE(SUM(({population_field})::numeric), 0)
                FROM affected_population
            ) AS affected_population;
        """
    ).format(
        flood_schema=sql.Identifier(flood_schema),
        flood_table=sql.Identifier(flood_table),
        building_schema=sql.Identifier(building_schema),
        building_table=sql.Identifier(building_table),
        population_schema=sql.Identifier(population_schema),
        population_table=sql.Identifier(population_table),
        population_field=_identifier_from_field(payload.population_field),
    )

    with db_connection() as conn:
        row = conn.execute(query).fetchone()

    return {
        "operation": "exposure",
        "inputs": payload.model_dump(),
        "affected_buildings": int(row["affected_buildings"] or 0),
        "affected_population": float(row["affected_population"] or 0),
        "notes": "Vector exposure uses ST_Intersects in EPSG:27700. Raster flood-depth exposure can be added through the zonal-statistics tool.",
    }
