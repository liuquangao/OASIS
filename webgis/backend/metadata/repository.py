import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api.config import settings


@lru_cache
def _metadata() -> dict[str, Any]:
    path = settings.metadata_path
    if not path.exists():
        fallback = Path(__file__).resolve().parents[2] / "data" / "metadata" / "datasets.json"
        path = fallback if fallback.exists() else path
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_datasets() -> list[dict[str, Any]]:
    return _metadata().get("datasets", [])


def load_layers() -> list[dict[str, Any]]:
    layers = []
    for dataset in load_datasets():
        layer = dataset.get("layer")
        if not layer or not layer.get("enabled", True):
            continue
        layers.append(
            {
                "name": dataset["name"],
                "display_name": dataset["display_name"],
                "category": dataset["category"],
                "type": dataset["type"],
                "service": layer["service"],
                "geoserver_name": layer["geoserver_name"],
                "color": layer.get("color", "#1677b8"),
                "description": dataset.get("description", ""),
            }
        )
    return layers


def get_dataset(layer_name: str) -> dict[str, Any]:
    for dataset in load_datasets():
        if dataset["name"] == layer_name or dataset.get("display_name") == layer_name:
            return dataset
    raise HTTPException(status_code=404, detail=f"Unknown dataset: {layer_name}")
