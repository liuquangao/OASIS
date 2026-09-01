"""Website startup checks and reproducible background data preparation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from typing import Any

from oasis.reproducible_data import (
    load_source_lock,
    prepare_risk_inputs,
    rebuild_glasgow_5m,
    verify_glasgow_5m,
)
from oasis.settings import Settings


RISK_REPAIRABLE_PATHS = (
    "DataZoneBoundaries2011",
    "processed/data_zone",
    "processed/facilities",
)


class SetupCoordinator:
    """Run at most one data preparation job in the API process."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._last_verification: dict[str, Any] | None = None
        self._job: dict[str, Any] = {
            "state": "idle",
            "action": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }

    def status(self, settings: Settings) -> dict[str, Any]:
        if self._job["state"] == "running" and self._last_verification is not None:
            verification = self._last_verification
        else:
            verification = verify_glasgow_5m(settings.core_analyst_input_dir)
            self._last_verification = verification
        action = self._automatic_action(settings, verification)
        configuration = configuration_status(settings)
        model_ready = next(item for item in configuration if item["id"] == "agent_model")["configured"]

        if self._job["state"] == "running":
            overall = "initializing"
        elif verification["ok"] and model_ready:
            overall = "ready"
        else:
            overall = "needs_attention"

        return {
            "status": overall,
            "can_use_agent": bool(verification["ok"] and model_ready and self._job["state"] != "running"),
            "data": {
                "status": "complete" if verification["ok"] else "incomplete",
                "profile": verification["profile"],
                "input_dir": str(settings.core_analyst_input_dir.resolve()),
                "checked_files": len(verification["checked"]),
                "errors": verification["errors"],
                "automatic_action": action,
                "estimated_full_download_bytes": _full_download_bytes(),
            },
            "configuration": configuration,
            "job": dict(self._job),
        }

    def initialize(self, settings: Settings) -> dict[str, Any]:
        status = self.status(settings)
        if not settings.auto_prepare_data or status["data"]["status"] == "complete":
            return status
        if self._task is not None and not self._task.done():
            return status

        action = status["data"]["automatic_action"]
        if action not in {"prepare_risk", "rebuild_all"}:
            return status

        self._job = {
            "state": "running",
            "action": action,
            "started_at": _now(),
            "finished_at": None,
            "error": None,
        }
        self._task = asyncio.create_task(self._run(action, settings))
        return self.status(settings)

    async def _run(self, action: str, settings: Settings) -> None:
        try:
            if action == "prepare_risk":
                await asyncio.to_thread(
                    prepare_risk_inputs,
                    settings.core_analyst_input_dir,
                    user_agent=settings.user_agent,
                )
            else:
                await asyncio.to_thread(
                    rebuild_glasgow_5m,
                    settings.core_analyst_input_dir,
                    lcm2019=settings.lcm2019_path,
                    accept_licences=settings.accept_data_licences,
                    user_agent=settings.user_agent,
                )
            verification = verify_glasgow_5m(settings.core_analyst_input_dir)
            self._last_verification = verification
            if not verification["ok"]:
                raise ValueError("Data preparation finished but verification failed: " + "; ".join(verification["errors"]))
        except Exception as exc:
            self._job["state"] = "failed"
            self._job["error"] = str(exc)
        else:
            self._job["state"] = "complete"
        finally:
            self._job["finished_at"] = _now()

    @staticmethod
    def _automatic_action(settings: Settings, verification: dict[str, Any]) -> str:
        if verification["ok"]:
            return "none"
        normalised_errors = [error.replace("\\", "/") for error in verification["errors"]]
        if normalised_errors and all(
            any(marker in error for marker in RISK_REPAIRABLE_PATHS)
            for error in normalised_errors
        ):
            return "prepare_risk"
        if (
            settings.lcm2019_path is not None
            and settings.lcm2019_path.is_file()
            and settings.accept_data_licences
        ):
            return "rebuild_all"
        return "configure_lcm"


def configuration_status(settings: Settings) -> list[dict[str, Any]]:
    model_vars, model_message = _model_configuration(settings)
    return [
        {
            "id": "agent_model",
            "label": "Language model",
            "importance": "required",
            "configured": settings.semantic_model_configured,
            "environment_variables": model_vars,
            "message": model_message,
        },
        {
            "id": "metoffice_forecast",
            "label": "Met Office 24-hour rainfall forecast",
            "importance": "recommended",
            "configured": bool(os.getenv("METOFFICE_SITE_API_KEY", "").strip()),
            "environment_variables": ["METOFFICE_SITE_API_KEY"],
            "message": "Required for live future-pluvial analysis; current and static analyses remain available without it.",
        },
        {
            "id": "admiralty_tides",
            "label": "ADMIRALTY tidal prediction",
            "importance": "optional",
            "configured": bool(os.getenv("ADMIRALTY_API_KEY", "").strip()),
            "environment_variables": ["ADMIRALTY_API_KEY"],
            "message": "Optional for live coastal tidal prediction.",
        },
        {
            "id": "carto_basemap",
            "label": "CARTO basemap",
            "importance": "optional",
            "configured": None,
            "environment_variables": ["webgis/frontend/config.local.js"],
            "message": "Checked in the browser because this key is stored in frontend configuration.",
        },
    ]


def _model_configuration(settings: Settings) -> tuple[list[str], str]:
    if settings.model == "test":
        return ["OASIS_MODEL"], (
            "Choose either a hosted model API or a local vLLM deployment; test mode cannot run the website Agent."
        )
    if settings.model_provider == "vllm":
        return ["OASIS_MODEL", "OASIS_MODEL_PROVIDER", "OPENAI_BASE_URL", "OPENAI_API_KEY"], (
            "Local vLLM needs its model name, OpenAI-compatible base URL, and a non-empty placeholder API key."
        )
    if settings.model_provider == "mimo":
        return ["OASIS_MODEL", "OASIS_MODEL_PROVIDER", "MIMO_API_KEY"], "MiMo requires MIMO_API_KEY."
    return ["OASIS_MODEL", "OPENAI_API_KEY"], "Hosted OpenAI mode requires a live model identifier and OPENAI_API_KEY."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _full_download_bytes() -> int:
    terrain = load_source_lock()["sources"]["terrain"]
    return sum(
        item[0]
        for group in ("tiles", "supplemental_tiles")
        for item in terrain[group].values()
    )


setup_coordinator = SetupCoordinator()
