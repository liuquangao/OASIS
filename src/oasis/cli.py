"""CLI used by people, Codex, tests, and reproducible evaluation scripts."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from oasis import __version__
from oasis.domain.areas import list_supported_areas, resolve_area
from oasis.integrations.sepa import SepaTimeSeriesClient
from oasis.runtime import run_agent
from oasis.settings import Settings


def _json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in value
        ]
    print(json.dumps(value, ensure_ascii=False, indent=2))


async def _sepa_client(settings: Settings) -> tuple[httpx.AsyncClient, SepaTimeSeriesClient]:
    client = httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=httpx.Timeout(settings.http_timeout_seconds),
    )
    return client, SepaTimeSeriesClient(client)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if args.command == "doctor":
        _json(
            {
                "ok": True,
                "version": __version__,
                "model": settings.model,
                "default_area": settings.default_area,
                "supported_areas": [area.id for area in list_supported_areas()],
                "note": (
                    "Live semantic model is configured."
                    if settings.semantic_model_configured
                    else "model=test is offline and does not provide real LLM reasoning"
                ),
            }
        )
        return 0

    if args.command == "agent":
        output = await run_agent(args.prompt, model=args.model, settings=settings)
        _json(output)
        return 0

    if args.tool_command == "area":
        area = resolve_area(args.place)
        _json(area)
        return 0
    if args.tool_command == "water-levels":
        area = resolve_area(args.place)
        client, sepa = await _sepa_client(settings)
        async with client:
            summary = await sepa.recent_water_levels_near_location(
                latitude=area.center_latitude,
                longitude=area.center_longitude,
                radius_km=args.radius_km,
                period_days=args.days,
                limit=args.limit,
            )
        _json(summary)
        return 0
    if args.tool_command == "rainfall":
        area = resolve_area(args.place)
        client, sepa = await _sepa_client(settings)
        async with client:
            summary = await sepa.recent_rainfall_near_location(
                latitude=area.center_latitude,
                longitude=area.center_longitude,
                radius_km=args.radius_km,
                period_hours=args.hours,
                limit=args.limit,
            )
        _json(summary)
        return 0
    raise RuntimeError("unhandled command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oasis")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local configuration and imports.")

    agent = sub.add_parser("agent", help="Run the PydanticAI agent loop.")
    agent.add_argument("prompt")
    agent.add_argument("--model", help="PydanticAI model identifier.")

    tool = sub.add_parser("tool", help="Run deterministic tools without an LLM.")
    tool_sub = tool.add_subparsers(dest="tool_command", required=True)
    area = tool_sub.add_parser("area", help="Resolve a named study area.")
    area.add_argument("--place", default="glasgow")

    levels = tool_sub.add_parser(
        "water-levels", help="Summarize recent levels at nearby SEPA stations."
    )
    levels.add_argument("--place", default="glasgow")
    levels.add_argument("--radius-km", type=float, default=30)
    levels.add_argument("--days", type=int, default=1)
    levels.add_argument("--limit", type=int, default=3)

    rainfall = tool_sub.add_parser(
        "rainfall", help="Summarize recent rainfall at nearby SEPA gauges."
    )
    rainfall.add_argument("--place", default="glasgow")
    rainfall.add_argument("--radius-km", type=float, default=20)
    rainfall.add_argument("--hours", type=int, default=24)
    rainfall.add_argument("--limit", type=int, default=3)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
