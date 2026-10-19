"""Single-operator mission REST API; durable state remains the source of truth."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from backend.app.api.auth import authorize_http
from backend.app.config import MissionExecution, MissionPreferences
from backend.app.core.demo import DEMO_GOAL
from backend.app.core.orchestrator import (
    ApprovalConflictError,
    MissionMissingError,
    MissionOrchestrator,
    OrchestrationError,
)
from backend.app.core.workspace_sandbox import UnsafePathError, WorkspaceSandbox
from backend.app.database import Database
from backend.app.models.mission import Artifact, Mission, MissionStatus, TaskNode, TaskStatus
from backend.app.models.orchestration import (
    MissionEventRecord,
    MissionExecutionProfile,
    MissionRun,
    TaskApproval,
)

router = APIRouter(
    prefix="/api/missions", tags=["missions"], dependencies=[Depends(authorize_http)]
)


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=16_384, pattern=r"\S")
    title: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"\S")
    configuration: MissionPreferences = Field(default_factory=MissionPreferences)
    blueprint_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    blueprint_parameters: dict[str, str] = Field(default_factory=dict, max_length=16)


class MissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    goal: str
    status: MissionStatus
    total_cost_usd: Decimal
    reserved_cost_usd: Decimal
    created_at: datetime


class TaskView(BaseModel):
    id: UUID
    mission_id: UUID
    department: str
    title: str
    description: str
    dependencies: list[UUID]
    status: TaskStatus
    output_summary: str | None
    requires_approval: bool


class ApprovalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    approval_id: UUID
    task_id: UUID
    mission_id: UUID
    decision: Literal["requested", "approved", "rejected"]
    reason: str | None
    created_at: datetime
    decided_at: datetime | None


class ArtifactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    file_path: str
    artifact_type: str
    content_preview: str | None


class MissionDetail(MissionView):
    execution: MissionExecution
    tasks: list[TaskView]
    approvals: list[ApprovalView]
    artifacts: list[ArtifactView]
    last_error: str | None
    last_event_id: int


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=4096)


class FileContent(BaseModel):
    path: str
    encoding: Literal["utf-8", "base64"]
    content: str
    size_bytes: int


def coordinator(request: Request) -> MissionOrchestrator:
    return cast(MissionOrchestrator, request.app.state.orchestrator)


@router.post("", response_model=MissionView, status_code=202)
async def create_mission(body: MissionCreate, request: Request) -> MissionView:
    try:
        mission = await coordinator(request).submit(
            body.goal,
            title=body.title,
            execution=MissionExecution(
                **body.configuration.model_dump(),
                blueprint_id=body.blueprint_id,
                blueprint_parameters=body.blueprint_parameters,
            ),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except OrchestrationError:
        raise HTTPException(503, "Mission coordinator is unavailable") from None
    return MissionView.model_validate(mission)


@router.post("/demo", response_model=MissionView, status_code=202)
async def create_demo(request: Request) -> MissionView:
    """Offline fixtures, real department execution, normal operator approval gate."""
    try:
        mission = await coordinator(request).submit(
            DEMO_GOAL,
            title="Self-Healing Calculator · Offline Demo",
            execution=MissionExecution(mode="demo"),
        )
    except OrchestrationError:
        raise HTTPException(503, "Mission coordinator is unavailable") from None
    return MissionView.model_validate(mission)


@router.get("", response_model=list[MissionView])
async def list_missions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[MissionView]:
    database = cast(Database, request.app.state.database)
    async with database.session() as session:
        missions = (
            await session.scalars(
                select(Mission)
                .order_by(Mission.created_at.desc(), Mission.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [MissionView.model_validate(mission) for mission in missions]


@router.get("/{mission_id}", response_model=MissionDetail)
async def get_mission(mission_id: UUID, request: Request) -> MissionDetail:
    database = cast(Database, request.app.state.database)
    async with database.session() as session:
        # Pin a real SQLite read snapshot across state, approvals, and event cursor.
        await session.execute(text("BEGIN"))
        mission = await session.get(Mission, mission_id)
        if mission is None:
            raise HTTPException(404, "Mission does not exist")
        tasks = (
            await session.scalars(
                select(TaskNode).where(TaskNode.mission_id == mission_id).order_by(TaskNode.id)
            )
        ).all()
        approvals = (
            await session.scalars(
                select(TaskApproval)
                .where(TaskApproval.mission_id == mission_id)
                .order_by(TaskApproval.created_at, TaskApproval.task_id)
            )
        ).all()
        artifacts = (
            await session.scalars(
                select(Artifact).where(Artifact.mission_id == mission_id).order_by(Artifact.id)
            )
        ).all()
        run = await session.get(MissionRun, mission_id)
        profile = await session.get(MissionExecutionProfile, mission_id)
        execution = (
            MissionExecution.model_validate_json(profile.configuration_json)
            if profile
            else MissionExecution(budget_usd=coordinator(request).settings.max_mission_cost_usd)
        )
        cursor = await session.scalar(
            select(func.max(MissionEventRecord.sequence)).where(
                MissionEventRecord.mission_id == mission_id
            )
        )
        return MissionDetail(
            **MissionView.model_validate(mission).model_dump(),
            execution=execution,
            tasks=[
                TaskView(
                    id=task.id,
                    mission_id=task.mission_id,
                    department=task.department,
                    title=task.title,
                    description=task.description,
                    dependencies=json.loads(task.dependencies_json),
                    status=task.status,
                    output_summary=task.output_summary,
                    requires_approval=task.requires_approval,
                )
                for task in tasks
            ],
            approvals=[ApprovalView.model_validate(approval) for approval in approvals],
            artifacts=[ArtifactView.model_validate(artifact) for artifact in artifacts],
            last_error=run.last_error if run else None,
            last_event_id=cursor or 0,
        )


async def _decision(
    mission_id: UUID,
    task_id: UUID,
    request: Request,
    body: DecisionRequest | None,
    decision: Literal["approved", "rejected"],
) -> ApprovalView:
    try:
        approval = await coordinator(request).decide(
            mission_id, task_id, decision, reason=body.reason if body else None
        )
    except MissionMissingError:
        raise HTTPException(404, "Mission task has no approval request") from None
    except ApprovalConflictError as error:
        raise HTTPException(409, str(error)) from None
    return ApprovalView.model_validate(approval)


@router.post("/{mission_id}/tasks/{task_id}/approve", response_model=ApprovalView)
async def approve_task(
    mission_id: UUID, task_id: UUID, request: Request, body: DecisionRequest | None = None
) -> ApprovalView:
    return await _decision(mission_id, task_id, request, body, "approved")


@router.post("/{mission_id}/tasks/{task_id}/reject", response_model=ApprovalView)
async def reject_task(
    mission_id: UUID, task_id: UUID, request: Request, body: DecisionRequest | None = None
) -> ApprovalView:
    return await _decision(mission_id, task_id, request, body, "rejected")


@router.get("/{mission_id}/files", response_model=list[ArtifactView])
async def list_files(mission_id: UUID, request: Request) -> list[ArtifactView]:
    database = cast(Database, request.app.state.database)
    async with database.session() as session:
        if await session.get(Mission, mission_id) is None:
            raise HTTPException(404, "Mission does not exist")
        artifacts = (
            await session.scalars(
                select(Artifact)
                .where(Artifact.mission_id == mission_id)
                .order_by(Artifact.file_path, Artifact.id)
            )
        ).all()
        return [ArtifactView.model_validate(artifact) for artifact in artifacts]


@router.get("/{mission_id}/files/content", response_model=FileContent)
async def inspect_file(
    mission_id: UUID, request: Request, path: Annotated[str, Query(min_length=1, max_length=4096)]
) -> FileContent:
    orchestrator = coordinator(request)
    async with orchestrator.database.session() as session:
        if await session.get(Mission, mission_id) is None:
            raise HTTPException(404, "Mission does not exist")
    try:
        async with WorkspaceSandbox(
            mission_id,
            root=orchestrator.settings.workspace_root,
            sandbox_mode=orchestrator.settings.sandbox_mode,
        ) as sandbox:
            content = await sandbox.read_bytes(path)
    except FileNotFoundError:
        raise HTTPException(404, "Workspace file does not exist") from None
    except UnsafePathError:
        raise HTTPException(
            400, "Workspace path is unsafe, missing, or exceeds file limits"
        ) from None
    try:
        decoded = content.decode("utf-8")
        if "\0" in decoded:
            raise UnicodeError("Binary file")
    except UnicodeError:
        return FileContent(
            path=path,
            encoding="base64",
            content=base64.b64encode(content).decode("ascii"),
            size_bytes=len(content),
        )
    return FileContent(path=path, encoding="utf-8", content=decoded, size_bytes=len(content))
