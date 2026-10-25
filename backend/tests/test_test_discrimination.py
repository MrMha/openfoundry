"""Replay a recorded live model workaround against a stricter external oracle."""

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.core.llm_router import OutputValidationError
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.repair_policy import reject_test_discrimination
from scripts.evaluation_support import independently_check
from scripts.live_evaluation_cases import CASES

FIXTURES = Path(__file__).with_name("fixtures")


async def test_recorded_caller_sensitive_delivery_cannot_pass_current_external_checks(settings):
    mid = uuid4()
    source = (FIXTURES / "caller_sensitive_csv.py.txt").read_text(encoding="utf-8")
    with pytest.raises(OutputValidationError, match="caller"):
        reject_test_discrimination(ast.parse(source))
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:
        await sandbox.write_file("app.py", source)
    old = await independently_check(
        settings, mid, (FIXTURES / "csv_original_checks.py.txt").read_text(encoding="utf-8")
    )
    assert old["status"] == "passed", old
    stricter = await independently_check(settings, mid, CASES["csv_summary"]["checks"])
    assert stricter["status"] == "failed", stricter
    assert "test_same_empty_input_contract_for_every_caller" in stricter["stdout"]


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef answer(x):\n    return 1 if os.getenv('PYTEST_CURRENT_TEST') else x\n",
        "from inspect import stack as frames\ndef answer(x):\n"
        "    return 1 if 'test_app' in frames()[1].filename else x\n",
    ],
)
def test_test_environment_or_aliased_caller_workarounds_are_rejected(source):
    with pytest.raises(OutputValidationError):
        reject_test_discrimination(ast.parse(source))


def test_normal_input_validation_is_allowed():
    reject_test_discrimination(
        ast.parse(
            "def parse_csv(text):\n    if not text:\n        raise ValueError('Missing header')\n"
            "    return {'example': 1}\n"
        )
    )
