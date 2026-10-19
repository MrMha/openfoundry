import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from orchestration_helpers import ExecutionTrace, Planner, Worker, node, plan
from sqlalchemy import select

from backend.app.api.ws_war_room import ConnectionManager
from backend.app.config import Settings
from backend.app.core.llm_router import LLMRouter
from backend.app.core.orchestrator import (
    ApprovalConflictError,
    MissionOrchestrator,
    OrchestrationError,
)
from backend.app.database import Database
from backend.app.models.events import parse_event
from backend.app.models.mission import Mission, MissionStatus, TaskNode, TaskStatus
from backend.app.models.orchestration import MissionRun, TaskApproval


@asynccontextmanager
async def service(
    settings: Settings,
    database: Database,
    output: dict[str, Any],
    trace: ExecutionTrace | None = None,
) -> AsyncIterator[tuple[MissionOrchestrator, Planner, ExecutionTrace]]:
    router = LLMRouter(settings, database)
    trace = trace or ExecutionTrace()
    planner = Planner(router, output)
    manager = ConnectionManager(database)
    departments = {
        name: Worker(router, trace, name) for name in ("engineering", "research", "growth")
    }
    departments["chief_of_staff"] = planner
    orchestrator = MissionOrchestrator(settings, database, manager, departments=departments)
    await orchestrator.start()
    try:
        yield orchestrator, planner, trace
    finally:
        await orchestrator.close()
        await manager.close()


async def mission_state(database: Database, mission_id: UUID) -> MissionStatus:
    async with database.session() as session:
        return (await session.get(Mission, mission_id)).status


async def wait_for_state(database: Database, mission_id: UUID, status: MissionStatus) -> None:
    for _ in range(200):
        actual = await mission_state(database, mission_id)
        if actual == status:
            return
        if actual in {MissionStatus.FAILED, MissionStatus.CANCELLED}:
            pytest.fail(f"Mission ended in {actual}, expected {status}")
        await asyncio.sleep(0.01)
    pytest.fail(f"Mission never reached {status}")


async def task_rows(database: Database, mission_id: UUID) -> list[TaskNode]:
    async with database.session() as session:
        return list(
            (await session.scalars(select(TaskNode).where(TaskNode.mission_id == mission_id))).all()
        )


async def approval_rows(database: Database, mission_id: UUID) -> list[TaskApproval]:
    async with database.session() as session:
        return list(
            (
                await session.scalars(
                    select(TaskApproval).where(TaskApproval.mission_id == mission_id)
                )
            ).all()
        )


async def test_ready_tasks_overlap_and_dependents_wait(
    settings: Settings, database: Database
) -> None:
    trace = ExecutionTrace(barrier=asyncio.Event())
    output = plan(
        node("research", department="research"),
        node("implementation"),
        node("launch_copy", ["research", "implementation"], department="growth"),
    )
    async with service(settings, database, output, trace) as (orchestrator, planner, trace):
        mission = await orchestrator.submit("Build the requested product")
        await asyncio.wait_for(orchestrator.wait(mission.id), 10)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        assert trace.peak == 2
        assert trace.timeline.index(("start", "launch_copy")) > trace.timeline.index(
            ("finish", "research")
        )
        assert trace.timeline.index(("start", "launch_copy")) > trace.timeline.index(
            ("finish", "implementation")
        )
        assert planner.calls == 1
        tasks = await task_rows(database, mission.id)
        assert len(tasks) == 4
        assert all(task.status == TaskStatus.COMPLETED for task in tasks)
        records = await orchestrator.manager.history(mission.id, 0)
        events = [parse_event(record.payload) for record in records]
        assert [event.sequence for event in events] == sorted({event.sequence for event in events})
        assert events[-1].type == "mission_status_change"
        assert events[-1].status == MissionStatus.COMPLETED
        assert (settings.mission_workspace(mission.id) / "SPEC.md").is_file()


