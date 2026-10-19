from uuid import uuid4

from backend.app.config import Settings
from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.database import Database
from backend.app.models.events import (
    CodeDiffEvent,
    Department,
    parse_event,
)
from backend.app.models.events import (
    TestResult as WarRoomTestResult,
)
from backend.app.models.mission import Mission


async def test_persisted_mission_workspace_memory_and_wire_event(
    settings: Settings, database: Database
) -> None:
    async with database.session() as session:
        mission = Mission(title="Notes app", goal="Build an accessible notes application")
        session.add(mission)
        await session.flush()
        mission_id = mission.id
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    specification = await memory.save_spec("# SPEC\nKeyboard navigation is required.")
    async with WorkspaceSandbox(
        mission_id, root=settings.workspace_root, allow_host_execution=True
    ) as workspace:
        await workspace.write_file("SPEC.md", specification.content)
        await workspace.initialize_git()
        await workspace.create_branch("engineering/accessibility")
        await workspace.write_file(
            "test_keyboard.py",
            "def test_keyboard_navigation():\n    assert 'Tab'.lower() == 'tab'\n",
        )
        result = await workspace.run_test_command(["pytest", "-q", "test_keyboard.py"])
        assert result.succeeded
        assert "1 passed" in result.stdout
        diff = await workspace.git_diff()
    await memory.remember(
        MemoryKind.RESEARCH_FINDING,
        "Keyboard accessibility",
        "The notes app supports keyboard navigation.",
        department=Department.RESEARCH,
    )
    reopened = MemoryVault(mission_id, storage_root=settings.memory_root)
    assert (await reopened.search("keyboard navigation"))[0].entry.mission_id == mission_id
    event = CodeDiffEvent(
        mission_id=mission_id,
        task_id=uuid4(),
        file_path="test_keyboard.py",
        unified_diff=diff,
        test_result=WarRoomTestResult(
            status="passed",
            command=result.command,
            return_code=result.return_code,
            duration_seconds=result.duration_seconds,
            output_summary=result.stdout,
        ),
    )
    decoded = parse_event(event.model_dump_json())
    assert isinstance(decoded, CodeDiffEvent)
    assert decoded.mission_id == mission_id
    assert decoded.test_result.status == "passed"
    assert "+def test_keyboard_navigation()" in decoded.unified_diff
