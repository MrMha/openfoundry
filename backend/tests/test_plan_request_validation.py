"""Reject malformed downstream task requests while the plan is still correctable."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import select
from test_chief_of_staff import delivery_plan, planning_task

from backend.app.core.llm_router import LLMRouter
from backend.app.core.memory_vault import MemoryVault
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.chief_of_staff import ChiefOfStaff
from backend.app.departments.researcher import ResearchRequest
from backend.app.models.mission import LLMAttempt, Mission, MissionStatus


async def test_plan_corrects_trailing_prose_before_dispatch_and_meters_both_attempts(
    settings, database
):
    mid = uuid4()
    async with database.session(write=True) as session:
        session.add(
            Mission(id=mid, title="CSV", goal="Analyze requirements", status=MissionStatus.RUNNING)
        )
    valid = delivery_plan()
    request = {
        "query": "Analyze CSV requirements",
        "documents": ["SPEC.md"],
        "prefer_docling": False,
    }
    valid["tasks"][0]["description"] = json.dumps(request)
    invalid = json.loads(json.dumps(valid))
    invalid["tasks"][0]["description"] += " Requirements analysis of the task specification."
    calls = []
    configured = settings.model_copy(update={"provider_max_attempts": 2})
    async with WorkspaceSandbox(mid, root=settings.workspace_root) as sandbox:

        async def completion(**kwargs):
            assert "SPEC.md" not in await sandbox.list_files()
            calls.append(kwargs)
            body = invalid if len(calls) == 1 else valid
            return {
                "choices": [{"message": {"content": json.dumps(body)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            }

        router = LLMRouter(configured, database, completion=completion)
        output = await ChiefOfStaff(router).execute_task(
            planning_task(mid),
            sandbox,
            MemoryVault(mid, storage_root=settings.memory_root),
            AsyncMock(),
        )
        research = next(t for t in output["tasks"] if t["department"] == "researcher")
        assert ResearchRequest.from_description(research["description"]).documents == ["SPEC.md"]
        assert len(calls) == 2
        assert "trailing" in calls[1]["messages"][-1]["content"].lower()
        assert "SPEC.md" in await sandbox.list_files()
    async with database.session() as session:
        rows = list(await session.scalars(select(LLMAttempt).where(LLMAttempt.mission_id == mid)))
    assert len(rows) == 2
    assert sum(row.error_code == "invalid_output" for row in rows) == 1
