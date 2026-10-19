"""Authenticated plugin discovery and cached, measured sandbox capability status."""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from tempfile import TemporaryDirectory
from typing import Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict

from backend.app.api.auth import authorize_http
from backend.app.config import Settings
from backend.app.core.mcp_bridge import MCPBridge, MCPStatus
from backend.app.core.orchestrator import MissionOrchestrator
from backend.app.core.workspace_sandbox import SandboxError, SandboxMode, WorkspaceSandbox
from backend.app.departments.base_department import BaseDepartment, registered_departments

router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(authorize_http)])
_LEGACY = {
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa_self_healer",
    "growth": "growth_career",
}


class PluginMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    role_description: str
    module: str
    class_name: str
    version: str | None
    capabilities: tuple[str, ...]
    builtin: bool
    enabled: bool
    legacy_replacement: str | None


class SandboxStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    configured_mode: SandboxMode
    execution_mode: Literal["isolated", "guarded", "unavailable"]
    isolation_available: bool | None
    capability_probe_performed: bool
    bwrap_installed: bool
    reason: str | None
    max_command_seconds: int = 60
    guarded_execution_is_os_isolation: Literal[False] = False
    platform: str
    checked_at: AwareDatetime


class PluginCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugins: list[PluginMetadata]
    sandbox: SandboxStatus
    mcp: MCPStatus


def plugin_metadata(active: Mapping[str, BaseDepartment]) -> list[PluginMetadata]:
    registry = dict(registered_departments())
    registry.update({name: type(plugin) for name, plugin in active.items()})
    try:
        package_version: str | None = version("openfoundry")
    except PackageNotFoundError:
        package_version = None
    result: list[PluginMetadata] = []
    for name, plugin in sorted(registry.items()):
        builtin = plugin.__module__.startswith("backend.app.departments.")
        result.append(
            PluginMetadata(
                name=name,
                role_description=plugin.role_description,
                module=plugin.__module__,
                class_name=plugin.__qualname__,
                version=plugin.plugin_version or (package_version if builtin else None),
                capabilities=plugin.capabilities,
                builtin=builtin,
                enabled=name in active,
                legacy_replacement=_LEGACY.get(name) if builtin else None,
            )
        )
    return result


class SandboxInspector:
    """One probe per application every 30 seconds; never touch mission workspaces.

    A temporary workspace runs only the existing harmless isolation probe. Concurrent
    callers share the result. Errors report unavailable rather than claiming isolation.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._status: SandboxStatus | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def inspect(self) -> SandboxStatus:
        async with self._lock:
            if self._status is not None and time.monotonic() < self._expires:
                return self._status
            checked = datetime.now(UTC)
            installed = (
                shutil.which("bwrap", path=WorkspaceSandbox._environment()["PATH"]) is not None
            )
            mode = self.settings.sandbox_mode
            available: bool | None = None
            performed = False
            effective: Literal["isolated", "guarded", "unavailable"] = "guarded"
            reason: str | None = (
                "Guarded host execution is explicitly configured" if mode == "guarded" else None
            )
            if mode != "guarded":
                try:
                    # Database startup already owns/creates data_dir. TemporaryDirectory
                    # deletes this probe's Git metadata and files after descriptors close.
                    with TemporaryDirectory(
                        prefix="sandbox-probe-", dir=self.settings.data_dir
                    ) as root:
                        async with WorkspaceSandbox(
                            uuid4(), root=root, sandbox_mode=mode
                        ) as sandbox:
                            performed = True
                            available = await sandbox._probe_isolation()
                            reason = sandbox._isolation_reason
                    effective = (
                        "isolated"
                        if available
                        else "unavailable"
                        if mode == "strict"
                        else "guarded"
                    )
                except (OSError, SandboxError) as error:
                    effective = "unavailable"
                    reason = f"Sandbox capability probe could not complete ({type(error).__name__})"
            self._status = SandboxStatus(
                configured_mode=mode,
                execution_mode=effective,
                isolation_available=available,
                capability_probe_performed=performed,
                bwrap_installed=installed,
                reason=reason,
                platform=sys.platform,
                checked_at=checked,
            )
            self._expires = time.monotonic() + 30
            return self._status


@router.get("", response_model=PluginCatalog)
async def list_plugins(request: Request) -> PluginCatalog:
    coordinator = cast(MissionOrchestrator, request.app.state.orchestrator)
    inspector = cast(SandboxInspector, request.app.state.sandbox_inspector)
    return PluginCatalog(
        plugins=plugin_metadata(coordinator.departments),
        sandbox=await inspector.inspect(),
        mcp=await cast(MCPBridge, request.app.state.mcp_bridge).inspect(),
    )
