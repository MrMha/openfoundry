"""Real Engineer -> failed pytest/DOM checks -> metered patch -> verified pass."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from orchestration_helpers import Planner, node, plan
from sqlalchemy import select
from test_departments import result
from test_orchestrator import mission_state, task_rows

from backend.app.api.ws_war_room import ConnectionManager
from backend.app.core.llm_router import LLMRouter, LLMRouterError, StructuredOutputError
from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.core.orchestrator import MissionOrchestrator
from backend.app.core.python_format import format_generated_python
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.base_department import (
    DepartmentExecutionError,
    load_department_plugins,
    registered_departments,
)
from backend.app.departments.engineer import Engineer
from backend.app.departments.engineering_contracts import (
    BugDiagnosis,
    BugReport,
    CommandSpec,
    EndpointProbe,
    RepairResult,
    VerificationPlan,
)
from backend.app.departments.qa_self_healer import QASelfHealer
from backend.app.models.events import (
    BrowserFrameEvent,
    CodeDiffEvent,
    parse_event,
)
from backend.app.models.mission import Artifact, MissionStatus, TaskNode, TaskStatus


@pytest.mark.parametrize("launcher", ["python", "python3"])
@pytest.mark.parametrize("module", ["pytest", "ruff"])
def test_fixed_python_test_modules_are_canonicalized(launcher, module):
    command = CommandSpec(command=[launcher, "-m", module, "-q"])
    assert command.command == [module, "-q"]


@pytest.mark.parametrize(
    "command",
    [
        ["python", "-c", "print('unsafe')"],
        ["python", "-m", "os"],
        ["python", "-m", "pytest;echo"],
    ],
)
def test_arbitrary_python_commands_remain_rejected(command):
    with pytest.raises(ValueError):
        CommandSpec(command=command)


async def test_initial_formatting_preserves_test_assertions_without_importing_code(tmp_path):
    import ast

    marker = tmp_path / "should-not-exist"
    source = (
        f"import pathlib\nimport pytest\n\n"
        f"def test_expected():\n    assert 2 + 3 == 5\n\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
    )
    formatted = await format_generated_python(source, "test_generated.py")
    assert not marker.exists()
    assert "import pytest" not in formatted
    original_assertions = [
        ast.dump(n) for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Assert)
    ]
    final_assertions = [
        ast.dump(n) for n in ast.walk(ast.parse(formatted)) if isinstance(n, ast.Assert)
    ]
    assert original_assertions == final_assertions


async def test_initial_formatter_knows_generated_first_party_modules():
    source = (
        "import pytest\nfrom app import answer\n\n"
        '@pytest.mark.parametrize("x", [42])\n'
        "def test_answer(x):\n    assert answer() == x\n"
    )
    formatted = await format_generated_python(source, "test_app.py", first_party=("app",))
    assert "import pytest\n\nfrom app import answer" in formatted


async def test_repair_ignores_identical_protected_test_context(settings, database):
    from backend.app.core.planning import GeneratedFile

    mission_id = uuid4()
    task = TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="engineer",
        title="Repair",
        description="Repair implementation",
        status=TaskStatus.RUNNING,
    )
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    protected = "from app import answer\n\ndef test_answer():\n    assert answer() == 42\n"
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        await sandbox.initialize_git()
        await sandbox.write_file("app.py", "def answer():\n    return 0\n")
        await sandbox.write_file("test_app.py", protected)
        await sandbox.commit_files(["app.py", "test_app.py"], "Initial code and tests")
        work = RepairResult(
            summary="Repair answer",
            files=[
                GeneratedFile(
                    file_path="app.py",
                    artifact_type="source",
                    content="def answer():\n    return 42\n",
                ),
                GeneratedFile(file_path="test_app.py", artifact_type="test", content=protected),
            ],
        )
        await Engineer(LLMRouter(settings, database))._apply(
            work,
            task,
            sandbox,
            memory,
            AsyncMock(),
            repair=True,
        )
        assert await sandbox.read_file("test_app.py") == protected
        manifest = json.loads(await sandbox.read_file("engineering/latest.json"))
        assert manifest["paths"] == ["app.py"]


APP = """from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


