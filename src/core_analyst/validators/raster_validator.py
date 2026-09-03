from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.transform import array_bounds
from rasterio.warp import reproject
from shapely.errors import ShapelyError
from shapely.geometry import shape
from shapely.validation import make_valid

from core_analyst.data_sources import RasterGrid

# shapely.geometry.shape() signals malformed GeoJSON with any of these: a non-dict
# geometry raises AttributeError, a missing member KeyError, bad coordinates ValueError,
# and an unknown "type" GeometryTypeError.
GEOMETRY_INPUT_ERRORS = (ShapelyError, AttributeError, KeyError, TypeError, ValueError)
# Reprojection failures: unreadable data (OSError), bad CRS/transform (RasterioError),
# incompatible parameters (ValueError).
REPROJECTION_ERRORS = (OSError, RasterioError, ValueError)

if TYPE_CHECKING:
    from core_analyst.analysts.exposure_analysis import VectorFeatureCollection


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "success"
    corrections: list[dict[str, Any]] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ValueError("Raster input contract failed:\n" + "\n".join(f"- {e}" for e in self.errors))


class RasterValidator:
    def validate_grids(self, grids: dict[str, RasterGrid]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {}
        items = list(grids.items())
        if not items:
            return ValidationResult(
                valid=False,
                errors=["No raster grids provided."],
                diagnostics={"inputs": "missing"},
                status="failed",
            )

        ref_name, ref_grid = items[0]
        ref_profile = ref_grid.profile
        diagnostics[ref_name] = self.grid_signature(ref_grid)
        for name, grid in items[1:]:
            comparison = self.compare_grid_to_reference(grid, ref_grid)
            diagnostics[name] = comparison["diagnostics"]
            if grid.data.shape != ref_grid.data.shape:
                errors.append(f"{name}: shape {grid.data.shape} does not match {ref_name} {ref_grid.data.shape}")
            for key in ("crs", "transform"):
                if grid.profile.get(key) != ref_profile.get(key):
                    errors.append(f"{name}: {key} does not match {ref_name}")
            nodata_status = comparison["diagnostics"].get("nodata")
            if nodata_status in {"warning", "substantial", "complete"}:
                warnings.append(f"{name}: NoData status is {nodata_status}")

        return ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            diagnostics=diagnostics,
            status=self._status(not errors, warnings),
        )

    def compare_grid_to_reference(self, grid: RasterGrid, reference: RasterGrid) -> dict[str, Any]:
        grid_sig = self.grid_signature(grid)
        ref_sig = self.grid_signature(reference)
        diagnostics = {
            "crs": "match" if grid_sig["crs"] == ref_sig["crs"] else "mismatch",
            "resolution": "match" if grid_sig["resolution"] == ref_sig["resolution"] else "mismatch",
            "extent": "match" if grid_sig["bounds"] == ref_sig["bounds"] else "mismatch",
            "transform": "match" if grid_sig["transform"] == ref_sig["transform"] else "mismatch",
            "width_height": "match" if grid_sig["shape"] == ref_sig["shape"] else "mismatch",
            "bounds": "match" if grid_sig["bounds"] == ref_sig["bounds"] else "mismatch",
            "alignment": "match" if grid_sig["transform"] == ref_sig["transform"] and grid_sig["shape"] == ref_sig["shape"] else "mismatch",
            "dtype": "match" if str(grid.data.dtype) == str(reference.data.dtype) else "mismatch",
            "nodata": self.nodata_status(grid)["status"],
        }
        return {"diagnostics": diagnostics, "grid": grid_sig, "reference": ref_sig}

    def align_grid_to_reference(
        self,
        grid: RasterGrid,
        reference: RasterGrid,
        *,
        data_type: str = "continuous",
        fill_value: float = np.nan,
    ) -> tuple[RasterGrid, ValidationResult]:
        before = self.compare_grid_to_reference(grid, reference)
        corrections: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        diagnostics = dict(before["diagnostics"])
        resampling = Resampling.nearest if data_type == "categorical" else Resampling.bilinear

        needs_alignment = any(
            diagnostics[key] == "mismatch"
            for key in ("crs", "resolution", "extent", "transform", "width_height", "alignment")
        )
        if not needs_alignment:
            result = ValidationResult(
                valid=True,
                warnings=warnings,
                diagnostics=diagnostics,
                provenance={"input": before["grid"], "reference": before["reference"]},
                status=self._status(True, warnings),
            )
            return grid, result

        try:
            target_profile = reference.profile.copy()
            target_profile.update(dtype="float32", count=1, nodata=fill_value)
            destination = np.full(reference.data.shape, fill_value, dtype="float32")
            reproject(
                source=grid.data.astype("float32"),
                destination=destination,
                src_transform=grid.profile.get("transform"),
                src_crs=grid.profile.get("crs"),
                src_nodata=grid.profile.get("nodata"),
                dst_transform=target_profile.get("transform"),
                dst_crs=target_profile.get("crs"),
                dst_nodata=fill_value,
                resampling=resampling,
            )
            corrected = RasterGrid(
                grid.name,
                destination,
                target_profile,
                grid.source_type,
                {
                    **grid.metadata,
                    "spatial_qa": {
                        "correction_operation": "reproject_resample_align_to_reference_grid",
                        "data_type": data_type,
                        "resampling_method": resampling.name,
                    },
                },
            )
            corrections.append({
                "operation": "reproject_resample_align_to_reference_grid",
                "original_crs": before["grid"]["crs"],
                "target_crs": before["reference"]["crs"],
                "original_resolution": before["grid"]["resolution"],
                "target_resolution": before["reference"]["resolution"],
                "original_transform": before["grid"]["transform"],
                "target_transform": before["reference"]["transform"],
                "original_shape": before["grid"]["shape"],
                "target_shape": before["reference"]["shape"],
                "resampling_method": resampling.name,
            })
        except REPROJECTION_ERRORS as exc:
            errors.append(str(exc))
            return grid, ValidationResult(
                valid=False,
                errors=errors,
                diagnostics=diagnostics,
                provenance={"input": before["grid"], "reference": before["reference"]},
                status="failed",
            )

        after = self.compare_grid_to_reference(corrected, reference)
        after_diagnostics = {
            key: ("corrected" if diagnostics.get(key) == "mismatch" and value == "match" else value)
            for key, value in after["diagnostics"].items()
        }
        nodata = self.nodata_status(corrected)
        if nodata["status"] in {"warning", "substantial"}:
            warnings.append(f"{grid.name}: NoData fraction is {nodata['fraction']:.2%}")
        if nodata["status"] == "complete":
            errors.append(f"{grid.name}: raster is completely invalid after alignment")
        return corrected, ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            diagnostics=after_diagnostics,
            provenance={
                "input": before["grid"],
                "reference": before["reference"],
                "output": after["grid"],
                "nodata": nodata,
            },
            status=self._status(not errors, warnings),
            corrections=corrections,
        )

    def nodata_status(self, grid: RasterGrid, *, warning_threshold: float = 0.05, substantial_threshold: float = 0.40) -> dict[str, Any]:
        data = grid.data.astype("float32", copy=False)
        invalid = ~np.isfinite(data)
        nodata = grid.profile.get("nodata")
        if nodata is not None and not (isinstance(nodata, float) and np.isnan(nodata)):
            invalid |= data == float(nodata)
        fraction = float(np.count_nonzero(invalid) / max(data.size, 1))
        if np.isclose(fraction, 0.0):
            status = "none"
        elif np.isclose(fraction, 1.0):
            status = "complete"
        elif fraction >= substantial_threshold:
            status = "substantial"
        elif fraction >= warning_threshold:
            status = "warning"
        else:
            status = "small"
        return {"status": status, "fraction": fraction, "nodata_value": None if nodata is None else str(nodata)}

    def validate_vector_collection(
        self,
        collection: VectorFeatureCollection,
        *,
        target_crs: str | None = None,
        required_attributes: list[str] | None = None,
        identifier_field: str | None = None,
        geometry_types: list[str] | None = None,
        repair: bool = True,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        repaired = 0
        identifiers: set[Any] = set()
        duplicates: list[Any] = []
        required_attributes = required_attributes or []
        geometry_types = geometry_types or []
        features = list(collection.features)
        crs = collection.crs
        if not crs:
            errors.append("Vector CRS is missing.")
        diagnostics = {
            "crs": "match" if target_crs is None or crs == target_crs else "mismatch",
            "empty_geometry": "match",
            "invalid_geometry": "match",
            "geometry_type": "match",
            "required_attributes": "match",
            "duplicate_identifiers": "match",
        }
        for index, feature in enumerate(features):
            properties = feature.properties
            for field in required_attributes:
                if field not in properties:
                    diagnostics["required_attributes"] = "mismatch"
                    errors.append(f"Feature {index}: required attribute {field} is missing.")
            if identifier_field:
                identifier = properties.get(identifier_field)
                if identifier in identifiers:
                    duplicates.append(identifier)
                identifiers.add(identifier)
            try:
                geometry = shape(feature.geometry)
            except GEOMETRY_INPUT_ERRORS as exc:
                diagnostics["invalid_geometry"] = "mismatch"
                errors.append(f"Feature {index}: malformed geometry ({exc}).")
                continue
            if geometry.is_empty:
                diagnostics["empty_geometry"] = "mismatch"
                errors.append(f"Feature {index}: empty geometry.")
            if geometry_types and geometry.geom_type not in geometry_types:
                diagnostics["geometry_type"] = "mismatch"
                warnings.append(f"Feature {index}: geometry type {geometry.geom_type} not in {geometry_types}.")
            if not geometry.is_valid:
                if repair:
                    repaired_geometry = make_valid(geometry)
                    if repaired_geometry.is_empty or not repaired_geometry.is_valid:
                        diagnostics["invalid_geometry"] = "mismatch"
                        errors.append(f"Feature {index}: invalid geometry repair failed.")
                    else:
                        repaired += 1
                        diagnostics["invalid_geometry"] = "corrected"
                else:
                    diagnostics["invalid_geometry"] = "mismatch"
                    errors.append(f"Feature {index}: invalid geometry.")
        if duplicates:
            diagnostics["duplicate_identifiers"] = "mismatch"
            errors.append(f"Duplicate identifiers found: {duplicates}.")
        corrections = []
        if repaired:
            corrections.append({"operation": "safe_geometry_repair", "feature_count": repaired})
        return ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            diagnostics=diagnostics,
            provenance={
                "source": collection.source,
                "dataset_name": collection.name,
                "original_crs": crs,
                "target_crs": target_crs,
                "feature_count": len(features),
                "geometry_repairs": repaired,
            },
            status=self._status(not errors, warnings or corrections),
            corrections=corrections,
        )

    def grid_signature(self, grid: RasterGrid) -> dict[str, Any]:
        transform = grid.profile.get("transform")
        height, width = grid.data.shape
        bounds = None
        if transform is not None:
            try:
                bounds = tuple(float(value) for value in array_bounds(height, width, transform))
            except (TypeError, ValueError):
                bounds = None
        return {
            "crs": str(grid.profile.get("crs")),
            "resolution": tuple(float(value) for value in grid.profile.get("res", self._resolution(transform))),
            "transform": str(transform) if transform is not None else None,
            "shape": [int(height), int(width)],
            "bounds": None if bounds is None else [round(value, 9) for value in bounds],
            "dtype": str(grid.data.dtype),
            "nodata": None if grid.profile.get("nodata") is None else str(grid.profile.get("nodata")),
        }

    def _resolution(self, transform: Any) -> tuple[float, float]:
        if transform is None:
            return (0.0, 0.0)
        try:
            return (abs(float(transform.a)), abs(float(transform.e)))
        except AttributeError:
            return (0.0, 0.0)

    def _status(self, valid: bool, warnings: list[Any]) -> str:
        if not valid:
            return "failed"
        return "success_with_warnings" if warnings else "success"
