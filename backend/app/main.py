"""Mission API lifecycle, local/operator authentication, and CLI entry point."""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.api.routes_blueprints import router as blueprint_routes
from backend.app.api.routes_missions import router as mission_routes
from backend.app.api.routes_plugins import SandboxInspector
from backend.app.api.routes_plugins import router as plugin_routes
from backend.app.api.routes_runtime import router as runtime_routes
from backend.app.api.ws_war_room import ConnectionManager
from backend.app.api.ws_war_room import router as websocket_routes
from backend.app.config import Settings
from backend.app.core.mcp_bridge import MCPBridge, create_mcp_server
from backend.app.core.orchestrator import MissionOrchestrator
from backend.app.core.security_logging import install_log_redaction, redact_secrets
from backend.app.core.spa import WarRoomStaticFiles
from backend.app.database import Database
from backend.app.departments.base_department import BaseDepartment

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    settings: Settings | None = None, *, departments: Mapping[str, BaseDepartment] | None = None
) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()
    install_log_redaction(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config)
        manager = ConnectionManager(database)
        bridge = MCPBridge(config, database)
        orchestrator = MissionOrchestrator(
            config, database, manager, departments=departments, mcp_bridge=bridge
        )
        try:
            await database.initialize()
            app.state.settings = config
            app.state.database = database
            app.state.connection_manager = manager
            app.state.orchestrator = orchestrator
            app.state.sandbox_inspector = SandboxInspector(config)
            app.state.mcp_bridge = bridge
            await orchestrator.start()
            yield
        finally:
            await orchestrator.close()
            await bridge.close()
            await manager.close()
            await database.close()

    app = FastAPI(title="OpenFoundry", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic's default input/context echo may contain accidentally submitted keys.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "loc": [
                            redact_secrets(part) if isinstance(part, str) else part
                            for part in item["loc"]
                        ],
                        "msg": redact_secrets(item["msg"]),
                        "type": item["type"],
                    }
                    for item in error.errors()
                ]
            },
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    if config.api_token is None:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
        )
    app.include_router(mission_routes)
    app.include_router(plugin_routes)
    app.include_router(runtime_routes)
    app.include_router(blueprint_routes)
    app.include_router(websocket_routes)

    @app.get("/health", tags=["operations"])
    async def health(request: Request) -> dict[str, str]:
        database = cast(Database, request.app.state.database)
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
        app.mount("/", WarRoomStaticFiles(directory=FRONTEND_DIST, html=True), name="war-room")

    return app


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Run the OpenFoundry backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("command", nargs="?", choices=["serve", "mcp"], default="serve")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    settings = Settings.from_env()
    install_log_redaction(settings)
    if args.command == "mcp":
        create_mcp_server(
            args.api_url, settings.api_token.get_secret_value() if settings.api_token else None
        ).run(transport="stdio")
        return
    if args.host not in {"127.0.0.1", "::1", "localhost"} and settings.api_token is None:
        parser.error("Set OPENFOUNDRY_API_TOKEN before binding a non-loopback interface")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1, ws_max_size=4096)


if __name__ == "__main__":
    run_cli()