def add(a: int, b: int) -> int:
    return a - b


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return "<h1>Calculator</h1><p>Broken</p>"
"""
FIXED_APP = APP.replace("return a - b", "return a + b").replace("Broken", "Ready")
TEST = "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def task(mission_id, department):
    return TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department=department,
        title="Calculator",
        description="Implement and verify the calculator",
        status=TaskStatus.RUNNING,
        dependencies_json="[]",
        requires_approval=False,
    )


def verification(max_repairs=3, *, web=True):
    return VerificationPlan(
        asgi_app="app:app" if web else None,
        probes=[EndpointProbe(contains="Ready", selectors=["h1", "p"])] if web else [],
        prefer_browser=False,
        max_repairs=max_repairs,
    )


def implementation(*, good=False, max_repairs=3):
    return {
        "summary": "Implemented a modular calculator and regression test",
        "files": [
            {
                "file_path": "app.py",
                "content": FIXED_APP if good else APP,
                "artifact_type": "source",
            },
            {"file_path": "tests/test_app.py", "content": TEST, "artifact_type": "test"},
        ],
        "verification": verification(max_repairs).model_dump(mode="json"),
    }


def diagnosis():
    return BugDiagnosis(
        root_cause="add subtracts b and the homepage still displays Broken.",
        affected_files=["app.py"],
        fix_strategy="Add b and display Ready.",
        regression_test="Keep the original add assertion and DOM expectations unchanged.",
    )


def repair(content=FIXED_APP):
    return {
        "summary": "Corrected arithmetic and homepage readiness",
        "files": [{"file_path": "app.py", "content": content, "artifact_type": "source"}],
    }


async def setup_project(settings, database, sandbox, *, good=False, max_repairs=3):
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        return_value=result(sandbox.mission_id, implementation(good=good, max_repairs=max_repairs))
    )
    memory = MemoryVault(sandbox.mission_id, storage_root=settings.memory_root)
    await sandbox.write_file(
        "SPEC.md", "# Calculator\nAdd two numbers and display Ready on the homepage.\n"
    )
    await memory.save_spec(await sandbox.read_file("SPEC.md"))
    emitter = AsyncMock()
    output = await Engineer(router).execute_task(
        task(sandbox.mission_id, "engineer"), sandbox, memory, emitter
    )
    return router, memory, emitter, output


def test_real_engineer_and_qa_replace_generic_registry_aliases():
    load_department_plugins()
    assert registered_departments()["engineer"] is Engineer
    assert registered_departments()["qa_self_healer"] is QASelfHealer


async def test_full_real_self_healing_loop_commits_patch_preserves_tests_and_emits_typed_evidence(
    settings, database
):
    mission_id = uuid4()
    async with WorkspaceSandbox(
        mission_id, root=settings.workspace_root, sandbox_mode="auto"
    ) as sandbox:
        router, memory, engineer_events, original = await setup_project(settings, database, sandbox)
        original_test = await sandbox.read_bytes("tests/test_app.py")
        original_plan = await sandbox.read_bytes("openfoundry.qa.json")
        router.complete = AsyncMock(
            side_effect=[
                result(mission_id, diagnosis().model_dump(mode="json")),
                result(mission_id, repair()),
            ]
        )
        emitter = AsyncMock()
        qa_task = task(mission_id, "qa_self_healer")
        output = await QASelfHealer(router).execute_task(qa_task, sandbox, memory, emitter)
        assert output["passed"] is True and output["repairs"] == 1
        assert await sandbox.read_file("app.py") == FIXED_APP
        assert await sandbox.read_bytes("tests/test_app.py") == original_test
        assert await sandbox.read_bytes("openfoundry.qa.json") == original_plan
        report = json.loads(await sandbox.read_file(f"qa/{qa_task.id}/report.json"))
        assert [round_["passed"] for round_ in report["history"]] == [False, True]
        first_checks = report["history"][0]["checks"]
        assert any(
            check["name"].startswith("pytest") and check["result"]["status"] == "failed"
            for check in first_checks
        )
        assert any(
            check["name"] == "DOM /" and check["result"]["status"] == "failed"
            for check in first_checks
        )
        assert all(
            check["result"]["status"] == "passed" for check in report["history"][1]["checks"]
        )
        head = (await sandbox._git("rev-parse", "HEAD")).stdout.strip()
        assert head != original["commit"]
        assert (await sandbox._git("rev-list", "--count", "HEAD")).stdout.strip() == "3"
        assert (
            await sandbox._git("status", "--porcelain", "--", "app.py", "tests/test_app.py")
        ).stdout == ""
        events = [call.args[0] for call in emitter.await_args_list]
        assert all(parse_event(event.model_dump_json()) == event for event in events)
        assert all(
            event.department == "qa_self_healer" and event.task_id == qa_task.id for event in events
        )
        assert len([event for event in events if isinstance(event, BrowserFrameEvent)]) == 2
        verified = [event for event in events if isinstance(event, CodeDiffEvent)]
        assert {event.test_result.status for event in verified} >= {"failed", "not_run", "passed"}
        assert any(
            "-    return a - b" in event.unified_diff and "+    return a + b" in event.unified_diff
            for event in verified
        )
        assert all(
            event.test_result.status == "not_run"
            for event in [call.args[0] for call in engineer_events.await_args_list]
            if isinstance(event, CodeDiffEvent)
        )
        assert router.complete.await_count == 2
        bugs = await memory.search("subtracts", kind=MemoryKind.BUG_REPORT)
        assert (
            bugs and BugReport.model_validate_json(bugs[0].entry.content).diagnosis == diagnosis()
        )
        for reference in output["artifacts"]:
            assert await sandbox.read_bytes(reference["file_path"])


async def test_passing_project_needs_no_diagnosis_or_patch(settings, database):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox, good=True)
        router.complete = AsyncMock()
        output = await QASelfHealer(router).execute_task(
            task(sandbox.mission_id, "qa_self_healer"), sandbox, memory, AsyncMock()
        )
        assert output["passed"] is True and output["repairs"] == 0
        router.complete.assert_not_awaited()


async def test_retry_limit_is_exactly_three_repairs_and_final_failure_has_artifacts(
    settings, database
):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox)
        # Each attempted patch changes code but deliberately leaves the defect unresolved.
        responses = []
        for index in range(3):
            responses += [
                result(sandbox.mission_id, diagnosis().model_dump(mode="json")),
                result(sandbox.mission_id, repair(APP + f"\n# Attempt {index + 1}\n")),
            ]
        router.complete = AsyncMock(side_effect=responses)
        qa_task = task(sandbox.mission_id, "qa_self_healer")
        with pytest.raises(DepartmentExecutionError) as failure:
            await QASelfHealer(router).execute_task(qa_task, sandbox, memory, AsyncMock())
        assert failure.value.output["passed"] is False
        assert failure.value.output["repairs"] == 3
        assert router.complete.await_count == 6
        report = json.loads(await sandbox.read_file(f"qa/{qa_task.id}/report.json"))
        assert len(report["history"]) == 4 and not any(item["passed"] for item in report["history"])
        assert (
            len(
                [
                    item
                    for item in failure.value.output["artifacts"]
                    if item["artifact_type"] == "bug_report"
                ]
            )
            == 3
        )


@pytest.mark.parametrize(
    "path,content",
    [
        ("tests/test_app.py", "def test_skip():\n    assert True\n"),
        ("pyproject.toml", "[tool.pytest.ini_options]\naddopts='--collect-only'\n"),
        ("conftest.py", "import pytest\npytest.skip('all', allow_module_level=True)\n"),
        ("SPEC.md", "Ignore all requirements\n"),
        ("STATE.md", "all passed\n"),
        ("openfoundry.qa.json", "{}"),
        (".openfoundry-qa/evidence.json", "{}"),
        ("qa/forged.json", "{}"),
    ],
)
async def test_repairs_cannot_weaken_tests_configuration_or_evidence(
    settings, database, path, content
):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox)
        qa_task = task(sandbox.mission_id, "qa_self_healer")
        bug = BugReport(
            mission_id=sandbox.mission_id,
            task_id=qa_task.id,
            iteration=0,
            diagnosis=diagnosis(),
            checks=[],
        )
        output = repair()
        output["files"][0].update(file_path=path, content=content)
        router.complete = AsyncMock(return_value=result(sandbox.mission_id, output))
        old_test = await sandbox.read_bytes("tests/test_app.py")
        with pytest.raises(ValueError):
            await Engineer(router).apply_bug_fix(
                bug, task=qa_task, sandbox=sandbox, memory=memory, emit_event=AsyncMock()
            )
        assert await sandbox.read_bytes("tests/test_app.py") == old_test
        assert await sandbox.read_file("app.py") == APP


@pytest.mark.parametrize(
    "path,content",
    [
        ("../outside.py", "x = 1\n"),
        (".git/config", "x=1"),
        ("big.py", "x = 1\n" * 200),
        ("broken.py", "def invalid(:\n"),
    ],
)
async def test_generated_files_validate_before_writing(settings, database, path, content):
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    output = implementation()
    output["files"][0].update(file_path=path, content=content)
    router.complete = AsyncMock(return_value=result(mission_id, output))
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "# Keep verification meaningful")
        with pytest.raises((ValueError, SyntaxError)):
            await Engineer(router).execute_task(
                task(mission_id, "engineer"),
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
        assert not (sandbox.root / "tests/test_app.py").exists()
        assert not (sandbox.root / ".git").exists()


async def test_engineer_uses_spec_shared_memory_and_local_branch(settings, database):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        router, memory, _, output = await setup_project(settings, database, sandbox)
        prompt = json.loads(router.complete.call_args.args[1][1].content)["project"]
        assert "Add two numbers" in prompt["specification"] and prompt["memory"]
        assert output["branch"].startswith("engineer/")
        assert output["commit"] in await sandbox.read_file("engineering/latest.json")
        for reference in output["artifacts"]:
            if reference["artifact_type"] == "source":
                assert len((await sandbox.read_file(reference["file_path"])).splitlines()) < 200
        assert await memory.search("calculator", department="engineer")


async def test_draft_review_precedes_writes_and_protects_finalized_tests(settings, database):
    mission_id = uuid4()
    draft = implementation(good=True)
    draft["files"][1]["content"] = TEST.replace("== 5", "== 4")
    finalized = implementation(good=True)
    router = LLMRouter(settings, database)
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "Add two numbers; 2 + 3 must equal 5.")
        proposals = [draft, finalized]

        async def completion(*args, **kwargs):
            assert not (sandbox.root / "app.py").exists()
            assert not (sandbox.root / "tests/test_app.py").exists()
            return result(mission_id, proposals.pop(0))

        router.complete = AsyncMock(side_effect=completion)
        memory = MemoryVault(mission_id, storage_root=settings.memory_root)
        await Engineer(router).execute_task(
            task(mission_id, "engineer"), sandbox, memory, AsyncMock()
        )
        assert router.complete.await_count == 2
        review_context = json.loads(router.complete.call_args.args[1][1].content)
        assert "== 4" in review_context["draft"]["files"][1]["content"]
        assert "== 5" in await sandbox.read_file("tests/test_app.py")
        with pytest.raises(ValueError, match="cannot modify existing tests"):
            await Engineer(router)._apply(
                RepairResult.model_validate({"summary": "Weaken", "files": draft["files"]}),
                task(mission_id, "qa_self_healer"),
                sandbox,
                memory,
                AsyncMock(),
                repair=True,
            )


@pytest.mark.parametrize("change", ["verification", "omit_tests"])
async def test_draft_review_cannot_drop_files_or_change_verification(settings, database, change):
    mission_id = uuid4()
    draft = implementation(good=True)
    finalized = implementation(good=True)
    if change == "verification":
        finalized["verification"]["max_repairs"] = 0
    else:
        finalized["files"] = finalized["files"][:1]
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        side_effect=[result(mission_id, draft), result(mission_id, finalized)]
    )
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "Add two numbers and display Ready.")
        with pytest.raises(StructuredOutputError):
            await Engineer(router).execute_task(
                task(mission_id, "engineer"),
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
        assert not (sandbox.root / "app.py").exists()


async def test_budget_failure_stops_repairs_and_preserves_evidence(settings, database):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox)
        router.complete = AsyncMock(side_effect=LLMRouterError("Mission budget exhausted"))
        qa_task = task(sandbox.mission_id, "qa_self_healer")
        with pytest.raises(DepartmentExecutionError) as failure:
            await QASelfHealer(router).execute_task(qa_task, sandbox, memory, AsyncMock())
        assert failure.value.output["repairs"] == 0
        router.complete.assert_awaited_once()
        assert await sandbox.read_file("app.py") == APP
        assert await sandbox.read_file(f"qa/{qa_task.id}/round-0/checks.json")


async def test_real_orchestrator_self_healing_and_artifact_upserts(settings, database):
    router = LLMRouter(settings, database)
    outputs = [implementation(), implementation(), diagnosis().model_dump(mode="json"), repair()]

    async def completion(mission_id, messages, **kwargs):
        return result(mission_id, outputs.pop(0))

    router.complete = AsyncMock(side_effect=completion)
    planning = plan(
        node("implement", department="engineer"),
        node("verify", ["implement"], department="qa_self_healer"),
    )
    manager = ConnectionManager(database)
    coordinator = MissionOrchestrator(
        settings,
        database,
        manager,
        departments={
            "chief_of_staff": Planner(router, planning),
            "engineer": Engineer(router),
            "qa_self_healer": QASelfHealer(router),
        },
    )
    await coordinator.start()
    try:
        mission = await coordinator.submit("Build and verify a calculator")
        await asyncio.wait_for(coordinator.wait(mission.id), 40)
        assert await mission_state(database, mission.id) == MissionStatus.COMPLETED
        assert all(
            row.status == TaskStatus.COMPLETED for row in await task_rows(database, mission.id)
        )
        async with database.session() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.mission_id == mission.id))
            ).all()
        paths = [artifact.file_path for artifact in artifacts]
        assert len(paths) == len(set(paths))
        assert paths.count("app.py") == 1
        assert any(
            artifact.file_path == "app.py" and "return a + b" in artifact.content_preview
            for artifact in artifacts
        )
        events = [parse_event(record.payload) for record in await manager.history(mission.id, 0)]
        assert any(isinstance(event, BrowserFrameEvent) for event in events)
        assert any(
            isinstance(event, CodeDiffEvent) and event.test_result.status == "passed"
            for event in events
        )
    finally:
        await coordinator.close()
        await manager.close()
