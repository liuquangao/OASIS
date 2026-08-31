from __future__ import annotations

import json
import hashlib

from oasis.cli import build_parser
from oasis.reproducible_data import (
    STUDY_AREA_PATH,
    load_source_lock,
    preflight_exact_data,
)


def test_exact_profile_locks_the_current_glasgow_grid() -> None:
    lock = load_source_lock()
    assert lock["profile"] == "glasgow-5m-exact"
    assert lock["analysis_grid"] == {
        "crs": "EPSG:27700",
        "bounds": [249424.0, 655461.5, 271434.0, 674071.5],
        "resolution_m": 5.0,
        "width": 4402,
        "height": 3722,
    }
    assert len(lock["sources"]["terrain"]["tiles"]) == 24
    assert len(lock["sources"]["terrain"]["supplemental_tiles"]) == 2


def test_legacy_edge_patch_matches_the_source_lock() -> None:
    lock = load_source_lock()
    path = STUDY_AREA_PATH.parent / "glasgow-dtm-edge-patch.csv"
    assert sum(1 for _ in path.open(encoding="utf-8")) - 1 == 3_924
    assert hashlib.sha256(path.read_bytes()).hexdigest() == lock["legacy_edge_patch"]["sha256"]


def test_preflight_stops_before_large_downloads_when_lcm_is_missing(tmp_path) -> None:
    result = preflight_exact_data(tmp_path / "missing", accept_licences=False)
    assert result["ok"] is False
    assert result["api_keys_required_for_data_build"] == []
    assert result["download_bytes"] == 9_149_108_748
    assert any("gb2019lcm25m.tif" in error for error in result["errors"])


def test_study_area_is_a_versioned_bng_polygon() -> None:
    data = json.loads(STUDY_AREA_PATH.read_text(encoding="utf-8"))
    assert data["crs"]["properties"]["name"].endswith("27700")
    assert data["features"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}


def test_cli_exposes_exact_data_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["data", "verify"])
    assert args.data_command == "verify"
    args = parser.parse_args(
        ["data", "rebuild", "--lcm2019", "gb2019lcm25m.tif", "--accept-licences"]
    )
    assert args.accept_licences is True
    args = parser.parse_args(["data", "prepare-risk"])
    assert args.data_command == "prepare-risk"
    args = parser.parse_args(["priority-assessment"])
    assert args.priority_scenario == "social_equity"
    assert args.forecast_horizon_hours == 24
