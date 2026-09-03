import pytest

from hydromind.agent import flood_agent
from hydromind.runtime import run_agent


def test_agent_is_defined_without_binding_a_provider() -> None:
    assert flood_agent is not None


def test_agent_composes_the_expected_function_toolsets() -> None:
    registered_tools = {
        name
        for toolset in flood_agent.toolsets
        for name in getattr(toolset, "tools", {})
    }
    assert registered_tools == {
        "resolve_area",
        "get_recent_water_levels_near_location",
        "get_recent_rainfall_near_location",
    }


@pytest.mark.asyncio
async def test_offline_agent_loop_smoke_test() -> None:
    output = await run_agent("Check the agent loop.", model="test")
    assert "Offline PydanticAI smoke test" in output.answer
    assert output.requires_human_review is True
