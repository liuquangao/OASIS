"""Deterministic centreline sampling of a local categorical hazard raster."""

from __future__ import annotations

import asyncio
from math import ceil
from pathlib import Path

import rasterio
from rasterio.warp import transform

from oasis.domain.geo import haversine_km
from oasis.models.routes import RouteHazardSummary


class RasterRouteHazardAnalyzer:
    def __init__(self, raster_path: Path) -> None:
        self._raster_path = raster_path

    async def analyse(
        self,
        coordinates: list[tuple[float, float]],
        *,
        sample_spacing_m: float,
    ) -> RouteHazardSummary:
        return await asyncio.to_thread(
            self._analyse_sync,
            coordinates,
            sample_spacing_m,
        )

    def _analyse_sync(
        self,
        coordinates: list[tuple[float, float]],
        sample_spacing_m: float,
    ) -> RouteHazardSummary:
        samples: list[tuple[float, float]] = []
        weights: list[float] = []
        for start, end in zip(coordinates, coordinates[1:]):
            segment_m = haversine_km(start[1], start[0], end[1], end[0]) * 1000
            steps = max(1, ceil(segment_m / sample_spacing_m))
            for index in range(steps):
                fraction = (index + 0.5) / steps
                samples.append(
                    (
                        start[0] + (end[0] - start[0]) * fraction,
                        start[1] + (end[1] - start[1]) * fraction,
                    )
                )
                weights.append(segment_m / steps)

        distances = {1: 0.0, 2: 0.0, 3: 0.0, 0: 0.0}
        with rasterio.open(self._raster_path) as dataset:
            xs, ys = transform(
                "EPSG:4326",
                dataset.crs,
                [point[0] for point in samples],
                [point[1] for point in samples],
            )
            values = dataset.sample(zip(xs, ys), masked=True)
            for value, distance_m in zip(values, weights):
                class_value = int(value[0]) if not bool(value.mask[0]) else 0
                distances[class_value if class_value in (1, 2, 3) else 0] += distance_m

        covered_m = distances[1] + distances[2] + distances[3]
        total_m = covered_m + distances[0]
        hazard_index = (
            (distances[1] + 2 * distances[2] + 3 * distances[3]) / covered_m
            if covered_m
            else None
        )
        highest_class = (
            "high"
            if distances[3]
            else "medium"
            if distances[2]
            else "low"
            if distances[1]
            else "no_data"
        )
        return RouteHazardSummary(
            sample_spacing_m=sample_spacing_m,
            high_distance_m=round(distances[3], 1),
            medium_distance_m=round(distances[2], 1),
            low_distance_m=round(distances[1], 1),
            no_data_distance_m=round(distances[0], 1),
            coverage_percent=round(100 * covered_m / total_m, 1) if total_m else 0,
            hazard_index=round(hazard_index, 3) if hazard_index is not None else None,
            highest_class=highest_class,
            warnings=[
                "Latest calculated 5 m prototype snapshot; not a live observation, forecast, or operational warning.",
                "Route hazard is sampled along the route centreline at fixed intervals.",
            ],
        )
