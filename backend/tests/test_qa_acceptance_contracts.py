"""Reject observable ways generated QA can pass without testing the delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.core.llm_router import LLMRouter, OutputValidationError
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.independent_qa import acceptance_tests
from backend.app.models.mission import TaskNode

VALID_EMPTY = (
    "import pytest\nfrom app import parse_csv\n"
    "def test_empty():\n    with pytest.raises(ValueError):\n        parse_csv('')\n"
)
VALID_SOURCE = (
    "from app import parse_csv\n"
    "def test_header_only():\n    assert parse_csv('category,amount') == {}\n"
)


@pytest.mark.parametrize(
    ("source", "empty_source"),
    [
        pytest.param(VALID_SOURCE, "def test_empty():\n    pass\n", id="empty-test-no-assertion"),
        pytest.param("def test_noop():\n    assert True\n", VALID_EMPTY, id="constant-assertion"),
        pytest.param(
            "import contextlib\ndef test_noop():\n"
            "    with contextlib.nullcontext():\n        pass\n",
            VALID_EMPTY,
            id="unrelated-context-manager",
        ),
        pytest.param(
            "from app import parse_csv\ndef helper():\n"
            "    assert parse_csv('category,amount') == {}\n"
            "def test_noop():\n    pass\n",
            VALID_EMPTY,
            id="unexecuted-helper-assertion",
        ),
        pytest.param(
            "from pytest import skip as omit\nfrom app import parse_csv\n"
            "def test_skips():\n    omit('bypass')\n"
            "    assert parse_csv('category,amount') == {}\n",
            VALID_EMPTY,
            id="aliased-skip",
        ),
        pytest.param(
            "import pytest\nfrom app import parse_csv\n"
            "def test_skips():\n    pytest.importorskip('optional_module')\n"
            "    assert parse_csv('category,amount') == {}\n",
            VALID_EMPTY,
            id="importorskip",
        ),
        pytest.param(
            "from unittest.mock import Mock\n"
            "def test_mock():\n    replacement = Mock(return_value={})\n"
            "    assert replacement() == {}\n",
            VALID_EMPTY,
            id="mock-instead-of-delivery",
        ),
        pytest.param(
            "from app import parse_csv\ndef test_unreachable():\n    return\n"
            "    assert parse_csv('category,amount') == {}\n",
            VALID_EMPTY,
            id="unreachable-assertion",
        ),
    ],
)
async def test_generated_qa_rejects_vacuous_or_disabled_tests(
    settings, database, source, empty_source
):
    mid = uuid4()
    task = TaskNode(id=uuid4(), mission_id=mid, department="qa_self_healer", title="Verify CSV")
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(
        return_value=SimpleNamespace(
            structured_output={"source": source, "empty_or_missing_input_tests": empty_source}
        )
    )
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("SPEC.md", "parse_csv requires category,amount; reject no header.")
        await sandbox.write_file("app.py", "def parse_csv(text):\n    return {}\n")
        with pytest.raises(OutputValidationError):
            await acceptance_tests(router, task, sandbox)
        assert not any("acceptance_test.py" in path for path in await sandbox.list_files())
