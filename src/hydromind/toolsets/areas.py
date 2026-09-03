"""Study-area tools."""

from pydantic_ai import FunctionToolset

from hydromind.deps import Deps
from hydromind.domain.areas import resolve_area as resolve_named_area
from hydromind.models.spatial import AreaOfInterest


area_tools = FunctionToolset[Deps](
    instructions="Resolve named places before requesting place-specific evidence."
)


@area_tools.tool_plain
def resolve_area(place: str) -> AreaOfInterest:
    """Resolve a supported named study area into a typed WGS84 area.

    Args:
        place: Place name supplied by the user, for example Glasgow.
    """

    return resolve_named_area(place)
