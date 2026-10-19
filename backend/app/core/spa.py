"""Production SPA assets with an HTML-only fallback and reserved backend namespaces."""

from __future__ import annotations

from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send


class WarRoomStaticFiles(StaticFiles):
    """Mount last: API/WebSocket misses must remain backend misses, never index.html."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = PurePosixPath(path).parts
        if any(part.startswith(".") for part in parts) or (
            parts and parts[0] in {"api", "ws", "health", "docs", "redoc", "openapi.json"}
        ):
            raise HTTPException(404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            accepts_html = any(
                name.lower() == b"accept" and b"text/html" in value.lower()
                for name, value in scope["headers"]
            )
            if (
                error.status_code != 404
                or scope["method"] not in {"GET", "HEAD"}
                or PurePosixPath(path).suffix
                or parts
                and parts[0] == "assets"
                or not accepts_html
            ):
                raise
            response = await super().get_response("index.html", scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response
