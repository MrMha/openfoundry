"""Independent checks must execute even when project pytest config disables its tests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.core.llm_router import LLMRouter
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.base_department import DepartmentExecutionError
from backend.app.departments.qa_self_healer import QASelfHealer
from backend.app.models.mission import TaskNode


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("pytest.ini", "[pytest]\naddopts = --collect-only\n"),
        (
            "conftest.py",
            "import pytest\n@pytest.fixture(autouse=True)\n"
            "def skip_everything():\n    pytest.skip('not executed')\n",
        ),
    ],
    ids=["project-collect-only", "project-skips-everything"],
)
async def test_project_configuration_cannot_turn_wrong_delivery_into_qa_pass(
    settings, database, path, content
):
    configured = settings.model_copy(update={"independent_qa_enabled": True})
    mid = uuid4()
    task = TaskNode(
        id=uuid4(),
        mission_id=mid,
        department="qa_self_healer",
        title="Verify CSV",
        description="Verify the delivery against SPEC.md",
    )
    source = (
        "import pytest\nfrom app import parse_csv\n"
        "def test_empty():\n    with pytest.raises(ValueError):\n        parse_csv('')\n"
    )
    router = LLMRouter(configured, database)

    def completion(*args, **kwargs):
        if "findings" in kwargs["json_schema"]["properties"]:
            return SimpleNamespace(structured_output={"findings": []})
        return SimpleNamespace(
            structured_output={
                "source": source.replace("test_empty", "test_missing_header"),
                "empty_or_missing_input_tests": source,
            }
        )

    router.complete = AsyncMock(side_effect=completion)
    memory = MemoryVault(mid, storage_root=settings.memory_root)
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "parse_csv requires a header; empty raises ValueError.")
        await sandbox.write_file("app.py", "def parse_csv(text):\n    return {}\n")
        await sandbox.write_file("test_self.py", "def test_self():\n    assert True\n")
        await sandbox.write_file(path, content)
        await sandbox.write_file(
            "openfoundry.qa.json",
            json.dumps(
                {
                    "commands": [{"command": ["pytest", "-q"]}],
                    "max_repairs": 0,
                }
            ),
        )
        with pytest.raises(DepartmentExecutionError) as failure:
            await QASelfHealer(router).execute_task(task, sandbox, memory, AsyncMock())
        assert failure.value.output["passed"] is False
        report = json.loads(await sandbox.read_file(f"qa/{task.id}/report.json"))
        assert report["history"][0]["checks"][1]["result"]["status"] == "failed"


@pytest.mark.parametrize(
    ("source", "passed"),
    [
        ("def test_actual():\n    assert 2 + 3 == 5\n", True),
        ("def test_wrong():\n    assert 2 + 3 == 6\n", False),
        (
            "import pytest\n@pytest.mark.skip(reason='not executed')\n"
            "def test_skipped():\n    assert False\n",
            False,
        ),
        (
            "import pytest\ndef test_actual():\n    assert 2 + 3 == 5\n"
            "def test_unavailable():\n    pytest.skip('missing behavior')\n",
            False,
        ),
        (
            "import pytest\n@pytest.mark.xfail\ndef test_expected_failure():\n    assert False\n",
            False,
        ),
        ("value = 42\n", False),
    ],
    ids=["executed-pass", "executed-failure", "all-skipped", "partly-skipped", "xfail", "no-tests"],
)
async def test_independent_execution_requires_real_unskipped_passes(settings, source, passed):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        await sandbox.write_file("test_checks.py", source)
        result = await sandbox.run_test_command(
            ["pytest", "-q", "test_checks.py"], independent_pytest=True
        )
        assert result.succeeded is passed, result.stdout + result.stderr
        assert "Independent pytest execution counts:" in result.stdout
