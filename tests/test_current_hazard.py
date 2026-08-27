import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from oasis.domain.hazard import interpret_hazard_class
from oasis.integrations.current_hazard import CoreAnalystCurrentHazard


def test_core_analyst_class_meanings_are_used_directly() -> None:
    assert interpret_hazard_class(1) == ("low", "Low")
    assert interpret_hazard_class(2) == ("medium", "Medium")
    assert interpret_hazard_class(3) == ("high", "High")


async def test_current_hazard_reads_the_latest_local_snapshot(tmp_path: Path) -> None:
    raster_path = tmp_path / "current.tif"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=1,
        height=1,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(-5, 56, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(np.array([[3]], dtype="uint8"), 1)

    now = datetime.now(timezone.utc).isoformat()
    (output_dir / "analysis_metadata.json").write_text(
        json.dumps(
            {
                "generated_at": now,
                "observation_start": now,
                "observation_end": now,
                "station_count": 4,
                "warnings": ["Prototype snapshot."],
            }
        ),
        encoding="utf-8",
    )
    service = CoreAnalystCurrentHazard(
        input_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        output_dir=output_dir,
        raster_path=raster_path,
        wms_url="https://example.test/wms",
        layer="test:current",
    )

    result = await service.lookup(55.5, -4.5)

    assert result.risk_level == "high"
    assert result.class_value == 3
    assert result.snapshot_time is not None