@pytest.mark.parametrize(
    "output",
    [
        plan(node("a", ["b"]), node("b", ["a"])),
        plan(node("a", ["missing"])),
        plan(node("a", ["a"])),
        plan(node("a"), node("a")),
        plan(node("a", department="missing")),
        plan(node("a", department="chief_of_staff")),
    ],
)
async def test_invalid_dags_fail_before_dispatch(
    settings: Settings, database: Database, output: dict[str, Any]
) -> None:
    async with service(settings, database, output) as (orchestrator, _, trace):
        mission = await orchestrator.submit("Reject malformed plans")
        await orchestrator.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.FAILED
        assert not trace.timeline
        tasks = await task_rows(database, mission.id)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.FAILED
        assert not (settings.mission_workspace(mission.id) / "SPEC.md").exists()


async def test_all_checkpoint_approvals_are_required_and_idempotent(
    settings: Settings, database: Database
) -> None:
    async with service(
        settings, database, plan(node("one", approval=True), node("two", approval=True))
    ) as (orchestrator, _, trace):
        mission = await orchestrator.submit("Wait for both approvals")
        await wait_for_state(database, mission.id, MissionStatus.AWAITING_APPROVAL)
        approvals = await approval_rows(database, mission.id)
        assert len(approvals) == 2
        assert not trace.timeline
        await orchestrator.decide(mission.id, approvals[0].task_id, "approved")
        assert await mission_state(database, mission.id) == MissionStatus.AWAITING_APPROVAL
        assert not trace.timeline
        await asyncio.gather(
            *(orchestrator.decide(mission.id, approvals[1].task_id, "approved") for _ in range(3))
        )
        await orchestrator.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        assert len([item for item in trace.timeline if item[0] == "start"]) == 2
        with pytest.raises(ApprovalConflictError):
            await orchestrator.decide(mission.id, approvals[0].task_id, "rejected")


async def test_rejection_cancels_pending_work(settings: Settings, database: Database) -> None:
    async with service(
        settings, database, plan(node("blocked", approval=True), node("dependent", ["blocked"]))
    ) as (orchestrator, _, trace):
        mission = await orchestrator.submit("Reject the checkpoint")
        await wait_for_state(database, mission.id, MissionStatus.AWAITING_APPROVAL)
        approval = (await approval_rows(database, mission.id))[0]
        await orchestrator.decide(
            mission.id, approval.task_id, "rejected", reason="Revise the approach"
        )
        await orchestrator.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.CANCELLED
        assert not trace.timeline
        assert all(
            task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            for task in await task_rows(database, mission.id)
        )


async def test_approval_wait_recovers_after_restart(settings: Settings, database: Database) -> None:
    output = plan(node("blocked", approval=True))
    async with service(settings, database, output) as (first, first_planner, _):
        mission = await first.submit("Resume an existing approval wait")
        await wait_for_state(database, mission.id, MissionStatus.AWAITING_APPROVAL)
        approval = (await approval_rows(database, mission.id))[0]
        assert first_planner.calls == 1
    async with service(settings, database, output) as (second, planner, trace):
        await second.decide(mission.id, approval.task_id, "approved")
        await second.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        assert planner.calls == 0
        assert ("start", "blocked") in trace.timeline


async def test_task_failures_skip_descendants(settings: Settings, database: Database) -> None:
    trace = ExecutionTrace(fail={"failing"})
    async with service(
        settings, database, plan(node("failing"), node("dependent", ["failing"])), trace
    ) as (orchestrator, _, _):
        mission = await orchestrator.submit("Fail safely")
        await orchestrator.wait(mission.id)
        tasks = {task.title: task for task in await task_rows(database, mission.id)}
        assert tasks["failing"].status == TaskStatus.FAILED
        assert tasks["dependent"].status == TaskStatus.SKIPPED
        assert ("start", "dependent") not in trace.timeline
        assert await mission_state(database, mission.id) == MissionStatus.FAILED


