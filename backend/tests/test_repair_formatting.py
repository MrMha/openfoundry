"""A real live failure: a new repair test's lint error became immutable."""

import ast
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from backend.app.core.llm_router import LLMRouter
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.engineer import Engineer
from backend.app.departments.engineering_contracts import BugDiagnosis, BugReport
from backend.app.models.mission import TaskNode


async def test_new_repair_test_is_formatted_before_it_becomes_protected(settings, database):
    mid = uuid4()
    task = TaskNode(
        id=uuid4(),
        mission_id=mid,
        department="qa_self_healer",
        title="Repair answer",
        description="Repair the diagnosed implementation while preserving acceptance tests",
    )
    draft_test = (
        "import pytest\nfrom app import answer\n\n"
        '@pytest.mark.parametrize("expected", [42])\n'
        "def test_regression(expected):\n    assert answer() == expected\n"
    )
    frozen_test = "from app import answer\n\n\ndef test_original():\n    assert answer() == 42\n"
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        return_value=SimpleNamespace(
            structured_output={
                "summary": "Repair the wrong answer and add regression coverage",
                "files": [
                    {
                        "file_path": "app.py",
                        "content": "def answer():\n    return 42\n",
                        "artifact_type": "source",
                    },
                    {
                        "file_path": "test_regression.py",
                        "content": draft_test,
                        "artifact_type": "test",
                    },
                ],
            }
        )
    )
    report = BugReport(
        mission_id=mid,
        task_id=task.id,
        iteration=0,
        checks=[],
        diagnosis=BugDiagnosis(
            root_cause="answer returns zero",
            affected_files=["app.py"],
            fix_strategy="Return the required answer",
            regression_test="Assert answer is 42",
        ),
    )
    memory = MemoryVault(mid, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.initialize_git()
        await sandbox.write_file("SPEC.md", "answer() returns 42.")
        await sandbox.write_file("app.py", "def answer():\n    return 0\n")
        await sandbox.write_file("test_original.py", frozen_test)
        await sandbox.write_file("ruff.toml", '[lint]\nselect = ["I"]\n')
        await sandbox.commit_files(
            ["SPEC.md", "app.py", "test_original.py", "ruff.toml"], "Baseline"
        )
        await Engineer(router).apply_bug_fix(
            report, task=task, sandbox=sandbox, memory=memory, emit_event=AsyncMock()
        )
        assert await sandbox.read_file("test_original.py") == frozen_test
        formatted = await sandbox.read_file("test_regression.py")
        original_asserts = [
            ast.dump(n) for n in ast.walk(ast.parse(draft_test)) if isinstance(n, ast.Assert)
        ]
        actual_asserts = [
            ast.dump(n) for n in ast.walk(ast.parse(formatted)) if isinstance(n, ast.Assert)
        ]
        assert actual_asserts == original_asserts
        lint = await sandbox.run_test_command(["ruff", "check", "."])
        assert lint.succeeded, lint.stdout + lint.stderr
        tests = await sandbox.run_test_command(["pytest", "-q"])
        assert tests.succeeded, tests.stdout + tests.stderr
