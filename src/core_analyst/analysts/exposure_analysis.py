from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds, transform as window_transform
from shapely.geometry import mapping, shape
from shapely.validation import make_valid

from core_analyst.data_sources import DataSource, RasterGrid, write_raster
from core_analyst.validators.raster_validator import RasterValidator


DEFAULT_REQUIRED_FACILITY_TYPES = ("hospital", "care_home", "school", "emergency_service")


@dataclass
class VectorFeature:
    geometry: dict[str, Any]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorFeatureCollection:
    name: str
    source: str
    crs: str | None
    features: list[VectorFeature]
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorExposureSource(ABC):
    @abstractmethod
    def get_features(self) -> VectorFeatureCollection:
        """Return vector exposure features with an explicit CRS."""


class InMemoryVectorSource(VectorExposureSource):
    def __init__(
        self,
        name: str,
        features: list[dict[str, Any] | VectorFeature],
        *,
        crs: str | None,
        source: str = "in_memory",
        metadata: dict[str, Any] | None = None,
    ):
        self.name = name
        self.features = [
            feature if isinstance(feature, VectorFeature)
            else VectorFeature(feature["geometry"], feature.get("properties", {}))
            for feature in features
        ]
        self.crs = crs
        self.source = source
        self.metadata = metadata or {}

    def get_features(self) -> VectorFeatureCollection:
        return VectorFeatureCollection(self.name, self.source, self.crs, self.features, self.metadata)


class GeoJSONVectorSource(VectorExposureSource):
    def __init__(self, name: str, path: str | Path, *, crs: str | None = "EPSG:4326"):
        self.name = name
        self.path = Path(path)
        self.crs = crs

    def get_features(self) -> VectorFeatureCollection:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        crs = self._read_crs(payload) or self.crs
        features = [
            VectorFeature(feature["geometry"], feature.get("properties", {}))
            for feature in payload.get("features", [])
            if feature.get("geometry")
        ]
        return VectorFeatureCollection(
            self.name,
            str(self.path),
            crs,
            features,
            {"path": str(self.path), "format": "GeoJSON"},
        )

    def _read_crs(self, payload: dict[str, Any]) -> str | None:
        crs = payload.get("crs")
        if isinstance(crs, dict):
            properties = crs.get("properties") or {}
            name = properties.get("name")
            if isinstance(name, str) and name:
                return name
        return None


