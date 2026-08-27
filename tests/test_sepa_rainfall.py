from __future__ import annotations

import httpx
import pytest

from oasis.integrations.sepa import SepaTimeSeriesClient


@pytest.mark.asyncio
async def test_latest_rainfall_near_location_uses_public_station_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www2.sepa.org.uk/Rainfall/api/Stations"
        return httpx.Response(
            200,
            json=[
                {
                    "station_name": "Dalmarnock STW",
                    "station_latitude": "55.8374507",
                    "station_longitude": "-4.216965486",
                    "station_no": "327234",
                    "itemDate": "2026-08-27 21:00:00",
                    "itemValue": "0.2",
                    "accumRange": "1",
                },
                {
                    "station_name": "Far Away",
                    "station_latitude": "57.0",
                    "station_longitude": "-4.0",
                    "station_no": "far-away",
                    "itemDate": "2026-08-27 21:00:00",
                    "itemValue": "9.9",
                    "accumRange": "1",
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        summary = await SepaTimeSeriesClient(
            http_client
        ).latest_rainfall_near_location(
            latitude=55.857087,
            longitude=-4.261645,
            radius_km=20,
            limit=3,
        )

    assert summary.station_count == 1
    observation = summary.stations[0]
    assert observation.station.station_no == "327234"
    assert observation.accumulation_mm == pytest.approx(0.2)
    assert observation.accumulation_hours == pytest.approx(1)
    assert observation.rate_mm_per_hour == pytest.approx(0.2)
    assert observation.station.distance_km == pytest.approx(3.54, abs=0.01)
    assert summary.provenance.source_url == (
        "https://www2.sepa.org.uk/Rainfall/api/Stations"
    )


@pytest.mark.asyncio
async def test_recent_rainfall_near_location_summarizes_nearest_gauge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        if query["request"] == "getTimeseriesList":
            assert query["stationparameter_no"] == "RE"
            assert query["ts_shortname"] == "15m.Total"
            return httpx.Response(
                200,
                json=[
                    {
                        "station_no": "327234",
                        "station_name": "Dalmarnock STW",
                        "station_latitude": "55.8374507",
                        "station_longitude": "-4.216965486",
                        "stationparameter_name": "Rain",
                        "stationparameter_no": "RE",
                        "ts_name": "15minute.Total",
                        "ts_id": "58708010",
                        "ts_path": "1/327234/RE/15m.Total",
                    },
                    {
                        "station_no": "far-away",
                        "station_name": "Far Away",
                        "station_latitude": "57.0",
                        "station_longitude": "-4.0",
                        "stationparameter_name": "Rain",
                        "stationparameter_no": "RE",
                        "ts_name": "15minute.Total",
                        "ts_id": "2",
                        "ts_path": "1/far-away/RE/15m.Total",
                    },
                ],
            )
        assert query["request"] == "getTimeseriesValues"
        assert query["ts_id"] == "58708010"
        assert query["period"] == "P1D"
        return httpx.Response(
            200,
            json=[
                {
                    "ts_id": "58708010",
                    "rows": "5",
                    "columns": "Timestamp,Value,Quality Code",
                    "data": [
                        ["2026-08-03T00:00:00.000Z", 0.2, 0],
                        ["2026-08-03T00:15:00.000Z", 0.4, 0],
                        ["2026-08-03T00:30:00.000Z", 0.0, 0],
                        ["2026-08-03T00:45:00.000Z", 0.6, 254],
                        ["2026-08-03T01:00:00.000Z", 0.2, 254],
                    ],
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        summary = await SepaTimeSeriesClient(
            http_client
        ).recent_rainfall_near_location(
            latitude=55.8642,
            longitude=-4.2518,
            radius_km=20,
            period_hours=24,
            limit=3,
        )

    assert summary.station_count == 1
    station = summary.stations[0]
    assert station.station.station_no == "327234"
    assert station.latest_15min_mm == pytest.approx(0.2)
    assert station.total_mm == pytest.approx(1.4)
    assert station.last_1h_mm == pytest.approx(1.2)
    assert station.last_24h_mm == pytest.approx(1.4)
    assert station.maximum_15min_mm == pytest.approx(0.6)
    assert station.maximum_1h_mm == pytest.approx(1.2)
    assert station.quality_codes == [0, 254]
    assert summary.provenance.integration == (
        "oasis.integrations.sepa.SepaTimeSeriesClient"
    )
    assert any("quality codes" in warning for warning in summary.warnings)
