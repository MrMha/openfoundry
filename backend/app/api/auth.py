"""Single-operator authentication for REST and browser-compatible WebSocket handshakes."""

from __future__ import annotations

import secrets
from typing import cast

from fastapi import HTTPException, Request, WebSocket, WebSocketException
from starlette.requests import HTTPConnection

from backend.app.config import Settings


def _authorized(connection: HTTPConnection, *, websocket: bool = False) -> bool:
    settings = cast(Settings, connection.app.state.settings)
    origin = connection.headers.get("origin")
    if origin:
        own_origin = (
            str(connection.base_url)
            .rstrip("/")
            .replace("ws://", "http://")
            .replace("wss://", "https://")
        )
        if origin.rstrip("/") not in {*settings.cors_origins, own_origin}:
            return False
    if settings.api_token is None:
        return connection.client is not None and connection.client.host in {
            "127.0.0.1",
            "::1",
            "testclient",
        }
    authorization = connection.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    candidate = value if scheme.casefold() == "bearer" else ""
    if websocket and not candidate:
        for protocol in connection.headers.get("sec-websocket-protocol", "").split(","):
            if protocol.strip().startswith("bearer."):
                candidate = protocol.strip()[7:]
                break
    return secrets.compare_digest(
        candidate.encode(), settings.api_token.get_secret_value().encode()
    )


async def authorize_http(request: Request) -> None:
    if not _authorized(request):
        raise HTTPException(
            status_code=401,
            detail="A valid API token and allowed origin are required",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def authorize_websocket(websocket: WebSocket) -> None:
    if not _authorized(websocket, websocket=True):
        raise WebSocketException(code=1008, reason="Unauthorized WebSocket connection")
