from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.routes import analysis, datasets, features, layers

app = FastAPI(
    title="Glasgow Flood Risk WebGIS API",
    version="0.2.0",
    description="Agent-ready API layer for WebGIS metadata, spatial queries, and flood exposure analysis.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasets.router)
app.include_router(layers.router)
app.include_router(features.router)
app.include_router(analysis.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
