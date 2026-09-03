import httpx

from hydromind.integrations.openstreetmap import OpenStreetMapClient


async def test_geocode_returns_typed_place() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "osm_type": "node",
                    "osm_id": 7,
                    "display_name": "Glasgow Central Station, Glasgow",
                    "lat": "55.8597",
                    "lon": "-4.2580",
                    "type": "station",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        place = await OpenStreetMapClient(
            client,
            nominatim_url="https://nominatim.test/search",
        ).geocode("Glasgow Central Station")

    assert place is not None
    assert place.place_type == "station"
    assert place.provider == "OpenStreetMap Nominatim"


async def test_nearby_search_uses_generic_osm_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "station"
        assert request.url.params["bounded"] == "1"
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "place_id": 9,
                    "osm_type": "node",
                    "osm_id": 10,
                    "display_name": "Example Station, Glasgow",
                    "name": "Example Station",
                    "lat": "55.86",
                    "lon": "-4.25",
                    "category": "railway",
                    "type": "station",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        places = await OpenStreetMapClient(
            client,
            nominatim_url="https://nominatim.test/search",
        ).search_nearby(
            latitude=55.857,
            longitude=-4.261,
            radius_km=2,
            tag_key="railway",
            tag_value="station",
            limit=5,
        )

    assert places[0].label == "Example Station"
    assert places[0].place_type == "railway:station"
