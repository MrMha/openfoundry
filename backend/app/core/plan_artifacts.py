"""Canonical plan materialization shared by planning plugins and the coordinator."""

from __future__ import annotations

from collections.abc import Sequence

from backend.app.core.memory_vault import MemoryVault
from backend.app.core.planning import MissionPlan
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.models.mission import TaskNode


async def write_changed(sandbox: WorkspaceSandbox, path: str, content: str) -> None:
    """Avoid rewriting an identical artifact when a plugin and scheduler share a hook."""
    try:
        if await sandbox.read_file(path) == content:
            return
    except FileNotFoundError:
        pass
    await sandbox.write_file(path, content)


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


async def materialize_plan(
    plan: MissionPlan, sandbox: WorkspaceSandbox, memory: MemoryVault
) -> None:
    """Write a validated plan, replacing earlier drafts and upserting canonical decisions.

    Validation belongs to the caller, which knows its available departments. No database
    TaskNodes are created here: the coordinator alone owns task identities and transitions.
    """
    if sandbox.mission_id != memory.mission_id:
        raise ValueError("Plan resources must belong to the same mission")
    state = "# Mission state\n\nStatus: planned\n\n"
    state += "| Task key | Department | Dependencies | Approval | Status |\n"
    state += "| --- | --- | --- | --- | --- |\n"
    for task in plan.tasks:
        state += (
            f"| {task.key} | {task.department} | {', '.join(task.dependencies) or 'none'} | "
            f"{'required' if task.requires_approval else 'no'} | pending |\n"
        )
    # Validate both sizes before changing either canonical file.
    if max(len(plan.specification.encode()), len(state.encode())) > sandbox.max_file_bytes:
        raise ValueError("Plan artifacts exceed sandbox file limits")
    await write_changed(sandbox, "SPEC.md", plan.specification)
    current = await memory.get_spec()
    if current is None or current.content != plan.specification:
        await memory.save_spec(plan.specification)
    await write_changed(sandbox, "STATE.md", state)


async def write_runtime_state(
    sandbox: WorkspaceSandbox, status: str, tasks: Sequence[TaskNode]
) -> str:
    """Render the committed task snapshot; dependencies use durable TaskNode UUIDs."""
    content = f"# Mission state\n\nMission: {sandbox.mission_id}\nStatus: {status}\n\n"
    content += "| Task ID | Title | Department | Status | Approval | Dependencies |\n"
    content += "| --- | --- | --- | --- | --- | --- |\n"
    for task in tasks:
        content += (
            f"| {task.id} | {cell(task.title)} | {task.department} | {task.status.value} | "
            f"{'required' if task.requires_approval else 'no'} | {cell(task.dependencies_json)} |\n"
        )
    await write_changed(sandbox, "STATE.md", content)
    return content
