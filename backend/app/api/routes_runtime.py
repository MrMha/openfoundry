"""Authenticated, non-secret configuration for browser clients."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.app.api.auth import authorize_http
from backend.app.config import Provider, Settings

router = APIRouter(
    prefix="/api/runtime", tags=["configuration"], dependencies=[Depends(authorize_http)]
)


class ProviderView(BaseModel):
    provider: Provider
    model: str
    configured: bool


class RuntimeView(BaseModel):
    auth_required: bool
    max_mission_cost_usd: Decimal
    providers: list[ProviderView]
    demo_available: bool = True


@router.get("", response_model=RuntimeView)
async def runtime_configuration(request: Request) -> RuntimeView:
    settings = cast(Settings, request.app.state.settings)
    return RuntimeView(
        auth_required=settings.api_token is not None,
        max_mission_cost_usd=settings.max_mission_cost_usd,
        providers=[
            ProviderView(
                provider=provider,
                model=getattr(settings, f"{provider}_model"),
                configured=bool(
                    settings.ollama_base_url
                    if provider == "ollama"
                    else getattr(settings, f"{provider}_api_key")
                ),
            )
            for provider in settings.provider_order
        ],
    )
