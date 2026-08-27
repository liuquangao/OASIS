from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import rasterio

from core_analyst.data_sources import RasterGrid


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ValueError("Raster input contract failed:\n" + "\n".join(f"- {e}" for e in self.errors))


class RasterValidator:
    def validate_files(self, raster_paths: dict[str, str | Path]) -> ValidationResult:
        errors: list[str] = []
        reference = None

        for name, path_value in raster_paths.items():
            path = Path(path_value)
            if not path.exists():
                errors.append(f"{name}: file does not exist at {path}")
                continue

            try:
                with rasterio.open(path) as dataset:
                    signature = {
                        "crs": dataset.crs,
                        "transform": dataset.transform,
                        "bounds": dataset.bounds,
                        "res": dataset.res,
                        "shape": dataset.shape,
                    }
            except Exception as exc:  # pragma: no cover - rasterio error text varies
                errors.append(f"{name}: could not open raster ({exc})")
                continue

            if reference is None:
                reference = (name, signature)
                continue

            ref_name, ref = reference
            for key in ("crs", "res", "shape"):
                if signature[key] != ref[key]:
                    errors.append(f"{name}: {key} does not match {ref_name}")
            if signature["bounds"] != ref["bounds"]:
                errors.append(f"{name}: spatial extent does not match {ref_name}")
            if signature["transform"] != ref["transform"]:
                errors.append(f"{name}: pixel grid transform does not match {ref_name}")

        return ValidationResult(valid=not errors, errors=errors)

    def validate_grids(self, grids: dict[str, RasterGrid]) -> ValidationResult:
        errors: list[str] = []
        items = list(grids.items())
        if not items:
            return ValidationResult(valid=False, errors=["No raster grids provided."])

        ref_name, ref_grid = items[0]
        ref_profile = ref_grid.profile
        for name, grid in items[1:]:
            if grid.data.shape != ref_grid.data.shape:
                errors.append(f"{name}: shape {grid.data.shape} does not match {ref_name} {ref_grid.data.shape}")
            for key in ("crs", "transform"):
                if grid.profile.get(key) != ref_profile.get(key):
                    errors.append(f"{name}: {key} does not match {ref_name}")

        return ValidationResult(valid=not errors, errors=errors)
