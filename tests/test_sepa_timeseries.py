from __future__ import annotations

import httpx
import pytest

from hydromind.integrations.sepa import SepaTimeSeriesClient


@pytest.mark.asyncio
async def test_recent_level_summary_parses_sepa_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "waterlevels.sepa.org.uk":
            return httpx.Response(
                200,
                text='<p class="data-visualisation-statistics--caption">Normal range 0.214m to 3.617m</p>',
            )
        query = dict(request.url.params)
        if query["request"] == "getTimeseriesList":
            return httpx.Response(
                200,
                json=[
                    {
                        "station_no": "133074",
                        "station_name": "Daldowie",
                        "station_latitude": "55.82998694",
                        "station_longitude": "-4.121948322",
                        "stationparameter_name": "Level",
                        "stationparameter_no": "SG",
                        "ts_name": "15minute",
                        "ts_id": "52849010",
                        "ts_path": "1/133074/SG/15m.Cmd",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "ts_id": "52849010",
                    "rows": "3",
                    "columns": "Timestamp,Value,Quality Code",
                    "data": [
                        ["2026-08-02T00:00:00.000Z", 0.30, 0],
                        ["2026-08-02T01:00:00.000Z", 0.32, 0],
                        ["2026-08-02T02:00:00.000Z", 0.34, 0],
                    ],
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        summary = await SepaTimeSeriesClient(http_client).recent_level_summary(
            "133074"
        )

    assert summary.station.name == "Daldowie"
    assert summary.reading_count == 3
    assert len(summary.recent_readings) == 3
    assert summary.latest_value_m == 0.34
    assert summary.change_per_hour_m == pytest.approx(0.02)
    assert summary.trend == "rising"
    assert summary.normal_range_low_m == pytest.approx(0.214)
    assert summary.normal_range_high_m == pytest.approx(3.617)
    assert summary.relative_level_percent == pytest.approx(3.7)
    assert summary.level_state == "normal"
    assert summary.provenance.integration == (
        "hydromind.integrations.sepa.SepaTimeSeriesClient"
    )


@pytest.mark.asyncio
async def test_recent_water_levels_near_location_combines_station_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "waterlevels.sepa.org.uk":
            return httpx.Response(
                200,
                text='<p class="data-visualisation-statistics--caption">Normal range 0.214m to 3.617m</p>',
            )
        query = dict(request.url.params)
        if query["request"] == "getStationList":
            return httpx.Response(
                200,
                json=[
                    {
                        "station_no": "133074",
                        "station_name": "Daldowie",
                        "station_latitude": "55.82998694",
                        "station_longitude": "-4.121948322",
                        "stationparameter_name": "Level",
                        "stationparameter_no": "SG",
                    }
                ],
            )
        if query["request"] == "getTimeseriesList":
            return httpx.Response(
                200,
                json=[
                    {
                        "station_no": "133074",
                        "station_name": "Daldowie",
                        "station_latitude": "55.82998694",
                        "station_longitude": "-4.121948322",
                        "stationparameter_name": "Level",
                        "stationparameter_no": "SG",
                        "ts_id": "52849010",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "columns": "Timestamp,Value,Quality Code",
                    "data": [
                        ["2026-08-03T00:00:00.000Z", 0.30, 0],
                        ["2026-08-03T01:00:00.000Z", 0.32, 0],
                    ],
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        summary = await SepaTimeSeriesClient(
            http_client
        ).recent_water_levels_near_location(
            latitude=55.8642,
            longitude=-4.2518,
            radius_km=30,
            period_days=1,
            limit=3,
        )

    assert summary.station_count == 1
    assert summary.stations[0].station.station_no == "133074"
    assert summary.stations[0].station.distance_km is not None
    assert summary.stations[0].latest_value_m == pytest.approx(0.32)
    assert summary.stations[0].level_state == "normal"
