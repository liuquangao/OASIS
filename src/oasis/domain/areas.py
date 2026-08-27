"""Named study-area catalogue for the first deployment."""

from __future__ import annotations

from oasis.models.spatial import AreaOfInterest, BoundingBox


_AREAS = {
    "glasgow": AreaOfInterest(
        id="glasgow",
        name="Glasgow study area",
        country_code="GB-SCT",
        center_latitude=55.8642,
        center_longitude=-4.2518,
        bbox=BoundingBox(west=-4.40, south=55.75, east=-4.05, north=55.95),
        notes=[
            "Initial research bounding box; not an administrative boundary.",
            "Replace with an authoritative boundary before evaluation or deployment.",
        ],
    )
}


def list_supported_areas() -> list[AreaOfInterest]:
    return list(_AREAS.values())


def resolve_area(place: str) -> AreaOfInterest:
    key = place.strip().casefold()
    aliases = {
        "glasgow city": "glasgow",
        "greater glasgow": "glasgow",
        "格拉斯哥": "glasgow",
    }
    key = aliases.get(key, key)
    try:
        return _AREAS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_AREAS))
        raise ValueError(
            f"Unsupported named area {place!r}; supported areas: {supported}. "
            "Explicit coordinates can be used by lower-level tools."
        ) from exc

