"""Deterministic department fixtures; no external LLM calls or host test execution."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from backend.app.core.llm_router import LLMRouter
from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.base_department import BaseDepartment, DepartmentOutput, EventEmitter
from backend.app.models.events import AgentThoughtEvent
from backend.app.models.mission import TaskNode


def node(
    key: str,
    dependencies: list[str] | None = None,
    *,
    approval: bool = False,
    department: str = "engineering",
) -> dict[str, Any]:
    return {
        "key": key,
        "department": department,
        "title": key,
        "description": f"Deliver and verify {key}",
        "dependencies": dependencies or [],
        "requires_approval": approval,
    }


def plan(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {
        "specification": "# SPEC\nDeliver the requested mission with verifiable artifacts.",
        "tasks": list(nodes) or [node("implementation")],
    }


@dataclass
class ExecutionTrace:
    active: int = 0
    peak: int = 0
    timeline: list[tuple[str, str]] = field(default_factory=list)
    barrier: asyncio.Event | None = None
    fail: set[str] = field(default_factory=set)
    block: bool = False


class Planner(BaseDepartment):
    name = "chief_of_staff"
    role_description = "Return a deterministic test DAG"

    def __init__(self, router: LLMRouter, output: dict[str, Any]) -> None:
        super().__init__(router)
        self.output = output
        self.calls = 0

    async def execute_task(
        self,
        task: TaskNode,
        sandbox: WorkspaceSandbox,
        memory: MemoryVault,
        emit_event: EventEmitter,
    ) -> DepartmentOutput:
        self.calls += 1
        return self.output


class Worker(BaseDepartment):
    name = "engineering"
    role_description = "Write and record a task-specific mission artifact"

    def __init__(self, router: LLMRouter, trace: ExecutionTrace, name: str = "engineering") -> None:
        super().__init__(router)
        self.trace = trace
        self.name = name

    async def execute_task(
        self,
        task: TaskNode,
        sandbox: WorkspaceSandbox,
        memory: MemoryVault,
        emit_event: EventEmitter,
    ) -> DepartmentOutput:
        self.trace.active += 1
        self.trace.peak = max(self.trace.peak, self.trace.active)
        self.trace.timeline.append(("start", task.title))
        try:
            if self.trace.barrier is not None and not self.trace.barrier.is_set():
                if self.trace.active == 2:
                    self.trace.barrier.set()
                await self.trace.barrier.wait()
            if self.trace.block:
                await asyncio.Event().wait()
            if task.title in self.trace.fail:
                raise RuntimeError("deliberate fixture failure")
            await emit_event(
                AgentThoughtEvent(
                    mission_id=task.mission_id,
                    task_id=task.id,
                    department=self.name,
                    thought=f"Delivering {task.title}",
                )
            )
            path = f"outputs/{task.title}.txt"
            await sandbox.write_file(path, task.description)
            await memory.remember(
                MemoryKind.ARCHITECTURE_DECISION, task.title, task.description, department=self.name
            )
            return {
                "summary": f"Delivered {task.title}",
                "artifacts": [{"file_path": path, "artifact_type": "text"}],
            }
        finally:
            self.trace.active -= 1
            self.trace.timeline.append(("finish", task.title))
