"""Chief planning contracts and real scheduler integration without provider charges."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import httpx
import pytest
from orchestration_helpers import ExecutionTrace, Worker, node, plan
from sqlalchemy import select
from test_departments import result
from test_orchestrator import mission_state, task_rows, wait_for_state

from backend.app.api.ws_war_room import ConnectionManager
from backend.app.core.llm_router import LLMRouter, LLMRouterError
from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.core.orchestrator import MissionOrchestrator, OrchestrationError
from backend.app.core.planning import MissionPlan
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments._research_web import PublicWeb
from backend.app.departments.base_department import load_department_plugins, registered_departments
from backend.app.departments.chief_of_staff import ChiefOfStaff
from backend.app.departments.researcher import Researcher
from backend.app.models.events import AgentThoughtEvent, parse_event
from backend.app.models.mission import Artifact, Mission, MissionStatus, TaskNode, TaskStatus


def planning_task(mission_id):
    return TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="chief_of_staff",
        title="Plan mission",
        description="Build a searchable knowledge base",
        status=TaskStatus.RUNNING,
        dependencies_json="[]",
        requires_approval=False,
    )


def delivery_plan():
    output = plan(
        node("research", department="researcher"),
        node("build", ["research"], department="engineer"),
        node("verify", ["build"], department="qa_self_healer"),
        node("launch", ["verify"], department="growth_career"),
    )
    output["specification"] = (
        "## Scope\nA searchable knowledge base.\n\n## Architecture decisions\n"
        "Use SQLite FTS to keep search local.\n\n## Acceptance tests\n"
        "Inserted documents are returned by keyword search.\n\n## Risks\n"
        "Search ranking needs representative test documents."
    )
    output["tasks"][0]["description"] = json.dumps(
        {
            "query": "SQLite FTS architecture",
            "urls": ["https://example.com/fts"],
            "prefer_browser": False,
        }
    )
    return output


def test_canonical_departments_are_registered_and_legacy_names_remain():
    load_department_plugins()
    plugins = registered_departments()
    assert plugins["chief_of_staff"] is ChiefOfStaff
    assert plugins["researcher"] is Researcher
    assert {
        "engineer",
        "qa_self_healer",
        "growth_career",
        "research",
        "engineering",
        "qa",
        "growth",
    } <= plugins.keys()


async def test_chief_saves_structured_spec_state_and_shared_architectural_decisions(
    settings, database
):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, delivery_plan()))
    task = planning_task(mission_id)
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    emitter = AsyncMock()
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        output = await ChiefOfStaff(router).execute_task(task, sandbox, memory, emitter)
        parsed = MissionPlan.model_validate(output)
        parsed.validate_dag(registered_departments())
        spec = await sandbox.read_file("SPEC.md")
        state = await sandbox.read_file("STATE.md")
        assert task.description in spec
        assert "## Architecture decisions" in spec
        assert "## Acceptance tests" in spec
        assert all(item.key in state for item in parsed.tasks)
        assert all(item.department in state for item in parsed.tasks)
        assert parsed.tasks[1].requires_approval
        assert (await memory.get_spec()).content == spec
        decision = (await memory.search("SQLite FTS local", kind=MemoryKind.ARCHITECTURE_DECISION))[
            0
        ]
        assert decision.entry.department == "chief_of_staff"
        assert decision.entry.source == "SPEC.md"
        event = emitter.call_args.args[0]
        assert isinstance(event, AgentThoughtEvent)
        assert event.mission_id == mission_id and event.task_id == task.id
        assert parse_event(event.model_dump_json()) == event
        router.complete.assert_awaited_once()
        call = router.complete.call_args
        assert call.args[0] == mission_id
        assert call.kwargs["json_schema"]["additionalProperties"] is False
        prompt = call.args[1][0].content
        assert all(
            name in prompt for name in ["researcher", "engineer", "qa_self_healer", "growth_career"]
        )


async def test_chief_replaces_draft_and_upserts_memory_without_duplicate_spec(settings, database):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    first = delivery_plan()
    revised = copy.deepcopy(first)
    revised["specification"] += "\n## Decision revision\nUse BM25 ranking."
    revised["tasks"][-1]["title"] = "Prepare launch guide"
    router.complete = AsyncMock(
        side_effect=[result(mission_id, first), result(mission_id, revised)]
    )
    task = planning_task(mission_id)
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        chief = ChiefOfStaff(router)
        await chief.execute_task(task, sandbox, memory, AsyncMock())
        previous = await memory.get_spec()
        output = await chief.execute_task(task, sandbox, memory, AsyncMock())
        current = await memory.get_spec()
        assert current.id == previous.id and current.created_at == previous.created_at
        assert "BM25" in await sandbox.read_file("SPEC.md")
        assert output["tasks"][-1]["title"] == "Prepare launch guide"
        assert (
            len(await memory.search("SPEC", kind=MemoryKind.ARCHITECTURE_DECISION, min_score=0))
            == 1
        )


@pytest.mark.parametrize(
    "invalid",
    [
        plan(node("a", ["b"]), node("b", ["a"])),
        plan(node("a", ["absent"])),
        plan(node("a", ["a"])),
        plan(node("a"), node("a")),
        plan(node("a", department="uninstalled_department")),
        plan(node("a", department="chief_of_staff")),
        plan(node("a"), node("b", ["a", "a"])),
        {"specification": "Spec", "tasks": []},
    ],
)
async def test_invalid_chief_plan_has_no_file_or_memory_side_effects(settings, database, invalid):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, invalid))
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        with pytest.raises(ValueError):
            await ChiefOfStaff(router).execute_task(
                planning_task(mission_id), sandbox, memory, AsyncMock()
            )
        assert not (sandbox.root / "SPEC.md").exists()
        assert not (sandbox.root / "STATE.md").exists()
        assert await memory.get_spec() is None


async def test_chief_router_failure_propagates_without_fake_plan(settings, database):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(side_effect=LLMRouterError("Provider unavailable"))
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        with pytest.raises(LLMRouterError):
            await ChiefOfStaff(router).execute_task(
                planning_task(mission_id),
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
        assert not (sandbox.root / "SPEC.md").exists()


async def test_chief_rejects_cross_mission_resources(settings, database):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    router.complete = AsyncMock()
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        with pytest.raises(ValueError, match="same mission"):
            await ChiefOfStaff(router).execute_task(
                planning_task(uuid4()),
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
    router.complete.assert_not_awaited()


async def test_planning_hook_updates_unstarted_task_nodes_and_stable_ids(settings, database):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    planning = planning_task(mission_id)
    async with database.session(write=True) as session:
        session.add(Mission(id=mission_id, title="Draft", goal="Build draft"))
        await session.flush()
        session.add(planning)
    manager = ConnectionManager(database)
    orchestrator = MissionOrchestrator(
        settings,
        database,
        manager,
        departments={
            "chief_of_staff": ChiefOfStaff(router),
            "engineering": Worker(router, ExecutionTrace()),
        },
    )
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        await orchestrator._persist_plan(
            planning, plan(node("keep"), node("remove")), sandbox, memory
        )
        replacement = plan(node("keep"), node("new", ["keep"]))
        replacement["tasks"][0]["description"] = "Updated scope"
        await orchestrator._persist_plan(planning, replacement, sandbox, memory)
        rows = await task_rows(database, mission_id)
        assert {row.title for row in rows} == {"Plan mission", "keep", "new"}
        kept = next(row for row in rows if row.title == "keep")
        assert kept.id == uuid5(mission_id, "task:keep")
        assert kept.description == "Updated scope"
        async with database.session(write=True) as session:
            row = await session.get(TaskNode, kept.id)
            row.status = TaskStatus.COMPLETED
        with pytest.raises(OrchestrationError, match="unstarted"):
            await orchestrator._persist_plan(planning, replacement, sandbox, memory)
        async with database.session() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.mission_id == mission_id))
            ).all()
        assert [artifact.file_path for artifact in artifacts] == ["SPEC.md"]
    await manager.close()


async def test_real_chief_and_researcher_run_four_phase_dag_with_durable_approval(
    settings, database, monkeypatch
):
    output = delivery_plan()
    router = LLMRouter(settings, database)

    async def completion(mission_id, messages, **kwargs):
        return result(mission_id, output)

    router.complete = AsyncMock(side_effect=completion)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<title>SQLite FTS</title><main><h1>FTS</h1>"
                "<p>SQLite FTS uses BM25 ranking.</p></main>"
            ),
        )
    )
    monkeypatch.setattr(
        "backend.app.departments.researcher.PublicWeb", lambda: PublicWeb(transport)
    )
    manager = ConnectionManager(database)
    trace = ExecutionTrace()
    departments = {
        name: Worker(router, trace, name)
        for name in ["engineer", "qa_self_healer", "growth_career"]
    }
    departments.update({"chief_of_staff": ChiefOfStaff(router), "researcher": Researcher(router)})
    orchestrator = MissionOrchestrator(settings, database, manager, departments=departments)
    await orchestrator.start()
    try:
        mission = await orchestrator.submit("Build a searchable knowledge base")
        await wait_for_state(database, mission.id, MissionStatus.AWAITING_APPROVAL)
        rows = await task_rows(database, mission.id)
        engineering = next(task for task in rows if task.department == "engineer")
        assert engineering.status == TaskStatus.AWAITING_APPROVAL
        assert not trace.timeline
        await orchestrator.decide(mission.id, engineering.id, "approved")
        await asyncio.wait_for(orchestrator.wait(mission.id), 10)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        rows = await task_rows(database, mission.id)
        assert len(rows) == 5 and all(task.status == TaskStatus.COMPLETED for task in rows)
        memory = MemoryVault(mission.id, storage_root=settings.memory_root)
        assert (await memory.search("BM25", department="researcher"))[
            0
        ].entry.source == "https://example.com/fts"
        state = (settings.mission_workspace(mission.id) / "STATE.md").read_text()
        assert "Status: completed" in state and "growth_career" in state
        async with database.session() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.mission_id == mission.id))
            ).all()
        paths = [artifact.file_path for artifact in artifacts]
        assert len(paths) == len(set(paths))
        assert {"SPEC.md", "STATE.md"} <= set(paths)
        events = [parse_event(record.payload) for record in await manager.history(mission.id, 0)]
        assert any(
            isinstance(event, AgentThoughtEvent) and event.department == "researcher"
            for event in events
        )
        router.complete.assert_awaited_once()
    finally:
        await orchestrator.close()
        await manager.close()


async def test_engineering_checkpoint_uses_dag_ancestry_not_list_order(settings, database):
    mission_id = uuid4()
    output = plan(
        node("later", ["first"], department="engineer"),
        node("first", department="engineer"),
        node("parallel", department="engineer"),
    )
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, output))
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        returned = await ChiefOfStaff(router).execute_task(
            planning_task(mission_id),
            sandbox,
            MemoryVault(mission_id, storage_root=settings.memory_root),
            AsyncMock(),
        )
    tasks = {item["key"]: item for item in returned["tasks"]}
    assert tasks["first"]["requires_approval"] and tasks["parallel"]["requires_approval"]
    assert not tasks["later"]["requires_approval"]


async def test_chief_enforces_combined_specification_size_before_writes(settings, database):
    mission_id = uuid4()
    output = plan()
    output["specification"] = "A" * 100_000
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, output))
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        with pytest.raises(ValueError):
            await ChiefOfStaff(router).execute_task(
                planning_task(mission_id),
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
        assert not (sandbox.root / "SPEC.md").exists()
