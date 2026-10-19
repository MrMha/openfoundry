"""Authenticated validated blueprint catalog; no frontend-only template copies."""

from fastapi import APIRouter, Depends

from backend.app.api.auth import authorize_http
from backend.app.core.blueprints import Blueprint, load_blueprints

router = APIRouter(
    prefix="/api/blueprints", tags=["blueprints"], dependencies=[Depends(authorize_http)]
)


@router.get("", response_model=list[Blueprint])
def list_blueprints() -> list[Blueprint]:
    return load_blueprints()
