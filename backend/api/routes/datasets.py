from fastapi import APIRouter

from metadata.repository import load_datasets

router = APIRouter(tags=["datasets"])


@router.get("/datasets")
def list_datasets() -> list[dict]:
    return load_datasets()