async def test_rejection_records_every_outstanding_decision(
    settings: Settings, database: Database
) -> None:
    async with service(
        settings, database, plan(node("one", approval=True), node("two", approval=True))
    ) as (orchestrator, _, trace):
        mission = await orchestrator.submit("Cancel a batch with multiple checkpoints")
        await wait_for_state(database, mission.id, MissionStatus.AWAITING_APPROVAL)
        approval = (await approval_rows(database, mission.id))[0]
        await orchestrator.decide(mission.id, approval.task_id, "rejected")
        await orchestrator.wait(mission.id)
        assert not trace.timeline
        assert all(
            item.decision == "rejected" for item in await approval_rows(database, mission.id)
        )
        decisions = [
            json.loads(record.payload)
            for record in await orchestrator.manager.history(mission.id, 0)
            if json.loads(record.payload)["type"] == "approval_decision"
        ]
        assert len(decisions) == 2
        assert all(item["decision"] == "rejected" for item in decisions)


async def test_configured_parallelism_is_respected(settings: Settings, database: Database) -> None:
    settings = settings.model_copy(update={"max_parallel_tasks": 1})
    async with service(settings, database, plan(node("one"), node("two"))) as (
        orchestrator,
        _,
        trace,
    ):
        mission = await orchestrator.submit("Use only one execution slot")
        await orchestrator.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        assert trace.peak == 1


async def test_department_timeout_is_a_visible_failure(
    settings: Settings, database: Database
) -> None:
    settings = settings.model_copy(update={"department_timeout_seconds": 0.1})
    async with service(settings, database, plan(), ExecutionTrace(block=True)) as (
        orchestrator,
        _,
        _,
    ):
        mission = await orchestrator.submit("Bound a nonresponsive department")
        await asyncio.wait_for(orchestrator.wait(mission.id), 5)
        assert await mission_state(database, mission.id) == MissionStatus.FAILED


async def test_second_coordinator_cannot_dispatch_same_database(
    settings: Settings, database: Database
) -> None:
    async with service(settings, database, plan()) as (_, planner, _):
        second = MissionOrchestrator(
            settings, database, ConnectionManager(database), departments={"chief_of_staff": planner}
        )
        with pytest.raises(OrchestrationError, match="Another coordinator"):
            await second.start()


async def test_interrupted_tasks_are_not_retried(settings: Settings, database: Database) -> None:
    mission_id, task_id = uuid4(), uuid4()
    async with database.session() as session:
        session.add(
            Mission(
                id=mission_id,
                title="Interrupted",
                goal="An interrupted provider call",
                status=MissionStatus.RUNNING,
            )
        )
        await session.flush()
        session.add_all(
            [
                TaskNode(
                    id=task_id,
                    mission_id=mission_id,
                    department="chief_of_staff",
                    title="Plan mission",
                    description="Interrupted",
                    dependencies_json="[]",
                    status=TaskStatus.RUNNING,
                ),
                MissionRun(mission_id=mission_id, planning_task_id=task_id),
            ]
        )
    async with service(settings, database, plan()) as (_, planner, trace):
        assert await mission_state(database, mission_id) == MissionStatus.FAILED
        assert planner.calls == 0
        assert not trace.timeline
        assert (await task_rows(database, mission_id))[0].status == TaskStatus.FAILED


async def test_custom_department_name_survives_events_and_memory(
    settings: Settings, database: Database
) -> None:
    router = LLMRouter(settings, database)
    trace = ExecutionTrace()
    manager = ConnectionManager(database)
    orchestrator = MissionOrchestrator(
        settings,
        database,
        manager,
        departments={
            "chief_of_staff": Planner(router, plan(node("audit", department="security_review"))),
            "security_review": Worker(router, trace, "security_review"),
        },
    )
    await orchestrator.start()
    try:
        mission = await orchestrator.submit("Run a custom plugin")
        await orchestrator.wait(mission.id)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        records = await manager.history(mission.id, 0)
        assert any(
            json.loads(record.payload).get("department") == "security_review" for record in records
        )
    finally:
        await orchestrator.close()
