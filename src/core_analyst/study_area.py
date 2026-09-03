from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from rasterio.coords import BoundingBox
from rasterio.crs import CRS
from rasterio.warp import transform_bounds


DEFAULT_GLASGOW_1KM_BUFFER = Path("HYDROMIND_Polygon") / "HYDROMIND_Polygon" / "Glasgow_City_1km_buffer.shp"


@dataclass(frozen=True)
class StudyAreaBounds:
    name: str
    path: str
    bounds: BoundingBox
    crs: CRS | None
    metadata: dict[str, str]

    def for_crs(self, target_crs) -> BoundingBox:
        if self.crs is None or target_crs is None or CRS.from_user_input(target_crs) == self.crs:
            return self.bounds
        left, bottom, right, top = transform_bounds(
            self.crs,
            CRS.from_user_input(target_crs),
            self.bounds.left,
            self.bounds.bottom,
            self.bounds.right,
            self.bounds.top,
            densify_pts=21,
        )
        return BoundingBox(left=left, bottom=bottom, right=right, top=top)


def load_glasgow_1km_buffer_bounds(input_dir: str | Path = "Input") -> StudyAreaBounds | None:
    path = Path(input_dir) / DEFAULT_GLASGOW_1KM_BUFFER
    if not path.exists():
        return None
    return read_shapefile_bounds(path, name="glasgow_city_1km_buffer")


def read_shapefile_bounds(path: str | Path, name: str | None = None) -> StudyAreaBounds:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 68:
        raise ValueError(f"Shapefile header is too short: {path}")
    xmin, ymin, xmax, ymax = struct.unpack("<4d", header[36:68])
    prj = path.with_suffix(".prj")
    crs = None
    if prj.exists():
        text = prj.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            crs = CRS.from_wkt(text)
    return StudyAreaBounds(
        name=name or path.stem,
        path=str(path),
        bounds=BoundingBox(left=xmin, bottom=ymin, right=xmax, top=ymax),
        crs=crs,
        metadata={
            "source": "shapefile_header_bbox",
            "boundary_role": "study_area",
            "buffer": "1km" if "1km" in path.stem.lower() else "unspecified",
        },
    )
