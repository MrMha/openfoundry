"""Check external grading isolation and failure receipts independently of live APIs."""

import ast
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.core.llm_router import LLMRouter
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.base_department import BaseDepartment
from backend.app.main import create_app
from scripts.evaluation_support import checkpoint, independently_check, production_source
from scripts.run_live_evaluation import CASES, run_case


@pytest.mark.parametrize("name", list(CASES))
def test_every_fixed_case_has_valid_test_code(name):
    ast.parse(CASES[name]["checks"])
    assert CASES[name]["requirements"].strip()


@pytest.mark.parametrize(
    "path",
    [
        "tests/helper.py",
        "test_bad.py",
        "conftest.py",
        "src/conftest.py",
        "sitecustomize.py",
        "qa/app.py",
        ".venv/lib.py",
        "__pycache__/app.py",
    ],
)
def test_grader_excludes_project_tests_and_configuration(path):
    assert not production_source(path)


async def test_multifile_grading_ignores_generated_pytest_hooks(settings):
    mid = uuid4()
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("package/__init__.py", "")
        await sandbox.write_file("package/core.py", "def answer():\n    return 42\n")
        await sandbox.write_file("app.py", "from package.core import answer\n")
        await sandbox.write_file("conftest.py", "raise RuntimeError('contaminated grader')\n")
        await sandbox.write_file("pytest.ini", "[pytest]\naddopts = --collect-only\n")
        await sandbox.write_file("test_fake.py", "raise RuntimeError('generated test ran')\n")
    result = await independently_check(
        settings, mid, "from app import answer\ndef test_answer():\n    assert answer() == 42\n"
    )
    assert result["status"] == "passed", result
    assert set(result["source_sha256"]) == {"app.py", "package/__init__.py", "package/core.py"}
    failed = await independently_check(
        settings, mid, "from app import answer\ndef test_answer():\n    assert answer() == 43\n"
    )
    assert failed["status"] == "failed"
    assert failed["return_code"] != 0


def test_atomic_receipt_redacts_secrets_and_keeps_unicode(tmp_path):
    path = tmp_path / "report.json"
    checkpoint(path, {"previous": "complete"}, secret="test-secret")
    checkpoint(path, {"text": "Café 中文", "credential": "test-secret"}, secret="test-secret")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "text": "Café 中文",
        "credential": "[REDACTED]",
    }
    assert not path.with_suffix(".tmp").exists()


async def test_no_python_delivery_cannot_pass_grading(settings):
    mid = uuid4()
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("README.md", "No implementation")
    assert (await independently_check(settings, mid, "assert True"))["status"] == "not_run"


def test_mission_deadline_stops_dispatch_and_preserves_failure_receipt(settings, database):
    class WaitingChief(BaseDepartment):
        name = "chief_of_staff"
        role_description = "Wait for cancellation without any provider request"

        async def execute_task(self, task, sandbox, memory, emit_event):
            await sandbox.write_file("partial.txt", "started before deadline")
            await asyncio.Event().wait()

    configured = settings.model_copy(update={"gemini_api_key": SecretStr("test-only-key")})
    chief = WaitingChief(LLMRouter(configured, database))
    received = []
    with TestClient(create_app(configured, departments={"chief_of_staff": chief})) as client:
        result = run_case(client, configured, "slugify", 2, received.append)
        assert result["deadline_exceeded"]
        assert not result["end_to_end_pass"]
        assert result["mission_id"] == received[0]
        assert all(task["status"] != "running" for task in result["mission"]["tasks"])
        assert result["independent_acceptance"]["status"] == "not_run"
        assert result["attempts"] == []
