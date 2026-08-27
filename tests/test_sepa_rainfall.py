from __future__ import annotations

import httpx
import pytest

from oasis.integrations.sepa import SepaTimeSeriesClient


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
