"""Small HTTP boundary for the browser-based map Agent."""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from oasis.runtime import run_spatial_agent
from oasis.settings import Settings
from oasis.models.map_conversation import MapAgentResponse, MapSessionState


class MapTurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    state: MapSessionState = Field(default_factory=MapSessionState)


app = FastAPI(title="OASIS Map Agent API", version="0.1.0")
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
    }


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
