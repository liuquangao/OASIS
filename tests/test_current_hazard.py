import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from hydromind.domain.hazard import interpret_hazard_class
from hydromind.integrations.current_hazard import CoreAnalystCurrentHazard
from hydromind.runtime import build_analysis_service
from hydromind.settings import Settings


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


def test_current_hazard_publishes_before_exposing_the_new_local_raster(tmp_path: Path) -> None:
    raster_path = tmp_path / "current.geotiff"
    temporary_path = tmp_path / "current.new.tif"
    raster_path.write_bytes(b"old")
    temporary_path.write_bytes(b"new")

    class RecordingPublisher:
        def __init__(self) -> None:
            self.published = None

        def publish_raster(self, path, *, name, label):
            assert raster_path.read_bytes() == b"old"
            self.published = (Path(path).read_bytes(), name, label)

    publisher = RecordingPublisher()
    service = CoreAnalystCurrentHazard(
        input_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        output_dir=tmp_path,
        raster_path=raster_path,
        wms_url="https://example.test/wms",
        layer="glasgow_flood:current_hazard_class_5m",
        publisher=publisher,  # type: ignore[arg-type]
    )

    service._publish_and_replace(temporary_path)

    assert publisher.published == (
        b"new",
        "current_hazard_class_5m",
        "Current pluvial hazard snapshot",
    )
    assert raster_path.read_bytes() == b"new"


def test_runtime_shares_one_publisher_with_current_and_full_analysis() -> None:
    service = build_analysis_service(Settings(), publish=True)

    assert service.current_hazard._publisher is service._publisher
