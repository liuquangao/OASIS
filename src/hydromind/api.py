"""Small HTTP boundary for the browser-based map Agent."""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import json
from pathlib import Path
import re
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ModelAPIError

from hydromind.runtime import run_spatial_agent
from hydromind.settings import Settings
from hydromind.assessment_jobs import assessment_coordinator
from hydromind.models.analysis import PriorityWeights
from hydromind.models.assessment import AssessmentJob, AssessmentPreferences
from hydromind.models.map_conversation import MapAgentResponse, MapSessionState
from hydromind.setup import setup_coordinator


class MapTurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    state: MapSessionState = Field(default_factory=MapSessionState)


class AssessmentExecuteRequest(BaseModel):
    preferences: AssessmentPreferences
    state: MapSessionState = Field(default_factory=MapSessionState)


class RerankRequest(BaseModel):
    weights: PriorityWeights
    include_simd: bool = True
    scenario_name: str = Field(default="custom", min_length=1, max_length=40)


_RUN_ID = re.compile(r"^[a-f0-9]{12}$")


app = FastAPI(title="HydroMind Agent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000", "null"],
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = Settings.from_env()
    return {
        "status": "ok",
        "semantic_model": (
            "configured" if settings.semantic_model_configured else "not_configured"
        ),
        "core_analysis": "enabled",
        "all_hazards": "enabled",
    }


@app.get("/setup/status")
async def setup_status() -> dict:
    return setup_coordinator.status(Settings.from_env())


@app.post("/setup/initialize")
async def setup_initialize() -> dict:
    return setup_coordinator.initialize(Settings.from_env())


@app.post("/agent/turn", response_model=MapAgentResponse)
async def run_map_turn(request: MapTurnRequest) -> MapAgentResponse:
    settings = Settings.from_env()
    if not settings.semantic_model_configured:
        raise HTTPException(status_code=503, detail="The semantic Agent is not configured.")
    try:
        return await run_spatial_agent(
            request.prompt,
            request.state,
            settings=settings,
        )
    except httpx.HTTPError as exc:
        failed_url = str(exc.request.url) if exc.request is not None else ""
        if failed_url.startswith(settings.geoserver_wms_url):
            detail = "The hazard map service is unavailable."
        elif failed_url.startswith(settings.nominatim_url):
            detail = "The location search service is unavailable."
        elif failed_url.startswith(settings.osrm_url):
            detail = "The route planning service is unavailable."
        else:
            detail = "An external service required by the Agent is unavailable."
        raise HTTPException(status_code=502, detail=detail) from exc
    except ModelAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The language model at {settings.openai_base_url or 'the configured endpoint'} is unavailable: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/analysis/runs/{run_id}")
async def analysis_run(run_id: str) -> dict:
    settings = Settings.from_env()
    path = _result_path(settings, run_id)
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/assessments/{plan_id}/execute", response_model=AssessmentJob)
async def execute_assessment(
    plan_id: str,
    request: AssessmentExecuteRequest,
) -> AssessmentJob:
    """Queue only a plan that the user has explicitly confirmed."""

    settings = Settings.from_env()
    try:
        return assessment_coordinator.start(
            plan_id=plan_id,
            preferences=request.preferences,
            state=request.state,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/assessment-jobs/{job_id}", response_model=AssessmentJob)
async def assessment_job(job_id: str) -> AssessmentJob:
    try:
        return assessment_coordinator.get(job_id, Settings.from_env())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/analysis/runs/{run_id}/rerank")
async def rerank_analysis(run_id: str, request: RerankRequest) -> dict:
    """Re-rank saved components without weather calls or hazard recomputation."""

    settings = Settings.from_env()
    from hydromind.runtime import build_analysis_service

    service = build_analysis_service(settings)
    try:
        result = await service.rerank_flood_priority(
            source_run_id=run_id,
            weights=request.weights,
            include_simd=request.include_simd,
            scenario_name=request.scenario_name,
        )
        quality = await service.validate_flood_priority_run(
            result.run_id,
            expected_data_zone_count=1071,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"run": result.model_dump(mode="json"), "quality": quality}


@app.get("/analysis/runs/{run_id}/artifacts/{key}")
async def analysis_artifact(run_id: str, key: str) -> FileResponse:
    settings = Settings.from_env()
    result = json.loads(_result_path(settings, run_id).read_text(encoding="utf-8"))
    artifact = Path(result["outputs"][key]).resolve()
    output_root = settings.core_analyst_analysis_output_dir.resolve()
    if output_root not in artifact.parents:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(artifact)


def _result_path(settings: Settings, run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    path = settings.core_analyst_analysis_output_dir / "runs" / run_id / "result.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return path
