from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def create_demo_rasters(data_dir: str | Path, config: dict) -> dict[str, Path]:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    demo = config["demo"]
    width = int(demo["width"])
    height = int(demo["height"])
    transform = from_origin(
        float(demo["origin_x"]),
        float(demo["origin_y"]),
        float(demo["pixel_size"]),
        float(demo["pixel_size"]),
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": demo["crs"],
        "transform": transform,
        "nodata": None,
    }

    yy, xx = np.mgrid[0:height, 0:width]
    x = xx / max(width - 1, 1)
    y = yy / max(height - 1, 1)

    valley = 1.0 - np.exp(-((x - 0.45) ** 2) / 0.018)
    dem = 22 + 75 * valley + 18 * y
    slope = np.abs(np.gradient(dem)[0]) + np.abs(np.gradient(dem)[1])
    flow = ((1.0 - valley) * 850 + np.maximum(y - 0.2, 0) * 250) ** 1.2
    impervious = np.clip(
        35
        + 55 * np.exp(-((x - 0.50) ** 2 + (y - 0.50) ** 2) / 0.045)
        + 18 * np.exp(-((x - 0.25) ** 2 + (y - 0.72) ** 2) / 0.02),
        0,
        100,
    )
    rainfall = 12 + 16 * np.exp(-((x - 0.55) ** 2 + (y - 0.35) ** 2) / 0.08) + 4 * y

    rasters = {
        "dem": dem,
        "slope": slope,
        "flow_accumulation": flow,
        "imperviousness": impervious,
        "rainfall": rainfall,
    }

    paths: dict[str, Path] = {}
    for name, values in rasters.items():
        path = data_dir / f"{name}.tif"
        with rasterio.open(path, "w", **profile) as dataset:
            dataset.write(values.astype("float32"), 1)
        paths[name] = path
    return paths
