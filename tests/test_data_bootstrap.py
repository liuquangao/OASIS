from __future__ import annotations

from oasis.cli import build_parser
from oasis.data_bootstrap import _copernicus_tiles, _worldcover_tiles
from core_analyst.real_data_inputs import prepare_real_exposure_vulnerability_inputs


def test_public_tile_names_cover_glasgow() -> None:
    bounds = (-4.5, 55.7, -3.9, 56.0)

    assert _copernicus_tiles(bounds) == [
        "Copernicus_DSM_COG_10_N55_00_W005_00_DEM",
        "Copernicus_DSM_COG_10_N55_00_W004_00_DEM",
    ]
    assert _worldcover_tiles(bounds) == ["N54W006"]


def test_cli_exposes_data_bootstrap() -> None:
    args = build_parser().parse_args(
        ["data", "bootstrap", "--resolution", "30", "--include-exposure"]
    )

    assert args.command == "data"
    assert args.data_command == "bootstrap"
    assert args.resolution == 30
    assert args.include_exposure is True


def test_input_preparation_reports_missing_optional_data(tmp_path) -> None:
    prepared = prepare_real_exposure_vulnerability_inputs(tmp_path)

    assert prepared.census_attributes is None
    assert prepared.buildings is None
    assert {item["dataset"] for item in prepared.unavailable} >= {
        "Scotland Census 2022",
        "Data Zone 2022 boundaries",
        "SIMD 2020v2",
    }
