"""Durable, single-coordinator DAG execution with transactional approval checkpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.ws_war_room import ConnectionManager
from backend.app.config import MissionExecution, Settings, mission_settings
from backend.app.core.llm_router import LLMRouter, LLMRouterError
from backend.app.core.mcp_bridge import MCPBridge
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.plan_artifacts import materialize_plan, write_runtime_state
from backend.app.core.planning import MissionPlan
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.database import Database
from backend.app.departments.base_department import (
    BaseDepartment,
    DepartmentExecutionError,
    load_department_plugins,
    registered_departments,
)
from backend.app.models.events import (
    AgentThoughtEvent,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    BrowserFrameEvent,
    CodeDiffEvent,
    MissionStatusChangeEvent,
    TaskStatusChangeEvent,
    WarRoomEvent,
)
from backend.app.models.mission import Artifact, Mission, MissionStatus, TaskNode, TaskStatus
from backend.app.models.orchestration import MissionExecutionProfile, MissionRun, TaskApproval

logger = logging.getLogger(__name__)
_TERMINAL_MISSIONS = {MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED}
_TERMINAL_TASKS = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.SKIPPED,
    TaskStatus.CANCELLED,
}


class OrchestrationError(RuntimeError):
    """A mission operation cannot be performed in its current state."""


class MissionMissingError(OrchestrationError):
    """The requested mission or task does not exist."""


class ApprovalConflictError(OrchestrationError):
    """The task has no pending approval or already received a different decision."""


class _JSONOutput(RootModel[dict[str, JsonValue]]):
    """Validate arbitrary plugin output at the scheduler boundary."""


class _ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(min_length=1, max_length=4096)
    artifact_type: str = Field(min_length=1, max_length=100)


class MissionOrchestrator:
    """One active coordinator per SQLite file; bounded concurrent execution per mission.

    State changes and replayable events commit in the same database transaction. Files,
    memory writes, and provider calls are external effects: interrupted running tasks fail
    visibly and are never retried automatically. Pending tasks and approval waits recover.
    Plugins are trusted in-process code and must honor cancellation and their supplied
    sandbox. Bundled research may access public web sources; every paid LLM call uses the router.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        manager: ConnectionManager,
        *,
        departments: Mapping[str, BaseDepartment] | None = None,
        mcp_bridge: MCPBridge | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.manager = manager
        self.mcp_bridge = mcp_bridge
        if departments is None:
            load_department_plugins(settings.department_plugins)
            llm = LLMRouter(settings, database)
            departments = {name: plugin(llm) for name, plugin in registered_departments().items()}
        self.departments = dict(departments)
        if "chief_of_staff" not in self.departments:
            raise ValueError("A chief_of_staff department is required")
        if any(name != department.name for name, department in self.departments.items()):
            raise ValueError("Department mapping keys must match plugin names")
        self._runners: dict[UUID, asyncio.Task[None]] = {}
        self._wakeups: dict[UUID, asyncio.Event] = {}
        self._coordinator_fd: int | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._stopping:
            raise OrchestrationError(
                "A closed coordinator cannot be restarted; create a new instance"
            )
        if self._coordinator_fd is not None:
            return
        database_file = self.database.engine.url.database
        if not database_file or database_file == ":memory:" or database_file.startswith("file:"):
            raise OrchestrationError("Mission orchestration requires a persistent SQLite file path")
        lock = self.settings.data_dir / "coordinator.lock"
        resolved = await asyncio.to_thread(Path(database_file).resolve)
        lock = resolved.with_suffix(".coordinator.lock")
        descriptor = os.open(
            lock,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            if sys.platform == "win32":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise OrchestrationError(
                "Another coordinator already owns this database; run one API worker"
            ) from None
        self._coordinator_fd = descriptor
        try:
            async with self.database.session() as session:
                missions = list(
                    (
                        await session.scalars(
                            select(Mission)
                            .join(MissionRun, Mission.id == MissionRun.mission_id)
                            .where(Mission.status.not_in(_TERMINAL_MISSIONS))
                        )
                    ).all()
                )
            for mission in missions:
                async with self.database.session() as session:
                    interrupted = list(
                        (
                            await session.scalars(
                                select(TaskNode).where(
                                    TaskNode.mission_id == mission.id,
                                    TaskNode.status == TaskStatus.RUNNING,
                                )
                            )
                        ).all()
                    )
                if interrupted:
                    await self._fail(
                        mission.id,
                        "A running task was interrupted. Inspect its outputs and retained "
                        "LLM reservations before starting a new mission.",
                    )
                elif mission.status != MissionStatus.PAUSED:
                    self._enqueue(mission.id)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._stopping = True
        tasks = list(self._runners.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._coordinator_fd is not None:
            os.close(self._coordinator_fd)
            self._coordinator_fd = None

    def _enqueue(self, mission_id: UUID) -> None:
        if self._coordinator_fd is None or self._stopping:
            raise OrchestrationError("The mission coordinator is not running")
        if mission_id in self._runners:
            return
        self._wakeups[mission_id] = asyncio.Event()
        task = asyncio.create_task(self._run(mission_id), name=f"mission:{mission_id}")
        self._runners[mission_id] = task

        def finished(completed: asyncio.Task[None]) -> None:
            self._runners.pop(mission_id, None)
            self._wakeups.pop(mission_id, None)
            if not completed.cancelled() and completed.exception() is not None:
                logger.error("Mission runner terminated unexpectedly: mission=%s", mission_id)

        task.add_done_callback(finished)

    async def wait(self, mission_id: UUID) -> None:
        task = self._runners.get(mission_id)
        if task is not None:
            await asyncio.shield(task)

    async def _task_event(
        self, session: AsyncSession, task: TaskNode, previous: TaskStatus | None
    ) -> None:
        await self.manager.append(
            session,
            TaskStatusChangeEvent(
                mission_id=task.mission_id,
                task_id=task.id,
                department=task.department,
                previous_status=previous,
                status=task.status,
                title=task.title,
                description=task.description,
                dependencies=tuple(UUID(value) for value in json.loads(task.dependencies_json)),
                requires_approval=task.requires_approval,
                output_summary=task.output_summary,
            ),
        )

    async def _mission_event(
        self,
        session: AsyncSession,
        mission: Mission,
        previous: MissionStatus | None,
        *,
        error: str | None = None,
    ) -> None:
        await self.manager.append(
            session,
            MissionStatusChangeEvent(
                mission_id=mission.id,
                previous_status=previous,
                status=mission.status,
                title=mission.title,
                goal=mission.goal,
                error=error,
            ),
        )

    async def submit(
        self, goal: str, *, title: str | None = None, execution: MissionExecution | None = None
    ) -> Mission:
        if not goal.strip() or len(goal) > 16_384:
            raise ValueError("Mission goal must contain between one and 16384 characters")
        if title is not None and (not title.strip() or len(title) > 255):
            raise ValueError("Mission title must contain between one and 255 characters")
        if self._coordinator_fd is None or self._stopping:
            raise OrchestrationError("The mission coordinator is not running")
        execution = execution or MissionExecution()
        planning_description = goal
        if execution.blueprint_id:
            from backend.app.core.blueprints import planning_blueprint

            context, parameters = planning_blueprint(
                execution.blueprint_id, execution.blueprint_parameters
            )
            planning_description += (
                "\n\nRequested blueprint (adapt to the goal; preserve gates):\n" + context
            )
            execution = execution.model_copy(update={"blueprint_parameters": parameters})
        elif execution.blueprint_parameters:
            raise ValueError("Blueprint parameters require a blueprint identifier")
        selected = (
            mission_settings(self.settings, execution)
            if execution.mode == "live"
            else self.settings
        )
        execution = execution.model_copy(update={"budget_usd": selected.max_mission_cost_usd})
        mission_id, planning_id = uuid4(), uuid4()
        async with self.database.session(write=True) as session:
            mission = Mission(
                id=mission_id,
                title=title or goal.strip().splitlines()[0][:80],
                goal=goal,
                status=MissionStatus.PENDING,
            )
            planning = TaskNode(
                id=planning_id,
                mission_id=mission_id,
                department="chief_of_staff",
                title="Plan mission",
                description=planning_description,
                dependencies_json="[]",
                status=TaskStatus.PENDING,
                requires_approval=False,
            )
            session.add(mission)
            await session.flush()
            session.add_all(
                [
                    planning,
                    MissionRun(mission_id=mission_id, planning_task_id=planning_id),
                    MissionExecutionProfile(
                        mission_id=mission_id, configuration_json=execution.model_dump_json()
                    ),
                ]
            )
            await session.flush()
            await self._mission_event(session, mission, None)
            await self._task_event(session, planning, None)
        self.manager.notify(mission_id)
        self._enqueue(mission_id)
        return mission

    async def _transition_mission(self, mission_id: UUID, status: MissionStatus) -> None:
        async with self.database.session(write=True) as session:
            mission = await session.get(Mission, mission_id)
            if mission is None or mission.status in _TERMINAL_MISSIONS or mission.status == status:
                return
            previous = mission.status
            mission.status = status
            await self._mission_event(session, mission, previous)
        self.manager.notify(mission_id)

    async def _checkpoint(self, mission_id: UUID, ready_ids: list[UUID]) -> bool:
        created = False
        async with self.database.session(write=True) as session:
            approvals = set(
                (
                    await session.scalars(
                        select(TaskApproval.task_id).where(
                            TaskApproval.mission_id == mission_id,
                            TaskApproval.decision == "approved",
                        )
                    )
                ).all()
            )
            for task_id in ready_ids:
                task = await session.get(TaskNode, task_id)
                if task is not None and task.requires_approval and task.id not in approvals:
                    task.status = TaskStatus.AWAITING_APPROVAL
                    approval = TaskApproval(
                        task_id=task.id,
                        mission_id=mission_id,
                        approval_id=uuid4(),
                        decision="requested",
                    )
                    session.add(approval)
                    await self._task_event(session, task, TaskStatus.PENDING)
                    await self.manager.append(
                        session,
                        ApprovalRequestEvent(
                            mission_id=mission_id,
                            approval_id=approval.approval_id,
                            task_id=task.id,
                            department=task.department,
                            title=task.title,
                            description=task.description,
                            approval_type="plan",
                            context={"dependencies": json.loads(task.dependencies_json)},
                        ),
                    )
                    created = True
            if created:
                mission = await session.get(Mission, mission_id)
                if mission is None:
                    raise MissionMissingError("Mission disappeared")
                previous = mission.status
                mission.status = MissionStatus.AWAITING_APPROVAL
                await self._mission_event(session, mission, previous)
        if created:
            self.manager.notify(mission_id)
        return created

    async def decide(
        self,
        mission_id: UUID,
        task_id: UUID,
        decision: Literal["approved", "rejected"],
        *,
        reason: str | None = None,
    ) -> TaskApproval:
        if reason is not None and len(reason) > 4096:
            raise ValueError("Approval reason exceeds 4096 characters")
        async with self.database.session(write=True) as session:
            changed = await session.scalar(
                update(TaskApproval)
                .where(
                    TaskApproval.task_id == task_id,
                    TaskApproval.mission_id == mission_id,
                    TaskApproval.decision == "requested",
                )
                .values(decision=decision, reason=reason, decided_at=datetime.now(UTC))
                .returning(TaskApproval.task_id)
            )
            approval = await session.get(TaskApproval, task_id)
            if approval is None or approval.mission_id != mission_id:
                raise MissionMissingError("Mission task has no approval request")
            if changed is None:
                if approval.decision != decision:
                    raise ApprovalConflictError("Approval already has a different decision")
                return approval
            task = await session.get(TaskNode, task_id)
            mission = await session.get(Mission, mission_id)
            if task is None or mission is None:
                raise MissionMissingError("Mission or task does not exist")
            if (
                task.status != TaskStatus.AWAITING_APPROVAL
                or mission.status != MissionStatus.AWAITING_APPROVAL
            ):
                raise ApprovalConflictError("Mission is no longer awaiting this approval")
            await self.manager.append(
                session,
                ApprovalDecisionEvent(
                    mission_id=mission_id,
                    task_id=task_id,
                    approval_id=approval.approval_id,
                    department=task.department,
                    decision=decision,
                    reason=reason,
                ),
            )
            if decision == "approved":
                previous = task.status
                task.status = TaskStatus.PENDING
                await self._task_event(session, task, previous)
                requested = await session.scalar(
                    select(TaskApproval.task_id)
                    .where(
                        TaskApproval.mission_id == mission_id, TaskApproval.decision == "requested"
                    )
                    .limit(1)
                )
                if requested is None:
                    mission.status = MissionStatus.RUNNING
                    await self._mission_event(session, mission, MissionStatus.AWAITING_APPROVAL)
            else:
                pending = list(
                    (
                        await session.scalars(
                            select(TaskNode).where(
                                TaskNode.mission_id == mission_id,
                                TaskNode.status.not_in(_TERMINAL_TASKS),
                            )
                        )
                    ).all()
                )
                for pending_task in pending:
                    pending_previous = pending_task.status
                    pending_task.status = TaskStatus.CANCELLED
                    await self._task_event(session, pending_task, pending_previous)
                mission.status = MissionStatus.CANCELLED
                await self._mission_event(
                    session,
                    mission,
                    MissionStatus.AWAITING_APPROVAL,
                    error="Task approval rejected",
                )
                run = await session.get(MissionRun, mission_id)
                if run is not None:
                    run.last_error = "Task approval rejected"
                remaining = (
                    await session.scalars(
                        select(TaskApproval).where(
                            TaskApproval.mission_id == mission_id,
                            TaskApproval.decision == "requested",
                        )
                    )
                ).all()
                by_id = {item.id: item for item in pending}
                for other in remaining:
                    other.decision = "rejected"
                    other.reason = "Mission cancelled by another rejected checkpoint"
                    other.decided_at = datetime.now(UTC)
                    await self.manager.append(
                        session,
                        ApprovalDecisionEvent(
                            mission_id=mission_id,
                            task_id=other.task_id,
                            approval_id=other.approval_id,
                            department=by_id[other.task_id].department,
                            decision="rejected",
                            reason=other.reason,
                        ),
                    )
        self.manager.notify(mission_id)
        if mission_id in self._wakeups:
            self._wakeups[mission_id].set()
        return approval

    async def _persist_plan(
        self,
        task: TaskNode,
        output: dict[str, JsonValue],
        sandbox: WorkspaceSandbox,
        memory: MemoryVault,
    ) -> None:
        plan = MissionPlan.model_validate(output)
        plan.validate_dag(self.departments.keys())
        ids = {item.key: uuid5(task.mission_id, f"task:{item.key}") for item in plan.tasks}
        async with self.database.session(write=True) as session:
            existing = {
                node.id: node
                for node in (
                    await session.scalars(
                        select(TaskNode).where(
                            TaskNode.mission_id == task.mission_id, TaskNode.id != task.id
                        )
                    )
                ).all()
            }
            # A planning hook may replace a draft before dispatch. Started or approved
            # work needs a new mission; never silently rewrite already authorized scope.
            approval = await session.scalar(
                select(TaskApproval.task_id)
                .where(TaskApproval.mission_id == task.mission_id)
                .limit(1)
            )
            if approval is not None or any(
                node.status != TaskStatus.PENDING for node in existing.values()
            ):
                raise OrchestrationError("Only an unstarted, unapproved draft can be revised")
            await materialize_plan(plan, sandbox, memory)
            for old_id, old in existing.items():
                if old_id not in ids.values():
                    old.status = TaskStatus.CANCELLED
                    await self._task_event(session, old, TaskStatus.PENDING)
                    await session.delete(old)
            for item in plan.tasks:
                dependencies = [ids[key] for key in item.dependencies] or [task.id]
                node = TaskNode(
                    id=ids[item.key],
                    mission_id=task.mission_id,
                    department=item.department,
                    title=item.title,
                    description=item.description,
                    dependencies_json=json.dumps([str(key) for key in dependencies]),
                    status=TaskStatus.PENDING,
                    requires_approval=item.requires_approval,
                )
                if node.id in existing:
                    # A fresh descriptor event supersedes the earlier draft in replay.
                    await session.merge(node)
                else:
                    session.add(node)
                await self._task_event(session, node, None)
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.mission_id == task.mission_id, Artifact.file_path == "SPEC.md"
                )
            )
            if artifact is None:
                session.add(
                    Artifact(
                        mission_id=task.mission_id,
                        file_path="SPEC.md",
                        artifact_type="specification",
                        content_preview=plan.specification[:1024],
                    )
                )
            else:
                artifact.content_preview = plan.specification[:1024]

    async def _sync_state(self, mission_id: UUID, sandbox: WorkspaceSandbox) -> None:
        async with self.database.session() as session:
            mission = await session.get(Mission, mission_id)
            tasks = list(
                (
                    await session.scalars(
                        select(TaskNode)
                        .where(TaskNode.mission_id == mission_id)
                        .order_by(TaskNode.id)
                    )
                ).all()
            )
        if mission is None:
            return
        content = await write_runtime_state(sandbox, mission.status.value, tasks)
        async with self.database.session(write=True) as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.mission_id == mission_id, Artifact.file_path == "STATE.md"
                )
            )
            if artifact is None:
                session.add(
                    Artifact(
                        mission_id=mission_id,
                        file_path="STATE.md",
                        artifact_type="mission_state",
                        content_preview=content[:1024],
                    )
                )
            else:
                artifact.content_preview = content[:1024]

    async def _artifact_records(
        self,
        mission_id: UUID,
        output: dict[str, JsonValue],
        sandbox: WorkspaceSandbox,
    ) -> list[Artifact]:
        references = output.get("artifacts", [])
        if not isinstance(references, list) or len(references) > 100:
            raise ValueError("Plugin artifacts must be a bounded list")
        artifacts: dict[str, Artifact] = {}
        for raw in references:
            reference = _ArtifactReference.model_validate(raw)
            content = await sandbox.read_bytes(reference.file_path)
            artifacts[reference.file_path] = Artifact(
                mission_id=mission_id,
                file_path=reference.file_path,
                artifact_type=reference.artifact_type,
                content_preview=content[:4096].decode("utf-8", errors="replace")[:1024],
            )
        return list(artifacts.values())

    async def _save_artifacts(self, session: AsyncSession, artifacts: list[Artifact]) -> None:
        for artifact in artifacts:
            previous = await session.scalar(
                select(Artifact).where(
                    Artifact.mission_id == artifact.mission_id,
                    Artifact.file_path == artifact.file_path,
                )
            )
            if previous is None:
                session.add(artifact)
            else:
                previous.artifact_type = artifact.artifact_type
                previous.content_preview = artifact.content_preview

    async def _execute(
        self,
        task_id: UUID,
        planning_id: UUID,
        sandbox: WorkspaceSandbox,
        memory: MemoryVault,
        departments: Mapping[str, BaseDepartment] | None = None,
    ) -> None:
        async with self.database.session(write=True) as session:
            task = await session.get(TaskNode, task_id)
            if task is None or task.status != TaskStatus.PENDING:
                raise OrchestrationError("Task is not pending")
            task.status = TaskStatus.RUNNING
            await self._task_event(session, task, TaskStatus.PENDING)
        self.manager.notify(task.mission_id)

        async def emit(event: WarRoomEvent) -> None:
            if not isinstance(event, (AgentThoughtEvent, BrowserFrameEvent, CodeDiffEvent)):
                raise ValueError(
                    "Only the orchestrator may emit state changes and approval requests"
                )
            if (
                event.mission_id != task.mission_id
                or event.task_id not in {None, task.id}
                or event.department != task.department
            ):
                raise ValueError(
                    "Plugin event must belong to its current mission, task, and department"
                )
            await self.manager.publish(event)

        try:
            async with asyncio.timeout(self.settings.department_timeout_seconds):
                result = await (departments or self.departments)[task.department].execute_task(
                    task, sandbox, memory, emit
                )
            output = _JSONOutput.model_validate(result).root
            encoded = json.dumps(output, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 2 * 1024 * 1024:
                raise ValueError("Plugin result exceeds two MiB")
            if task.id == planning_id:
                await self._persist_plan(task, output, sandbox, memory)
            artifacts = await self._artifact_records(task.mission_id, output, sandbox)
            summary = output.get(
                "summary",
                "Mission DAG and specification prepared" if task.id == planning_id else encoded,
            )
            if not isinstance(summary, str):
                raise ValueError("Plugin output summary must be text")
            async with self.database.session(write=True) as session:
                current = await session.get(TaskNode, task.id)
                if current is None:
                    raise MissionMissingError("Task disappeared")
                current.status = TaskStatus.COMPLETED
                current.output_summary = summary[:65_536]
                await self._save_artifacts(session, artifacts)
                await self._task_event(session, current, TaskStatus.RUNNING)
        except BaseException as error:
            diagnostic_artifacts: list[Artifact] = []
            if isinstance(error, DepartmentExecutionError):
                try:
                    diagnostic_artifacts = await self._artifact_records(
                        task.mission_id, _JSONOutput.model_validate(error.output).root, sandbox
                    )
                except (ValueError, OSError, RuntimeError):
                    logger.error(
                        "Failed task returned invalid diagnostic artifacts: task=%s", task.id
                    )
            message = (
                str(error)
                if isinstance(error, (LLMRouterError, DepartmentExecutionError))
                else f"Task failed ({type(error).__name__}); inspect its outputs before retrying"
            )
            async with self.database.session(write=True) as session:
                current = await session.get(TaskNode, task.id)
                if current is not None:
                    current.status = TaskStatus.FAILED
                    current.output_summary = message[:65_536]
                    await self._save_artifacts(session, diagnostic_artifacts)
                    await self._task_event(session, current, TaskStatus.RUNNING)
            self.manager.notify(task.mission_id)
            raise
        self.manager.notify(task.mission_id)

    async def _fail(self, mission_id: UUID, message: str) -> None:
        async with self.database.session(write=True) as session:
            mission = await session.get(Mission, mission_id)
            if mission is None or mission.status in _TERMINAL_MISSIONS:
                return
            pending = list(
                (
                    await session.scalars(
                        select(TaskNode).where(
                            TaskNode.mission_id == mission_id,
                            TaskNode.status.not_in(_TERMINAL_TASKS),
                        )
                    )
                ).all()
            )
            for task in pending:
                previous = task.status
                task.status = (
                    TaskStatus.FAILED if previous == TaskStatus.RUNNING else TaskStatus.SKIPPED
                )
                task.output_summary = message[:65_536]
                await self._task_event(session, task, previous)
            previous_mission = mission.status
            mission.status = MissionStatus.FAILED
            run = await session.get(MissionRun, mission_id)
            if run is not None:
                run.last_error = message
            await self._mission_event(session, mission, previous_mission, error=message)
        self.manager.notify(mission_id)

    async def _run(self, mission_id: UUID) -> None:
        try:
            async with self.database.session() as session:
                profile = await session.get(MissionExecutionProfile, mission_id)
            execution = (
                MissionExecution.model_validate_json(profile.configuration_json)
                if profile
                else MissionExecution()
            )
            departments = self.departments
            if execution.mode == "demo":
                from backend.app.core.demo import demo_departments

                departments = demo_departments(self.settings, self.database)
            elif (
                execution.provider
                or execution.model
                or (
                    execution.budget_usd != self.settings.max_mission_cost_usd
                    and execution.budget_usd is not None
                )
            ):
                llm = LLMRouter(mission_settings(self.settings, execution), self.database)
                departments = {name: type(plugin)(llm) for name, plugin in self.departments.items()}
            async with WorkspaceSandbox(
                mission_id,
                root=self.settings.workspace_root,
                sandbox_mode=self.settings.sandbox_mode,
            ) as sandbox:
                for department in departments.values():
                    department.mcp = self.mcp_bridge
                memory = MemoryVault(mission_id, storage_root=self.settings.memory_root)
                while True:
                    wakeup = self._wakeups[mission_id]
                    wakeup.clear()
                    async with self.database.session() as session:
                        mission = await session.get(Mission, mission_id)
                        run = await session.get(MissionRun, mission_id)
                        tasks = list(
                            (
                                await session.scalars(
                                    select(TaskNode)
                                    .where(TaskNode.mission_id == mission_id)
                                    .order_by(TaskNode.id)
                                )
                            ).all()
                        )
                    await self._sync_state(mission_id, sandbox)
                    if mission is None or run is None or mission.status in _TERMINAL_MISSIONS:
                        return
                    if mission.status in {MissionStatus.AWAITING_APPROVAL, MissionStatus.PAUSED}:
                        await wakeup.wait()
                        continue
                    if any(task.status == TaskStatus.FAILED for task in tasks):
                        await self._fail(
                            mission_id,
                            "A department task failed; remaining tasks were not dispatched",
                        )
                        return
                    if tasks and all(task.status == TaskStatus.COMPLETED for task in tasks):
                        await self._transition_mission(mission_id, MissionStatus.COMPLETED)
                        await self._sync_state(mission_id, sandbox)
                        return
                    completed = {task.id for task in tasks if task.status == TaskStatus.COMPLETED}
                    ready = [
                        task.id
                        for task in tasks
                        if task.status == TaskStatus.PENDING
                        and {UUID(value) for value in json.loads(task.dependencies_json)}
                        <= completed
                    ]
                    if not ready:
                        await self._fail(
                            mission_id,
                            "No executable task remains; persisted dependencies are inconsistent",
                        )
                        return
                    if await self._checkpoint(mission_id, ready):
                        continue
                    await self._transition_mission(mission_id, MissionStatus.RUNNING)
                    results = await asyncio.gather(
                        *(
                            self._execute(
                                task_id, run.planning_task_id, sandbox, memory, departments
                            )
                            for task_id in ready[: self.settings.max_parallel_tasks]
                        ),
                        return_exceptions=True,
                    )
                    if any(isinstance(result, BaseException) for result in results):
                        await self._fail(
                            mission_id,
                            "A department task failed; remaining tasks were not dispatched",
                        )
                        return
        except asyncio.CancelledError:
            # A clean approval wait can survive restart. Running tasks may have external
            # effects and are marked failed by _execute; startup handles abrupt crashes.
            async with self.database.session() as session:
                failed = await session.scalar(
                    select(TaskNode.id)
                    .where(
                        TaskNode.mission_id == mission_id,
                        TaskNode.status.in_([TaskStatus.RUNNING, TaskStatus.FAILED]),
                    )
                    .limit(1)
                )
            if failed is not None:
                await self._fail(
                    mission_id,
                    "Mission interrupted during execution; inspect outputs and reservations",
                )
            raise
        except Exception as error:
            logger.error(
                "Mission scheduling failed: mission=%s error=%s", mission_id, type(error).__name__
            )
            await self._fail(mission_id, f"Mission scheduling failed ({type(error).__name__})")

        finally:
            # Keep the human-readable snapshot current on failure, rejection, and shutdown.
            async with WorkspaceSandbox(
                mission_id,
                root=self.settings.workspace_root,
                sandbox_mode=self.settings.sandbox_mode,
            ) as final:
                await self._sync_state(mission_id, final)
