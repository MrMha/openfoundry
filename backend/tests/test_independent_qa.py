"""Independent requirement checks catch false passes from implementation-authored tests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.core.llm_router import LLMRouter, OutputValidationError
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.base_department import DepartmentExecutionError
from backend.app.departments.engineer import Engineer
from backend.app.departments.engineering_contracts import EngineeringResult
from backend.app.departments.independent_qa import acceptance_tests, public_signatures
from backend.app.departments.qa_self_healer import QASelfHealer
from backend.app.models.mission import TaskNode

CHECKS = (
    "import pytest\nfrom app import parse_csv\n\n"
    "def test_missing_header():\n    with pytest.raises(ValueError):\n        parse_csv('')\n"
)


def test_public_signatures_hide_function_bodies_and_class_state():
    source = (
        "class Service:\n    hidden = 'implementation_marker'\n"
        "    def run(self, value: str) -> str:\n        return 'implementation_marker'\n"
        "def parse_csv(text: str) -> dict:\n    return {'implementation_marker': 1}\n"
    )
    skeleton = public_signatures(source)
    assert "implementation_marker" not in skeleton
    assert "def run(self, value: str) -> str" in skeleton
    assert "def parse_csv(text: str) -> dict" in skeleton


@pytest.mark.parametrize("good", [False, True])
async def test_independent_acceptance_controls_qa_result(settings, database, good):
    configured = settings.model_copy(update={"independent_qa_enabled": True})
    mid = uuid4()
    task = TaskNode(
        id=uuid4(),
        mission_id=mid,
        department="qa_self_healer",
        title="Verify CSV",
        description="Verify requirements",
        dependencies_json="[]",
    )
    router = LLMRouter(configured, database)
    router.complete = AsyncMock(
        return_value=SimpleNamespace(
            structured_output={
                "source": CHECKS,
                "empty_or_missing_input_tests": CHECKS.replace(
                    "test_missing_header", "test_empty_input"
                ),
            }
        )
    )
    memory = MemoryVault(mid, storage_root=settings.memory_root)
    async with WorkspaceSandbox(
        mid, root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        await sandbox.write_file(
            "SPEC.md", "app.py parse_csv(text) requires a header; empty input raises ValueError."
        )
        await sandbox.write_file(
            "app.py",
            "def parse_csv(text):\n"
            + ("    if not text:\n        raise ValueError('Missing header')\n" if good else "")
            + "    return {}\n",
        )
        await sandbox.write_file(
            "test_self.py",
            "from app import parse_csv\n"
            "def test_self():\n    assert parse_csv('category,amount') == {}\n",
        )
        await sandbox.write_file(
            "openfoundry.qa.json",
            json.dumps(
                {
                    "commands": [{"command": ["pytest", "-q"]}],
                    "max_repairs": 0,
                }
            ),
        )
        qa = QASelfHealer(router)
        if good:
            output = await qa.execute_task(task, sandbox, memory, AsyncMock())
            assert output["passed"]
        else:
            with pytest.raises(DepartmentExecutionError) as failure:
                await qa.execute_task(task, sandbox, memory, AsyncMock())
            assert not failure.value.output["passed"]
            output = failure.value.output
        report = json.loads(await sandbox.read_file(f"qa/{task.id}/report.json"))
        checks = report["history"][0]["checks"]
        assert checks[0]["result"]["status"] == "passed"
        assert checks[1]["result"]["status"] == ("passed" if good else "failed")
        call = router.complete.call_args.kwargs
        assert "implementation bodies" in router.complete.call_args.args[1][0].content.lower()
        assert "return {}" not in router.complete.call_args.args[1][1].content
        assert call["output_validator"]
        assert any(
            a["artifact_type"] == "independent_acceptance_tests" for a in output["artifacts"]
        )


async def test_skipped_independent_tests_are_rejected_before_writing(settings, database):
    mid = uuid4()
    task = TaskNode(id=uuid4(), mission_id=mid, department="qa_self_healer", title="Verify")
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        return_value=SimpleNamespace(
            structured_output={
                "source": "import pytest\n@pytest.mark.skip\ndef test_skip():\n    assert True\n",
                "empty_or_missing_input_tests": "def test_empty():\n    assert True\n",
            }
        )
    )
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "Return a correct result")
        await sandbox.write_file("app.py", "def parse_csv(text):\n    return {}\n")
        with pytest.raises(OutputValidationError, match="skip"):
            await acceptance_tests(router, task, sandbox)
        assert not any("acceptance_test" in path for path in await sandbox.list_files())


async def test_draft_lint_reports_bad_tests_without_importing_them(settings, database):
    work = EngineeringResult.model_validate(
        {
            "summary": "uncommitted test draft",
            "files": [
                {
                    "file_path": "ruff.toml",
                    "content": '[lint]\nselect=["B"]\n',
                    "artifact_type": "config",
                },
                {
                    "file_path": "test_draft.py",
                    "artifact_type": "test",
                    "content": "from pathlib import Path\nimport pytest\n"
                    "Path('generated-code-executed').write_text('unsafe')\n"
                    "def test_draft():\n    with pytest.raises(Exception):\n"
                    "        raise ValueError()\n",
                },
            ],
            "verification": {},
        }
    )
    diagnostics = await Engineer(LLMRouter(settings, database))._draft_lint(work)
    assert "B017" in diagnostics
    assert not list(settings.data_dir.rglob("generated-code-executed"))
    receipts = list(settings.data_dir.rglob("static-lint-receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["return_code"] != 0
