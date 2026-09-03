"""Historical forecast-input and decision-stability validation for Glasgow."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import time
from typing import Any

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio

from core_analyst.analysts.data_zone_assessment import (
    run_data_zone_flood_priority_assessment,
)
from core_analyst.analysts.pluvial_prediction import PluvialPredictionAnalyst
from core_analyst.data_sources import DataSource, RasterGrid, RealTimeAPISource, write_raster
from core_analyst.real_data_inputs import prepare_real_exposure_vulnerability_inputs
from core_analyst.workflows.hydromind_real_data import (
    build_historical_hydrological_sources,
    build_hydromind_input_sources,
)
from core_analyst.utils.config import load_config


CEDA_UKV_ROOT = "https://dap.ceda.ac.uk/badc/ukmo-nwp/data/ukv-grib"
EVENT_REPORT_URL = (
    "https://www.metoffice.gov.uk/binaries/content/assets/metofficegovuk/pdf/"
    "weather/learn-about/uk-past-events/interesting/2023/2023_07_scotland_rain.pdf"
)


@dataclass
class CedaCredentials:
    access_token: str | None = None
    username: str | None = None
    password: str | None = None


class ArrayRainfallSource(DataSource):
    def __init__(self, name: str, grid: RasterGrid, metadata: dict[str, Any]):
        self.name = name
        self.grid = grid
        self.metadata = metadata

    def get_data(self, reference: RasterGrid | None = None) -> RasterGrid:
        if reference is None or self.grid.data.shape != reference.data.shape:
            raise ValueError("Historical rainfall grid must match the reference grid")
        return RasterGrid(
            self.name,
            self.grid.data.copy(),
            reference.profile.copy(),
            "historical",
            self.metadata,
        )


class HistoricalPointRainfallSource(RealTimeAPISource):
    source_name = "nrfa_historical_catchment_rainfall"
    data_type = "historical_observation"

    def __init__(self, stations: list[dict[str, Any]]):
        self.stations = stations

    def retrieve_observations(self) -> dict[str, Any]:
        return {"stations": self.stations}

    def observations_to_grid(
        self, observations: dict[str, Any], reference: RasterGrid
    ) -> np.ndarray:
        stations = observations["stations"]
        return self._idw_grid(
            reference,
            [item["easting"] for item in stations],
            [item["northing"] for item in stations],
            [item["rainfall_mm"] / 24.0 for item in stations],
        )


def run_historical_flood_validation(
    *,
    input_dir: str | Path,
    config_dir: str | Path,
    output_dir: str | Path,
    issue_time: datetime,
    horizon_hours: int,
    forecast_path: str | Path | None,
    credentials: CedaCredentials,
    hazard_threshold: int = 2,
    priority_scenario: str = "social_equity",
) -> dict[str, Any]:
    """Run a no-leakage October 2023 forecast and ranking comparison."""

    issue_time = _as_utc(issue_time)
    if horizon_hours < 1 or horizon_hours > 48:
        raise ValueError("Historical forecast horizon must be between 1 and 48 hours")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path, source_url, recovery = _resolve_forecast(
        output_dir,
        issue_time,
        forecast_path=forecast_path,
        credentials=credentials,
    )
    source_issue_time = _forecast_reference_time(source_path)
    if source_issue_time > issue_time:
        raise ValueError(
            f"Forecast leakage: source issue time {source_issue_time.isoformat()} is later "
            f"than requested issue time {issue_time.isoformat()}"
        )

    static_sources = build_hydromind_input_sources(input_dir, rainfall_source="mock")
    reference = static_sources["dem"].get_data()
    forecast_grid, forecast_metadata = _forecast_rainfall_grid(
        source_path,
        reference,
        issue_time=source_issue_time,
        horizon_hours=horizon_hours,
        output_path=output_dir / "forecast_rainfall_mm_per_hour.tif",
    )
    stations = _historical_nrfa_stations(
        input_dir,
        valid_date=(issue_time + timedelta(hours=horizon_hours)).date().isoformat(),
    )
    if not stations:
        raise ValueError("No NRFA historical rainfall observations were available for validation")
    observed_grid = HistoricalPointRainfallSource(stations).get_data(reference)
    observed_path = output_dir / "observed_rainfall_mm_per_hour.tif"
    write_raster(observed_path, observed_grid)
    baseline_stations = _historical_nrfa_stations(
        input_dir,
        valid_date=issue_time.date().isoformat(),
    )
    if not baseline_stations:
        raise ValueError("No NRFA baseline rainfall observations were available for comparison")
    baseline_source = HistoricalPointRainfallSource(baseline_stations)
    baseline_grid = baseline_source.get_data(reference)
    baseline_path = output_dir / "baseline_rainfall_mm_per_hour.tif"
    write_raster(baseline_path, baseline_grid)

    metrics = _rainfall_metrics(forecast_grid, stations)
    forecast_source = ArrayRainfallSource(
        "ukv_historical_forecast",
        forecast_grid,
        {**forecast_metadata, "issue_time": source_issue_time.isoformat()},
    )
    observed_source = ArrayRainfallSource(
        "nrfa_historical_observation",
        observed_grid,
        {
            "valid_date": (issue_time + timedelta(hours=horizon_hours)).date().isoformat(),
            "station_count": len(stations),
            "units": "mm/hour derived from catchment daily rainfall",
        },
    )
    baseline_rainfall_source = ArrayRainfallSource(
        "nrfa_baseline_observation",
        baseline_grid,
        {
            "valid_date": issue_time.date().isoformat(),
            "station_count": len(baseline_stations),
            "units": "mm/hour derived from catchment daily rainfall",
        },
    )
    config = load_config(Path(config_dir) / "pluvial_prediction_config.yaml")
    forecast_hazard = PluvialPredictionAnalyst(config, output_dir / "forecast_hazard").run(
        static_sources,
        observed_source,
        forecast_source,
        prediction_horizon_hours=horizon_hours,
    )
    observed_hazard = PluvialPredictionAnalyst(config, output_dir / "observed_hazard").run(
        static_sources,
        observed_source,
        observed_source,
        prediction_horizon_hours=horizon_hours,
    )
    baseline_hazard = PluvialPredictionAnalyst(config, output_dir / "baseline_hazard").run(
        static_sources,
        baseline_rainfall_source,
        baseline_rainfall_source,
        prediction_horizon_hours=horizon_hours,
    )

    prepared = prepare_real_exposure_vulnerability_inputs(
        input_dir,
        processed_dir=Path(input_dir) / "processed",
    )
    if not prepared.data_zone_geography:
        raise ValueError("Prepared Census 2022 Data Zone geography is unavailable")
    forecast_assessment = run_data_zone_flood_priority_assessment(
        hazard_raster=forecast_hazard["output_paths"]["future_hazard_class"],
        data_zones=prepared.data_zone_geography,
        buildings=prepared.buildings,
        critical_services=prepared.critical_services,
        output_dir=output_dir / "forecast_assessment",
        scenario="historical_forecast",
        hazard_threshold=hazard_threshold,
        priority_scenario=priority_scenario,
        provenance={"forecast_issue_time": source_issue_time.isoformat()},
    )
    observed_assessment = run_data_zone_flood_priority_assessment(
        hazard_raster=observed_hazard["output_paths"]["future_hazard_class"],
        data_zones=prepared.data_zone_geography,
        buildings=prepared.buildings,
        critical_services=prepared.critical_services,
        output_dir=output_dir / "observed_assessment",
        scenario="historical_observation_reconstruction",
        hazard_threshold=hazard_threshold,
        priority_scenario=priority_scenario,
        provenance={"observation_date": stations[0]["date"]},
    )
    ranking_metrics = _ranking_metrics(
        Path(forecast_assessment["outputs"]["data_zone_assessment"]),
        Path(observed_assessment["outputs"]["data_zone_assessment"]),
    )
    metrics.update(ranking_metrics)
    metrics["hazard_area_distribution"] = {
        "baseline_day": _class_distribution(
            baseline_hazard["output_paths"]["future_hazard_class"]
        ),
        "event_day": _class_distribution(
            observed_hazard["output_paths"]["future_hazard_class"]
        ),
    }
    river_context = _historical_nrfa_flow_context(
        input_dir,
        start_date=issue_time.date().isoformat(),
        end_date=(issue_time + timedelta(hours=horizon_hours)).date().isoformat(),
    )
    comparison_path = output_dir / "historical_validation_comparison.png"
    _plot_comparison(forecast_grid.data, observed_grid.data, metrics, comparison_path)

    summary = {
        "issue_time": source_issue_time.isoformat(),
        "valid_until": (source_issue_time + timedelta(hours=horizon_hours)).isoformat(),
        "area": "Glasgow",
        "forecast_source": "Met Office UKV operational forecast archive via CEDA",
        "no_time_leakage": source_issue_time <= issue_time,
        "observation_sources": [
            "NRFA catchment daily rainfall",
            "NRFA gauged daily flow (context)",
            "Met Office October 2023 event report",
        ],
        "nrfa_river_flow_context": river_context,
        "historical_warning_evidence": {
            "source": "Met Office 6–7 October 2023 event report",
            "url": EVENT_REPORT_URL,
            "role": "Official post-event warning and impact context; not an inundation polygon.",
        },
        **metrics,
        "forecast_priority_top_10": forecast_assessment["summary"]["top_areas"],
        "observed_priority_top_10": observed_assessment["summary"]["top_areas"],
        "interpretation": (
            "Forecast-input and decision-stability validation. NRFA catchment daily rainfall "
            "is an observation proxy; no Glasgow inundation footprint is available, so this is "
            "not flood-extent accuracy."
        ),
    }
    summary_path = output_dir / "historical_validation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    outputs = {
        "forecast_rainfall": str(output_dir / "forecast_rainfall_mm_per_hour.tif"),
        "observed_rainfall": str(observed_path),
        "baseline_rainfall": str(baseline_path),
        "forecast_hazard_class": forecast_hazard["output_paths"]["future_hazard_class"],
        "observed_hazard_class": observed_hazard["output_paths"]["future_hazard_class"],
        "baseline_hazard_class": baseline_hazard["output_paths"]["future_hazard_class"],
        "priority_by_data_zone": forecast_assessment["outputs"]["priority_by_data_zone"],
        "observed_priority_by_data_zone": observed_assessment["outputs"]["priority_by_data_zone"],
        "historical_validation_summary": str(summary_path),
        "historical_validation_figure": str(comparison_path),
    }
    return {
        "status": "success",
        "summary": summary,
        "outputs": outputs,
        "provenance": {
            "forecast_archive_url": source_url,
            "forecast_file": source_path.name,
            "forecast_reference_time": source_issue_time.isoformat(),
            "event_report_url": EVENT_REPORT_URL,
            "credentials_persisted": False,
            "no_temporal_leakage": source_issue_time <= issue_time,
        },
        "warnings": [
            {
                "code": "no_inundation_ground_truth",
                "message": summary["interpretation"],
            }
        ],
        "recovery": recovery,
    }


def _resolve_forecast(
    output_dir: Path,
    issue_time: datetime,
    *,
    forecast_path: str | Path | None,
    credentials: CedaCredentials,
) -> tuple[Path, str, list[dict[str, Any]]]:
    if forecast_path:
        path = Path(forecast_path)
        if not path.is_file():
            raise FileNotFoundError(f"Historical UKV file not found: {path}")
        return path, "local CEDA archive file", []
    token = credentials.access_token or _ceda_token(credentials)
    if not token:
        raise ValueError(
            "Historical validation needs CEDA_ACCESS_TOKEN, CEDA_USERNAME/CEDA_PASSWORD, "
            "or HYDROMIND_HISTORICAL_UKV_PATH."
        )
    stamp = issue_time.strftime("%Y%m%d%H%M")
    filename = f"{stamp}_u1096_ng_umqv_Wholesale4.grib"
    url = f"{CEDA_UKV_ROOT}/{issue_time:%Y/%m/%d}/{filename}"
    target = output_dir / filename
    recovery: list[dict[str, Any]] = []
    if target.is_file():
        return target, url, [{"action": "reuse_cache", "outcome": "success"}]
    for attempt in range(1, 3):
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as response:
                if response.status_code == 200:
                    temporary = target.with_suffix(target.suffix + ".part")
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                    temporary.replace(target)
                    return target, url, recovery
                response.read()
        recovery.append(
            {
                "action": "retry" if attempt == 1 else "stop",
                "attempt": attempt,
                "status_code": response.status_code,
                "outcome": "failed",
            }
        )
        if response.status_code == 429:
            time.sleep(float(response.headers.get("Retry-After", "2")))
        elif response.status_code >= 500 and attempt == 1:
            time.sleep(2)
        else:
            break
    raise ValueError(
        f"CEDA UKV download failed with HTTP {recovery[-1]['status_code']}; "
        "check archive permission or provide HYDROMIND_HISTORICAL_UKV_PATH."
    )


def _ceda_token(credentials: CedaCredentials) -> str | None:
    if not credentials.username or not credentials.password:
        return None
    response = httpx.post(
        "https://services.ceda.ac.uk/api/token/create/",
        auth=(credentials.username, credentials.password),
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _forecast_reference_time(path: Path) -> datetime:
    match = re.search(r"(20\d{10})", path.name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=UTC)
    with rasterio.open(path) as dataset:
        value = dataset.tags().get("forecast_reference_time")
        if value:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError("Historical forecast file has no auditable forecast reference time")


def _forecast_rainfall_grid(
    path: Path,
    reference: RasterGrid,
    *,
    issue_time: datetime,
    horizon_hours: int,
    output_path: Path,
) -> tuple[RasterGrid, dict[str, Any]]:
    from core_analyst.data_adapters import AlignedRasterSource
    from rasterio.enums import Resampling

    source = path
    metadata: dict[str, Any] = {"source_file": path.name}
    if path.suffix.lower() in {".tif", ".tiff"}:
        with rasterio.open(path) as dataset:
            tags = dataset.tags()
            units = tags.get("units") or tags.get("UNIT")
            representation = tags.get("rainfall_representation")
            if representation not in {"rate_mm_per_hour", "accumulation_mm"}:
                raise ValueError(
                    "A local historical rainfall GeoTIFF must declare rainfall_representation "
                    "as rate_mm_per_hour or accumulation_mm."
                )
            data = dataset.read(1).astype("float32")
            if representation == "accumulation_mm":
                data /= float(horizon_hours)
            profile = dataset.profile.copy()
            profile.update(driver="GTiff", count=1, dtype="float32", nodata=np.nan)
            with rasterio.open(output_path, "w", **profile) as target:
                target.write(data, 1)
                target.update_tags(
                    units="mm/hour",
                    forecast_reference_time=issue_time.isoformat(),
                    processing=f"{representation} normalized to the requested horizon",
                )
            source = output_path
            metadata.update({"source_units": units, "aggregation": representation})
    else:
        with rasterio.open(path) as dataset:
            groups: dict[str, list[tuple[int, dict[str, str], str]]] = {}
            for band in range(1, dataset.count + 1):
                tags = dataset.tags(band)
                text = " ".join([dataset.descriptions[band - 1] or "", *map(str, tags.values())]).lower()
                if "precip" in text or "rain" in text:
                    lead_seconds = int(float(tags.get("GRIB_FORECAST_SECONDS", 0)))
                    if 0 < lead_seconds <= horizon_hours * 3600:
                        parameter = tags.get("GRIB_ELEMENT") or tags.get("GRIB_SHORT_NAME") or "precipitation"
                        groups.setdefault(parameter, []).append((band, tags, text))
            if not groups:
                raise ValueError(
                    "The configured UKV GRIB does not contain precipitation bands within the "
                    "requested horizon; provide an appropriate UKV precipitation GRIB through "
                    "HYDROMIND_HISTORICAL_UKV_PATH."
                )
            parameter = sorted(
                groups,
                key=lambda key: (
                    key.upper() in {"APCP", "TP", "TOTAL_PRECIPITATION"},
                    len(groups[key]),
                ),
                reverse=True,
            )[0]
            candidates = sorted(
                groups[parameter],
                key=lambda item: int(float(item[1].get("GRIB_FORECAST_SECONDS", 0))),
            )
            units = candidates[0][1].get("GRIB_UNIT", "")
            all_text = " ".join(item[2] for item in candidates)
            arrays = [dataset.read(item[0]).astype("float32") for item in candidates]
            if "/s" in units or "s-1" in units:
                leads = [int(float(item[1].get("GRIB_FORECAST_SECONDS", 0))) for item in candidates]
                intervals = np.diff([0, *leads])
                total_mm = sum(array * seconds for array, seconds in zip(arrays, intervals))
                aggregation = "precipitation rate integrated over forecast intervals"
            elif "from forecast start" in all_text or "cumulative" in all_text:
                total_mm = arrays[-1]
                aggregation = "last cumulative precipitation field within horizon"
            else:
                total_mm = np.sum(arrays, axis=0)
                aggregation = "interval precipitation fields summed within horizon"
            if units.strip().lower() in {"m", "m of water equivalent"}:
                total_mm *= 1000.0
            data = total_mm / float(horizon_hours)
            profile = dataset.profile.copy()
            profile.update(driver="GTiff", count=1, dtype="float32", nodata=np.nan)
            with rasterio.open(output_path, "w", **profile) as target:
                target.write(data, 1)
                target.update_tags(
                    forecast_reference_time=issue_time.isoformat(),
                    source_bands=",".join(str(item[0]) for item in candidates),
                    units="mm/hour",
                    processing=aggregation,
                )
            source = output_path
            metadata.update(
                {
                    "parameter": parameter,
                    "source_units": units,
                    "selected_bands": [item[0] for item in candidates],
                    "aggregation": aggregation,
                }
            )
    aligned = AlignedRasterSource(
        "historical_forecast_rainfall",
        source,
        Resampling.bilinear,
    ).get_data(reference=reference)
    aligned.metadata.update(metadata)
    return aligned, metadata


def _class_distribution(path: str | Path) -> dict[str, float]:
    with rasterio.open(path) as dataset:
        values = dataset.read(1)
        valid = dataset.read_masks(1) > 0
    total = int(np.count_nonzero(valid))
    return {
        label: round(100 * int(np.count_nonzero(valid & (values == value))) / total, 2)
        if total else 0.0
        for value, label in ((1, "low_percent"), (2, "medium_percent"), (3, "high_percent"))
    }


def _historical_nrfa_flow_context(
    input_dir: str | Path,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    source = build_historical_hydrological_sources(input_dir).get("nrfa_historical_river_flow")
    if source is None:
        return []
    records = []
    for station_id in source.station_ids():
        series = source.daily_values(station_id, start_date=start_date, end_date=end_date)
        values = [item["value"] for item in series["values"] if item["value"] is not None]
        if values:
            records.append(
                {
                    "station_id": station_id,
                    "name": series["metadata"].get("station", {}).get("name"),
                    "maximum_daily_flow": max(values),
                    "record_count": len(values),
                }
            )
    return records


def _historical_nrfa_stations(input_dir: str | Path, *, valid_date: str) -> list[dict[str, Any]]:
    source = build_historical_hydrological_sources(input_dir).get("nrfa_historical_rainfall")
    if source is None:
        return []
    stations = []
    for station_id in source.station_ids():
        series = source.daily_values(station_id, start_date=valid_date, end_date=valid_date)
        value = series["values"][0]["value"] if series["values"] else None
        metadata = series["metadata"]
        grid_reference = metadata.get("station", {}).get("gridReference")
        if value is None or not grid_reference:
            continue
        easting, northing = _os_grid_reference(str(grid_reference))
        stations.append(
            {
                "station_id": station_id,
                "name": metadata.get("station", {}).get("name"),
                "date": valid_date,
                "rainfall_mm": float(value),
                "easting": easting,
                "northing": northing,
            }
        )
    return stations


def _os_grid_reference(value: str) -> tuple[float, float]:
    cleaned = re.sub(r"\s+", "", value.upper())
    if not re.fullmatch(r"[A-Z]{2}\d{2,10}", cleaned):
        raise ValueError(f"Unsupported OS grid reference: {value}")
    first, second = ord(cleaned[0]) - 65, ord(cleaned[1]) - 65
    if first > 7:
        first -= 1
    if second > 7:
        second -= 1
    e100 = ((first - 2) % 5) * 5 + (second % 5)
    n100 = (19 - (first // 5) * 5) - (second // 5)
    digits = cleaned[2:]
    half = len(digits) // 2
    easting = int(digits[:half].ljust(5, "0")) + e100 * 100000
    northing = int(digits[half:].ljust(5, "0")) + n100 * 100000
    return float(easting), float(northing)


def _rainfall_metrics(grid: RasterGrid, stations: list[dict[str, Any]]) -> dict[str, Any]:
    transform = grid.profile["transform"]
    predictions = []
    observations = []
    for station in stations:
        col, row = ~transform * (station["easting"], station["northing"])
        row, col = int(row), int(col)
        if 0 <= row < grid.data.shape[0] and 0 <= col < grid.data.shape[1]:
            value = float(grid.data[row, col]) * 24.0
            if np.isfinite(value):
                predictions.append(value)
                observations.append(float(station["rainfall_mm"]))
    if not predictions:
        return {
            "rainfall_bias_mm": None,
            "rainfall_mae_mm": None,
            "spatial_correlation": None,
            "matched_station_count": 0,
        }
    residual = np.asarray(predictions) - np.asarray(observations)
    correlation = (
        float(np.corrcoef(predictions, observations)[0, 1])
        if len(predictions) >= 2 and np.std(predictions) and np.std(observations)
        else None
    )
    return {
        "rainfall_bias_mm": float(np.mean(residual)),
        "rainfall_mae_mm": float(np.mean(np.abs(residual))),
        "spatial_correlation": correlation,
        "matched_station_count": len(predictions),
    }


def _ranking_metrics(forecast_csv: Path, observed_csv: Path) -> dict[str, Any]:
    def ranks(path: Path) -> dict[str, int]:
        with path.open(encoding="utf-8") as handle:
            return {
                str(row["id"]): int(float(row["priority_rank"]))
                for row in csv.DictReader(handle)
                if row.get("priority_rank")
            }

    forecast = ranks(forecast_csv)
    observed = ranks(observed_csv)
    common = sorted(forecast.keys() & observed.keys())
    forecast_top = set(sorted(common, key=forecast.get)[:10])
    observed_top = set(sorted(common, key=observed.get)[:10])
    correlation = None
    if len(common) >= 2:
        correlation = float(
            np.corrcoef(
                [forecast[unit] for unit in common],
                [observed[unit] for unit in common],
            )[0, 1]
        )
    return {
        "top_10_overlap": len(forecast_top & observed_top),
        "rank_correlation": correlation,
    }


def _plot_comparison(
    forecast: np.ndarray,
    observed: np.ndarray,
    metrics: dict[str, Any],
    path: Path,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), constrained_layout=True)
    for axis, data, title in (
        (axes[0], forecast * 24.0, "UKV forecast · 24 h rainfall"),
        (axes[1], observed * 24.0, "NRFA observation reconstruction"),
    ):
        image = axis.imshow(data, cmap="Blues", vmin=0)
        axis.set_title(title)
        axis.axis("off")
        fig.colorbar(image, ax=axis, fraction=0.046, label="mm")
    axes[2].axis("off")
    axes[2].text(
        0.03,
        0.95,
        "Validation summary\n\n"
        f"Matched gauges: {metrics.get('matched_station_count')}\n"
        f"Rainfall MAE: {_fmt(metrics.get('rainfall_mae_mm'))} mm\n"
        f"Bias: {_fmt(metrics.get('rainfall_bias_mm'))} mm\n"
        f"Spatial r: {_fmt(metrics.get('spatial_correlation'))}\n"
        f"Top-10 overlap: {metrics.get('top_10_overlap')}/10\n"
        f"Rank r: {_fmt(metrics.get('rank_correlation'))}\n\n"
        "Not flood-extent accuracy.",
        va="top",
        fontsize=11,
    )
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _fmt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.3f}"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
