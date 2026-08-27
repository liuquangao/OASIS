from fastapi import APIRouter

from metadata.repository import load_layers

router = APIRouter(tags=["layers"])


@router.get("/layers")
def list_layers() -> list[dict]:
    return load_layers()
