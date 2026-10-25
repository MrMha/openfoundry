"""Contract review can correct draft tests, while preserving the rejected proposal."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.core.llm_router import LLMRouter, OutputValidationError
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.independent_qa import acceptance_tests
from backend.app.departments.test_contract_review import review_test_contracts
from backend.app.models.mission import TaskNode

SPEC = "parse_csv requires a header. Entirely empty input raises ValueError."
EMPTY = (
    "import pytest\nfrom app import parse_csv\ndef test_empty():\n"
    "    with pytest.raises(ValueError):\n        parse_csv('')\n"
)


async def test_wrong_draft_contract_is_corrected_before_freezing_and_both_reviews_are_saved(
    settings, database
):
    mid = uuid4()
    task = TaskNode(id=uuid4(), mission_id=mid, department="qa_self_healer", title="Verify")
    bad = "from app import parse_csv\ndef test_wrong_draft():\n    assert parse_csv('') == {}\n"
    corrected = EMPTY.replace("test_empty", "test_corrected_draft")
    finding = {
        "file_path": "acceptance_test.py",
        "line": 3,
        "requirement_quote": "Entirely empty input raises ValueError.",
        "issue": "An empty document cannot satisfy the required header.",
        "suggested_correction": "Assert the specified ValueError for empty input.",
    }
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        side_effect=[
            SimpleNamespace(
                structured_output={"source": bad, "empty_or_missing_input_tests": EMPTY}
            ),
            SimpleNamespace(structured_output={"findings": [finding]}),
            SimpleNamespace(
                structured_output={"source": corrected, "empty_or_missing_input_tests": EMPTY}
            ),
            SimpleNamespace(structured_output={"findings": []}),
        ]
    )
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", SPEC)
        await sandbox.write_file(
            "app.py",
            "def parse_csv(text):\n    if not text:\n"
            "        raise ValueError('header')\n    return {}\n",
        )
        path = await acceptance_tests(router, task, sandbox)
        assert "assert parse_csv('') == {}" not in await sandbox.read_file(path)
        first = json.loads(await sandbox.read_file(f"qa/{task.id}/test-contract-review-0.json"))
        second = json.loads(await sandbox.read_file(f"qa/{task.id}/test-contract-review-1.json"))
        assert first["findings"] == [finding] and bad.strip() in first["draft_source"]
        assert second["findings"] == []
        actual = await sandbox.run_test_command(["pytest", "-q", path], independent_pytest=True)
        assert actual.succeeded, actual.stdout + actual.stderr
        assert router.complete.await_count == 4


async def test_contract_review_cannot_introduce_a_requirement_absent_from_spec(settings, database):
    mid = uuid4()
    task = TaskNode(id=uuid4(), mission_id=mid, department="qa_self_healer", title="Verify")
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        return_value=SimpleNamespace(
            structured_output={
                "findings": [
                    {
                        "file_path": "test_app.py",
                        "line": 1,
                        "requirement_quote": "Invented requirement about an external database",
                        "issue": "Invented issue",
                        "suggested_correction": "Invented correction",
                    }
                ]
            }
        )
    )
    with pytest.raises(OutputValidationError, match="authoritative requirement"):
        await review_test_contracts(router, task, SPEC, {"test_app.py": EMPTY})
