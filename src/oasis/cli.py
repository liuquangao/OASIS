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
from oasis.runtime import build_analysis_service, run_agent
from oasis.reproducible_data import (
    preflight_exact_data,
    prepare_risk_inputs,
    rebuild_glasgow_5m,
    result_dict as exact_result_dict,
    verify_glasgow_5m,
)
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

    if args.command == "all-hazards":
        result = await build_analysis_service(settings, publish=not args.no_publish).run_all_hazards(
            use_live_data=not args.static,
            forecast_horizon=args.forecast_horizon_hours,
        )
        _json(result)
        return 0

    if args.command == "priority-assessment":
        result = await build_analysis_service(settings, publish=not args.no_publish).run_flood_priority_assessment(
            scenario=args.scenario,
            use_live_data=not args.static,
            forecast_horizon=args.forecast_horizon_hours,
            hazard_threshold=args.hazard_threshold,
            priority_scenario=args.priority_scenario,
            all_hazards_run_id=args.all_hazards_run_id,
        )
        _json(result)
        return 0

    if args.command == "nrfa":
        service = build_analysis_service(settings, publish=False)
        if args.station_id:
            result = await service.nrfa_history(
                dataset=args.dataset,
                station_id=args.station_id,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        else:
            result = await service.nrfa_stations(args.dataset)
        _json(result)
        return 0

    if args.command == "data" and args.data_command == "preflight":
        _json(preflight_exact_data(args.lcm2019, accept_licences=args.accept_licences))
        return 0

    if args.command == "data" and args.data_command == "rebuild":
        result = await asyncio.to_thread(
            rebuild_glasgow_5m,
            args.input_dir or settings.core_analyst_input_dir,
            lcm2019=args.lcm2019,
            cache_dir=args.cache_dir,
            accept_licences=args.accept_licences,
            force=args.force,
            user_agent=settings.user_agent,
        )
        _json(exact_result_dict(result))
        return 0

    if args.command == "data" and args.data_command == "verify":
        _json(verify_glasgow_5m(args.input_dir or settings.core_analyst_input_dir))
        return 0


    if args.command == "data" and args.data_command == "prepare-risk":
        result = await asyncio.to_thread(
            prepare_risk_inputs,
            args.input_dir or settings.core_analyst_input_dir,
            cache_dir=args.cache_dir,
            force=args.force,
            user_agent=settings.user_agent,
        )
        _json(result)
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

    all_hazards = sub.add_parser("all-hazards", help="Run and publish all current/future hazard outputs.")
    all_hazards.add_argument("--static", action="store_true", help="Use static/demo forcing instead of live providers.")
    all_hazards.add_argument("--no-publish", action="store_true", help="Do not publish generated rasters to GeoServer.")
    all_hazards.add_argument("--forecast-horizon-hours", type=int, default=6)

    priority = sub.add_parser(
        "priority-assessment",
        help="Run the end-to-end Data Zone hazard, exposure, vulnerability, and priority workflow.",
    )
    priority.add_argument("--scenario", choices=["current", "future"], default="future")
    priority.add_argument("--static", action="store_true")
    priority.add_argument("--no-publish", action="store_true")
    priority.add_argument("--forecast-horizon-hours", type=int, default=24)
    priority.add_argument("--hazard-threshold", type=int, choices=[1, 2, 3], default=2)
    priority.add_argument(
        "--priority-scenario",
        choices=["life_safety", "social_equity", "economic_protection"],
        default="social_equity",
    )
    priority.add_argument("--all-hazards-run-id")

    nrfa = sub.add_parser("nrfa", help="List or query local NRFA historical series.")
    nrfa.add_argument("--dataset", choices=["nrfa_historical_river_flow", "nrfa_historical_rainfall"], required=True)
    nrfa.add_argument("--station-id")
    nrfa.add_argument("--start-date")
    nrfa.add_argument("--end-date")

    data = sub.add_parser("data", help="Acquire and prepare reproducible analysis inputs.")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    preflight = data_sub.add_parser(
        "preflight",
        help="Check licences and the manually obtained UKCEH file before large downloads.",
    )
    preflight.add_argument("--lcm2019", type=str)
    preflight.add_argument("--accept-licences", action="store_true")

    rebuild = data_sub.add_parser(
        "rebuild",
        help="Rebuild the source-faithful Glasgow 5 m analysis inputs.",
    )
    rebuild.add_argument("--lcm2019", required=True, type=str)
    rebuild.add_argument("--input-dir", type=str)
    rebuild.add_argument("--cache-dir", type=str)
    rebuild.add_argument("--accept-licences", action="store_true")
    rebuild.add_argument("--force", action="store_true")

    verify = data_sub.add_parser("verify", help="Verify the exact Glasgow 5 m input contract.")
    verify.add_argument("--input-dir", type=str)

    prepare_risk = data_sub.add_parser(
        "prepare-risk",
        help="Download locked official facilities and prepare Data Zone social-risk inputs.",
    )
    prepare_risk.add_argument("--input-dir", type=str)
    prepare_risk.add_argument("--cache-dir", type=str)
    prepare_risk.add_argument("--force", action="store_true")

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
