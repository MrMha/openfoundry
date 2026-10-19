"""Typed, discoverable department contract. Plugins run as trusted backend code."""

from __future__ import annotations

import importlib
import inspect
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, ClassVar, TypeVar

if TYPE_CHECKING:
    from backend.app.core.mcp_bridge import MCPBridge

from pydantic import JsonValue

from backend.app.core.llm_router import LLMRouter
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.models.events import WarRoomEvent
from backend.app.models.mission import TaskNode

EventEmitter = Callable[[WarRoomEvent], Awaitable[None]]
DepartmentOutput = dict[str, JsonValue]


class DepartmentExecutionError(RuntimeError):
    """A failed task with concrete diagnostic artifacts safe for scheduler persistence."""

    def __init__(self, message: str, *, output: DepartmentOutput) -> None:
        super().__init__(message)
        self.output = output


class BaseDepartment(ABC):
    """Implementations must support concurrent calls and use the supplied mission resources.

    Constructors accept the application's credential-isolated router. Returned dictionaries
    must be JSON values; ``summary`` and ``artifacts`` are understood by the scheduler.
    Only thoughts, browser frames, and code diffs may be emitted by plugins. Authoritative
    task/mission transitions and approval events belong to the orchestrator.
    """

    name: ClassVar[str]
    role_description: ClassVar[str]
    capabilities: ClassVar[tuple[str, ...]] = ()
    plugin_version: ClassVar[str | None] = None
    mcp: MCPBridge | None = None

    def __init__(self, router: LLMRouter) -> None:
        self.router = router

    @abstractmethod
    async def execute_task(
        self,
        task: TaskNode,
        sandbox: WorkspaceSandbox,
        memory: MemoryVault,
        emit_event: EventEmitter,
    ) -> DepartmentOutput:
        """Execute one detached TaskNode and return its JSON-serializable result."""


Plugin = TypeVar("Plugin", bound=BaseDepartment)
_registry: dict[str, type[BaseDepartment]] = {}
_registry_lock = threading.RLock()


def register_department(plugin: type[Plugin]) -> type[Plugin]:
    if not issubclass(plugin, BaseDepartment) or inspect.isabstract(plugin):
        raise TypeError("Departments must be concrete BaseDepartment subclasses")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", getattr(plugin, "name", "")):
        raise ValueError("Department names must be lowercase identifiers of at most 64 characters")
    if not getattr(plugin, "role_description", "").strip():
        raise ValueError("Departments must describe their role")
    if (
        not isinstance(plugin.capabilities, tuple)
        or len(plugin.capabilities) > 32
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value)
            for value in plugin.capabilities
        )
        or len(set(plugin.capabilities)) != len(plugin.capabilities)
    ):
        raise ValueError("Plugin capabilities must be unique lowercase identifiers")
    if plugin.plugin_version is not None and (
        not isinstance(plugin.plugin_version, str)
        or not plugin.plugin_version.strip()
        or len(plugin.plugin_version) > 64
    ):
        raise ValueError("Plugin version must be nonempty and at most 64 characters")
    if not inspect.iscoroutinefunction(plugin.execute_task):
        raise TypeError("Department execute_task must be async")
    with _registry_lock:
        previous = _registry.get(plugin.name)
        if previous is not None and previous is not plugin:
            raise ValueError(f"Department already registered: {plugin.name}")
        _registry[plugin.name] = plugin
    return plugin


def registered_departments() -> Mapping[str, type[BaseDepartment]]:
    with _registry_lock:
        return dict(_registry)


def load_department_plugins(modules: Sequence[str] = ()) -> None:
    """Load trusted installed entry points and explicitly configured Python modules."""
    importlib.import_module("backend.app.departments.chief_of_staff")
    importlib.import_module("backend.app.departments.builtin")
    importlib.import_module("backend.app.departments.researcher")
    importlib.import_module("backend.app.departments.engineer")
    importlib.import_module("backend.app.departments.qa_self_healer")
    importlib.import_module("backend.app.departments.growth_career")
    for module in modules:
        importlib.import_module(module)
    for entry in entry_points(group="openfoundry.departments"):
        plugin = entry.load()
        if not isinstance(plugin, type) or not issubclass(plugin, BaseDepartment):
            raise TypeError(f"Invalid department entry point: {entry.name}")
        register_department(plugin)