class ExposureAnalyst:
    """Hazard-to-exposure analysis for population, buildings, and facilities."""

    def __init__(
        self,
        *,
        hazard_threshold: int = 2,
        population_field: str = "population",
        critical_type_field: str = "type",
        required_facility_types: tuple[str, ...] = DEFAULT_REQUIRED_FACILITY_TYPES,
    ):
        self.hazard_threshold = int(hazard_threshold)
        self.population_field = population_field
        self.critical_type_field = critical_type_field
        self.required_facility_types = required_facility_types

    def run(
        self,
        hazard_raster: RasterGrid | str | Path,
        exposure_sources: dict[str, Any],
        output_dir: str | Path,
        *,
        hazard_type: str,
        scenario: str,
    ) -> dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        hazard = self._read_hazard(hazard_raster)
        warnings: list[dict[str, str]] = []
        outputs: dict[str, str] = {}
        hazard_nodata = RasterValidator().nodata_status(hazard)
        diagnostics: dict[str, Any] = {
            "hazard": {
                "nodata": hazard_nodata["status"],
                "grid": RasterValidator().grid_signature(hazard),
            },
            "exposure_sources": {},
        }
        if hazard_nodata["status"] in {"warning", "substantial"}:
            warnings.append({
                "code": "hazard_nodata_warning",
                "message": f"Hazard raster NoData fraction is {hazard_nodata['fraction']:.2%}.",
            })
        if hazard_nodata["status"] == "complete":
            warnings.append({
                "code": "hazard_completely_invalid",
                "message": "Hazard raster is completely invalid; exposure analysis is unavailable.",
            })
        provenance: dict[str, Any] = {
            "hazard": self._hazard_provenance(hazard),
            "exposure_sources": {},
            "processing": {
                "operation": "hazard_to_exposure",
                "hazard_threshold": self.hazard_threshold,
                "hazard_type": hazard_type,
                "scenario": scenario,
            },
        }

        exposed_mask = self.hazard_mask(hazard.data, self.hazard_threshold)
        if not exposed_mask.any():
            warnings.append({"code": "empty_hazard_footprint", "message": "No pixels meet the exposure threshold."})

        population = self._population_exposure(
            exposure_sources.get("population"),
            hazard,
            exposed_mask,
            output_dir,
            outputs,
            provenance,
            warnings,
        )
        buildings = self._vector_count_exposure(
            exposure_sources.get("buildings"),
            hazard,
            exposed_mask,
            "buildings",
            output_dir,
            outputs,
            provenance,
            warnings,
        )
        critical = self._critical_infrastructure_exposure(
            exposure_sources.get("critical_infrastructure"),
            hazard,
            exposed_mask,
            output_dir,
            outputs,
            provenance,
            warnings,
        )

        summary = {
            "hazard_type": hazard_type,
            "scenario": scenario,
            "hazard_threshold": self.hazard_threshold,
            "population": population,
            "buildings": buildings,
            "critical_infrastructure": critical,
        }
        diagnostics["exposure_sources"] = {
            key: value.get("spatial_qa", {})
            for key, value in provenance["exposure_sources"].items()
        }
        result = {
            "status": self._overall_status(summary),
            "summary": summary,
            "diagnostics": diagnostics,
            "outputs": outputs,
            "provenance": provenance,
            "warnings": warnings,
        }
        summary_path = output_dir / "exposure_summary.json"
        metadata_path = output_dir / "analysis_metadata.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        metadata_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        outputs["exposure_summary"] = str(summary_path)
        outputs["metadata"] = str(metadata_path)
        return result

    def hazard_mask(self, hazard_class: np.ndarray, threshold: int | None = None) -> np.ndarray:
        threshold = self.hazard_threshold if threshold is None else int(threshold)
        return np.isfinite(hazard_class) & (hazard_class.astype("float32") >= threshold)

    def _read_hazard(self, hazard_raster: RasterGrid | str | Path) -> RasterGrid:
        if isinstance(hazard_raster, RasterGrid):
            return hazard_raster
        path = Path(hazard_raster)
        with rasterio.open(path) as dataset:
            data = dataset.read(1).astype("float32")
            profile = dataset.profile.copy()
        return RasterGrid(path.stem, data, profile, "hazard_output", {"path": str(path)})

    def _hazard_provenance(self, hazard: RasterGrid) -> dict[str, Any]:
        transform = hazard.profile.get("transform")
        return {
            "dataset_name": hazard.name,
            "source": hazard.source_type,
            "metadata": hazard.metadata,
            "crs": str(hazard.profile.get("crs")),
            "shape": list(hazard.data.shape),
            "transform": str(transform) if transform is not None else None,
            "nodata": hazard.profile.get("nodata"),
        }

    def _population_exposure(
        self,
        source: Any,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
        output_dir: Path,
        outputs: dict[str, str],
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        if source is None:
            return self._unavailable("population", "population_dataset_not_available", provenance, warnings)
        if self._is_vector_source(source):
            return self._population_vector_exposure(source, hazard, exposed_mask, provenance, warnings)
        try:
            grid = self._read_population_grid(source, hazard)
        except Exception as exc:
            warnings.append({"code": "population_unavailable", "message": str(exc)})
            provenance["exposure_sources"]["population"] = {"status": "unavailable", "error": str(exc)}
            return {"total": None, "exposed": None, "exposure_ratio": None, "status": "unavailable"}

        provenance["exposure_sources"]["population"] = {
            "status": "available",
            "dataset_name": grid.name,
            "source": grid.source_type,
            "metadata": grid.metadata,
            "crs": str(grid.profile.get("crs")),
            "original_resolution": grid.metadata.get("original_resolution") or grid.profile.get("res"),
            "spatial_qa": grid.metadata.get("spatial_qa", {}),
            "processing_operation": "population_sum_over_hazard_mask",
            "source_geography": "raster_grid",
            "target_geography": "hazard_raster_grid",
            "aggregation_method": "cellwise_sum_over_exposed_hazard_pixels",
            "hazard_threshold": self.hazard_threshold,
        }
        diagnostics = provenance.setdefault("diagnostics", {}).setdefault("exposure_sources", {})
        diagnostics["population"] = grid.metadata.get("spatial_qa", {})
        valid_population = np.isfinite(grid.data)
        total = float(np.nansum(grid.data[valid_population]))
        exposed_values = np.where(exposed_mask & valid_population, grid.data, 0.0).astype("float32")
        exposed = float(np.nansum(exposed_values))
        ratio = None if total <= 0 else exposed / total

        profile = hazard.profile.copy()
        profile.update(dtype="float32", nodata=0.0)
        path = output_dir / "population_exposure.tif"
        write_raster(path, RasterGrid("population_exposure", exposed_values, profile, "analysis_output"))
        outputs["population_exposure"] = str(path)
        return {"total": total, "exposed": exposed, "exposure_ratio": ratio, "status": "available"}

    def _population_vector_exposure(
        self,
        source: Any,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        collection = self._read_vector_source(source, "population", provenance, warnings)
        if collection is None:
            return {"total": None, "exposed": None, "exposure_ratio": None, "status": "unavailable"}

        total = 0.0
        exposed = 0.0
        for index, feature in enumerate(collection.features):
            try:
                value = float(feature.properties.get(self.population_field, 0.0) or 0.0)
            except (TypeError, ValueError):
                warnings.append({
                    "code": "population_malformed_value",
                    "message": f"Feature {index} has a non-numeric {self.population_field} value.",
                })
                continue
            geometry = self._feature_geometry(feature, collection, hazard, "population", index, warnings)
            if geometry is None:
                continue
            total += value
            if self._intersects_exposed_cells(geometry, hazard, exposed_mask):
                exposed += value
        ratio = None if total <= 0 else exposed / total
        return {"total": total, "exposed": exposed, "exposure_ratio": ratio, "status": "available"}

    def _read_population_grid(self, source: Any, hazard: RasterGrid) -> RasterGrid:
        if isinstance(source, RasterGrid):
            grid = source
        elif isinstance(source, DataSource):
            grid = source.get_data(reference=hazard)
        elif isinstance(source, (str, Path)):
            from rasterio.enums import Resampling

            from core_analyst.data_adapters import AlignedRasterSource

            grid = AlignedRasterSource("population", source, Resampling.bilinear).get_data(reference=hazard)
        else:
            raise TypeError("Population exposure source must be a RasterGrid, DataSource, or raster path.")
        validator = RasterValidator()
        comparison = validator.compare_grid_to_reference(grid, hazard)
        grid.metadata.setdefault("spatial_qa", {})["diagnostics"] = comparison["diagnostics"]
        if any(comparison["diagnostics"][key] == "mismatch" for key in ("crs", "resolution", "extent", "transform", "width_height", "alignment")):
            corrected, validation = validator.align_grid_to_reference(grid, hazard, data_type="continuous")
            if not validation.valid:
                raise ValueError("Population raster could not be aligned to the hazard raster.")
            corrected.metadata.setdefault("spatial_qa", {})["validation"] = {
                "status": validation.status,
                "diagnostics": validation.diagnostics,
                "corrections": validation.corrections,
                "provenance": validation.provenance,
            }
            return corrected
        return grid

    def _is_vector_source(self, source: Any) -> bool:
        if isinstance(source, (VectorFeatureCollection, VectorExposureSource)):
            return True
        if isinstance(source, (str, Path)):
            return Path(source).suffix.lower() in {".geojson", ".json"}
        return False

    def _vector_count_exposure(
        self,
        source: Any,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
        source_key: str,
        output_dir: Path,
        outputs: dict[str, str],
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        if source is None:
            return self._unavailable(source_key, f"{source_key}_dataset_not_available", provenance, warnings)
        collection = self._read_vector_source(source, source_key, provenance, warnings)
        if collection is None:
            return {"total": None, "exposed": None, "exposure_ratio": None, "status": "unavailable"}

        total = 0
        exposed_features = []
        for index, feature in enumerate(collection.features):
            geometry = self._feature_geometry(feature, collection, hazard, source_key, index, warnings)
            if geometry is None:
                continue
            total += 1
            if self._intersects_exposed_cells(geometry, hazard, exposed_mask):
                exposed_features.append({
                    "type": "Feature",
                    "geometry": transform_geom(str(hazard.profile["crs"]), "EPSG:4326", mapping(geometry)),
                    "properties": {**feature.properties, "exposed": True},
                })
        exposed = len(exposed_features)
        if exposed_features:
            path = output_dir / f"{source_key}_exposure.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": exposed_features}), encoding="utf-8")
            outputs[f"{source_key}_exposure"] = str(path)
        ratio = None if total == 0 else exposed / total
        return {"total": total, "exposed": exposed, "exposure_ratio": ratio, "status": "available"}

    def _critical_infrastructure_exposure(
        self,
        source: Any,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
        output_dir: Path,
        outputs: dict[str, str],
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        source_key = "critical_infrastructure"
        if source is None:
            return self._unavailable(source_key, "critical_infrastructure_dataset_not_available", provenance, warnings)
        collection = self._read_vector_source(source, source_key, provenance, warnings)
        if collection is None:
            return {
                "total": None,
                "exposed": None,
                "by_type": {},
                "categories_used": [],
                "status": "unavailable",
            }

        total = 0
        exposed = 0
        by_type = {facility_type: 0 for facility_type in self.required_facility_types}
        categories_seen: set[str] = set()
        exposed_features = []
        for index, feature in enumerate(collection.features):
            geometry = self._feature_geometry(feature, collection, hazard, source_key, index, warnings)
            if geometry is None:
                continue
            facility_type = str(feature.properties.get(self.critical_type_field, "unknown"))
            categories_seen.add(facility_type)
            total += 1
            if self._intersects_exposed_cells(geometry, hazard, exposed_mask):
                exposed += 1
                by_type[facility_type] = by_type.get(facility_type, 0) + 1
                exposed_features.append({
                    "type": "Feature",
                    "geometry": transform_geom(str(hazard.profile["crs"]), "EPSG:4326", mapping(geometry)),
                    "properties": {**feature.properties, "exposed": True},
                })

        if exposed_features:
            path = output_dir / "critical_infrastructure_exposure.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": exposed_features}), encoding="utf-8")
            outputs["critical_infrastructure_exposure"] = str(path)

        required_seen = sorted(set(self.required_facility_types) & categories_seen)
        status = "available" if set(self.required_facility_types).issubset(categories_seen) else "partial"
        return {
            "total": total,
            "exposed": exposed,
            "by_type": by_type,
            "categories_used": sorted(categories_seen),
            "required_categories_used": required_seen,
            "status": status,
        }

    def _read_vector_source(
        self,
        source: Any,
        source_key: str,
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> VectorFeatureCollection | None:
        try:
            if isinstance(source, VectorFeatureCollection):
                collection = source
            elif isinstance(source, VectorExposureSource):
                collection = source.get_features()
            elif isinstance(source, (str, Path)):
                collection = GeoJSONVectorSource(source_key, source).get_features()
            else:
                raise TypeError("Vector exposure source must be a VectorExposureSource, collection, or GeoJSON path.")
        except Exception as exc:
            warnings.append({"code": f"{source_key}_unavailable", "message": str(exc)})
            provenance["exposure_sources"][source_key] = {"status": "unavailable", "error": str(exc)}
            return None

        provenance["exposure_sources"][source_key] = {
            "status": "available",
            "dataset_name": collection.name,
            "source": collection.source,
            "crs": collection.crs,
            "feature_count": len(collection.features),
            "metadata": collection.metadata,
            "spatial_qa": {},
            "processing_operation": "vector_intersection_with_exposed_raster_cells",
            "source_geography": collection.metadata.get("geographic_unit", "vector_features"),
            "target_geography": "hazard_raster_footprint",
            "aggregation_method": "feature_intersection_count" if source_key != "population" else "polygon_total_intersection",
            "hazard_threshold": self.hazard_threshold,
        }
        if not collection.crs:
            warnings.append({"code": f"{source_key}_missing_crs", "message": "Vector source CRS is missing."})
            provenance["exposure_sources"][source_key]["status"] = "unavailable"
            return None
        validation = RasterValidator().validate_vector_collection(collection)
        provenance["exposure_sources"][source_key]["spatial_qa"] = {
            "status": validation.status,
            "diagnostics": validation.diagnostics,
            "corrections": validation.corrections,
            "provenance": validation.provenance,
        }
        for warning in validation.warnings:
            warnings.append({"code": f"{source_key}_vector_qa_warning", "message": warning})
        for error in validation.errors:
            warnings.append({"code": f"{source_key}_vector_qa_error", "message": error})
        return collection

    def _count_intersections(
        self,
        collection: VectorFeatureCollection,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
        source_key: str,
        warnings: list[dict[str, str]],
    ) -> tuple[int, int]:
        total = 0
        exposed = 0
        for index, feature in enumerate(collection.features):
            geometry = self._feature_geometry(feature, collection, hazard, source_key, index, warnings)
            if geometry is None:
                continue
            total += 1
            if self._intersects_exposed_cells(geometry, hazard, exposed_mask):
                exposed += 1
        return total, exposed

    def _intersects_exposed_cells(
        self,
        geometry: Any,
        hazard: RasterGrid,
        exposed_mask: np.ndarray,
    ) -> bool:
        window = from_bounds(*geometry.bounds, transform=hazard.profile["transform"])
        col_start = max(0, math.floor(window.col_off))
        row_start = max(0, math.floor(window.row_off))
        col_stop = min(exposed_mask.shape[1], math.ceil(window.col_off + window.width))
        row_stop = min(exposed_mask.shape[0], math.ceil(window.row_off + window.height))
        if col_start >= col_stop or row_start >= row_stop:
            return False

        candidate = exposed_mask[row_start:row_stop, col_start:col_stop]
        if not candidate.any():
            return False
        local_window = Window(col_start, row_start, col_stop - col_start, row_stop - row_start)
        covered = geometry_mask(
            [mapping(geometry)],
            out_shape=candidate.shape,
            transform=window_transform(local_window, hazard.profile["transform"]),
            invert=True,
            all_touched=True,
        )
        return bool(np.any(candidate & covered))

    def _feature_geometry(
        self,
        feature: VectorFeature,
        collection: VectorFeatureCollection,
        hazard: RasterGrid,
        source_key: str,
        index: int,
        warnings: list[dict[str, str]],
    ):
        try:
            geometry_payload = feature.geometry
            hazard_crs = str(hazard.profile.get("crs"))
            if collection.crs != hazard_crs:
                geometry_payload = transform_geom(collection.crs, hazard_crs, geometry_payload)
            geometry = shape(geometry_payload)
        except Exception as exc:
            warnings.append({"code": f"{source_key}_malformed_geometry", "message": f"Feature {index}: {exc}"})
            return None

        if geometry.is_empty:
            warnings.append({"code": f"{source_key}_empty_geometry", "message": f"Feature {index} has empty geometry."})
            return None
        if not geometry.is_valid:
            repaired = make_valid(geometry)
            warnings.append({
                "code": f"{source_key}_invalid_geometry_repaired",
                "message": f"Feature {index} had invalid geometry and was repaired.",
            })
            geometry = repaired
            if geometry.is_empty or not geometry.is_valid:
                warnings.append({
                    "code": f"{source_key}_invalid_geometry_skipped",
                    "message": f"Feature {index} remained invalid after repair.",
                })
                return None
        return geometry

    def _unavailable(
        self,
        source_key: str,
        reason: str,
        provenance: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        warnings.append({"code": reason, "message": f"{source_key} exposure dataset is not available."})
        provenance["exposure_sources"][source_key] = {"status": "unavailable", "reason": reason}
        if source_key == "critical_infrastructure":
            return {"total": None, "exposed": None, "by_type": {}, "status": "unavailable"}
        return {"total": None, "exposed": None, "exposure_ratio": None, "status": "unavailable"}

    def _overall_status(self, summary: dict[str, Any]) -> str:
        statuses = {
            summary["population"]["status"],
            summary["buildings"]["status"],
            summary["critical_infrastructure"]["status"],
        }
        if statuses == {"available"}:
            return "success"
        if "available" in statuses or "partial" in statuses:
            return "partial"
        return "unavailable"


def run_exposure_analysis(
    hazard_raster: RasterGrid | str | Path,
    exposure_sources: dict[str, Any],
    output_dir: str | Path,
    *,
    hazard_type: str,
    scenario: str,
    hazard_threshold: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exposure_config = (config or {}).get("exposure", config or {})
    threshold = hazard_threshold
    if threshold is None:
        threshold = int(exposure_config.get("hazard_threshold", 2))
    analyst = ExposureAnalyst(
        hazard_threshold=threshold,
        population_field=exposure_config.get("population_field", "population"),
        critical_type_field=exposure_config.get("critical_type_field", "type"),
        required_facility_types=tuple(
            exposure_config.get("required_facility_types", DEFAULT_REQUIRED_FACILITY_TYPES)
        ),
    )
    return analyst.run(
        hazard_raster,
        exposure_sources,
        output_dir,
        hazard_type=hazard_type,
        scenario=scenario,
    )
