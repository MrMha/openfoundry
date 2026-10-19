import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from orchestration_helpers import node, plan

from backend.app.config import Settings
from backend.app.core.llm_router import LLMResult, LLMRouter, TokenUsage
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.database import Database
from backend.app.departments.base_department import (
    BaseDepartment,
    load_department_plugins,
    register_department,
    registered_departments,
)
from backend.app.departments.builtin import EngineeringDepartment
from backend.app.departments.chief_of_staff import ChiefOfStaff
from backend.app.models.mission import TaskNode, TaskStatus


def result(mission_id, output):
    return LLMResult(
        mission_id,
        uuid4(),
        "openai",
        "openai/gpt-4o-mini",
        json.dumps(output),
        TokenUsage(100, 100),
        Decimal("0.01"),
        output,
    )


def test_plugin_registration_and_duplicate_names() -> None:
    plugin_name = "test_plugin_" + uuid4().hex

    class Plugin(BaseDepartment):
        name = plugin_name
        role_description = "Test plugin registration contract"

        async def execute_task(self, task, sandbox, memory, emit_event):
            return {"summary": "A valid plugin output"}

    assert register_department(Plugin) is Plugin
    assert register_department(Plugin) is Plugin
    assert registered_departments()[plugin_name] is Plugin

    class Collision(Plugin):
        name = plugin_name

    with pytest.raises(ValueError, match="already registered"):
        register_department(Collision)


def test_invalid_and_abstract_plugins_fail_registration() -> None:
    with pytest.raises(TypeError):
        register_department(BaseDepartment)

    class Invalid(BaseDepartment):
        name = "Invalid name"
        role_description = "Invalid name contract"

        async def execute_task(self, task, sandbox, memory, emit_event):
            return {}

    with pytest.raises(ValueError):
        register_department(Invalid)
    load_department_plugins()
    assert {
        "chief_of_staff",
        "research",
        "engineering",
        "qa",
        "growth",
    } <= registered_departments().keys()


async def test_chief_of_staff_uses_metered_structured_router(
    settings: Settings, database: Database
) -> None:
    mission_id = uuid4()
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, plan(node("implementation"))))
    task = TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="chief_of_staff",
        title="Plan mission",
        description="Build a notes app",
        status=TaskStatus.RUNNING,
    )
    emitter = AsyncMock()
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        output = await ChiefOfStaff(router).execute_task(
            task, sandbox, MemoryVault(mission_id, storage_root=settings.memory_root), emitter
        )
    assert output["tasks"][0]["key"] == "implementation"
    assert emitter.await_count == 1
    kwargs = router.complete.call_args.kwargs
    assert kwargs["json_schema"]["additionalProperties"] is False
    assert set(kwargs["json_schema"]["required"]) == {"specification", "tasks"}


async def test_engineering_plugin_writes_complete_files_and_shared_memory(
    settings: Settings, database: Database
) -> None:
    mission_id = uuid4()
    output = {
        "summary": "Implemented add",
        "files": [
            {
                "file_path": "src/calculator.py",
                "content": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "artifact_type": "source",
            }
        ],
    }
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, output))
    task = TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="engineering",
        title="Implement calculator",
        description="Implement typed integer addition",
        status=TaskStatus.RUNNING,
    )
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        emitted = AsyncMock()
        answer = await EngineeringDepartment(router).execute_task(task, sandbox, memory, emitted)
        assert "return a + b" in await sandbox.read_file("src/calculator.py")
        assert answer["artifacts"][0]["file_path"] == "src/calculator.py"
    assert (await memory.search("implemented add"))[0].entry.department == "engineering"


async def test_generated_artifact_traversal_fails_before_writing(
    settings: Settings, database: Database
) -> None:
    mission_id = uuid4()
    output = {
        "summary": "Bad path",
        "files": [{"file_path": "../escape.py", "content": "bad", "artifact_type": "source"}],
    }
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, output))
    task = TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="engineering",
        title="Bad path",
        description="Path validation",
        status=TaskStatus.RUNNING,
    )
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
        with pytest.raises(ValueError):
            await EngineeringDepartment(router).execute_task(
                task,
                sandbox,
                MemoryVault(mission_id, storage_root=settings.memory_root),
                AsyncMock(),
            )
    assert not (settings.workspace_root / "escape.py").exists()
