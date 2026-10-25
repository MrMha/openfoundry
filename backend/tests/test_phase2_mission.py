"""Five real departments, live WebSocket events, one repair, and real USD ledger logic."""

import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import litellm
from fastapi.testclient import TestClient
from orchestration_helpers import node, plan
from sqlalchemy import select
from test_engineering_qa import FIXED_APP, diagnosis, implementation, repair
from test_growth_career import launch_output

from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.departments._research_web import PublicWeb
from backend.app.main import create_app
from backend.app.models.events import (
    AgentThoughtEvent,
    ApprovalRequestEvent,
    BrowserFrameEvent,
    CodeDiffEvent,
    MissionStatusChangeEvent,
    TaskStatusChangeEvent,
    parse_event,
)
from backend.app.models.mission import AttemptStatus, LLMAttempt


def test_complete_phase2_mission_streams_one_self_heal_and_accounts_for_every_paid_call(
    settings, monkeypatch
):
    # Replace only provider I/O and the public HTTP boundary. All plugin execution,
    # schema validation, budget reservations, accounting, Git, tests and ASGI probes are real.
    planning = plan(
        node("research", department="researcher"),
        node("implement", ["research"], department="engineer"),
        node("verify", ["implement"], department="qa_self_healer"),
        node("launch", ["verify"], department="growth_career"),
    )
    planning["specification"] = (
        "# Calculator\nImplement integer addition and a Ready homepage; verify and draft a launch."
    )
    planning["tasks"][0]["description"] = json.dumps(
        {
            "query": "integer addition",
            "urls": ["https://example.com/addition"],
            "prefer_browser": False,
        }
    )
    outputs = [
        planning,
        implementation(),
        implementation(),
        diagnosis().model_dump(mode="json"),
        repair(),
        launch_output(),
    ]
    provider_calls = []

    async def completion(**kwargs):
        index = len(provider_calls)
        provider_calls.append(kwargs)
        assert index < len(outputs), "Unexpected extra paid call"
        return {
            "choices": [
                {"message": {"content": json.dumps(outputs[index])}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 200 + index * 20, "completion_tokens": 1000 + index * 100},
        }

    completion_spy = AsyncMock(side_effect=completion)
    monkeypatch.setattr(litellm, "acompletion", completion_spy)
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda model: {
            "max_input_tokens": 100000,
            "max_output_tokens": 8192,
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
        },
    )
    monkeypatch.setattr(
        litellm,
        "cost_per_token",
        lambda *, model, prompt_tokens, completion_tokens: (
            prompt_tokens * 0.000001,
            completion_tokens * 0.000002,
        ),
    )
    requested_sources = []

    def research_http(request):
        requested_sources.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<title>Addition</title><main><h1>Integer addition</h1>"
                "<p>Addition combines integer operands.</p></main>"
            ),
        )

    transport = httpx.MockTransport(research_http)
    monkeypatch.setattr(
        "backend.app.departments.researcher.PublicWeb", lambda: PublicWeb(transport)
    )
    config = settings.model_copy(
        update={
            "sandbox_mode": "guarded",
            "max_output_tokens": 4096,
            "department_timeout_seconds": 180,
        }
    )
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/missions",
            json={
                "goal": "Research, build, verify and draft launch assets for an integer calculator."
            },
        )
        assert created.status_code == 202
        mission_id = created.json()["id"]
        events = []
        deadline = time.monotonic() + 240
        with client.websocket_connect(f"/ws/missions/{mission_id}?after=0") as websocket:
            while time.monotonic() < deadline:
                payload = websocket.receive_json()
                if payload["type"] == "heartbeat":
                    continue
                event = parse_event(json.dumps(payload))
                events.append(event)
                if isinstance(event, ApprovalRequestEvent):
                    assert event.department == "engineer"
                    approved = client.post(
                        f"/api/missions/{mission_id}/tasks/{event.task_id}/approve",
                        json={"reason": "Reviewed generated specification"},
                    )
                    assert approved.status_code == 200
                if isinstance(event, MissionStatusChangeEvent) and event.status in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    detail = client.get(f"/api/missions/{mission_id}").json()
                    assert event.status == "completed", detail
                    break
            else:
                raise AssertionError("Mission never emitted a terminal WebSocket event")
        detail = client.get(f"/api/missions/{mission_id}").json()
        assert detail["status"] == "completed"
        assert len(detail["tasks"]) == 5
        assert all(task["status"] == "completed" for task in detail["tasks"])
        by_department = {task["department"]: task for task in detail["tasks"]}
        assert set(by_department) == {
            "chief_of_staff",
            "researcher",
            "engineer",
            "qa_self_healer",
            "growth_career",
        }
        started = [
            event.department
            for event in events
            if isinstance(event, TaskStatusChangeEvent) and event.status == "running"
        ]
        assert started == [
            "chief_of_staff",
            "researcher",
            "engineer",
            "qa_self_healer",
            "growth_career",
        ]
        assert {
            event.department for event in events if isinstance(event, AgentThoughtEvent)
        } == set(by_department)
        assert any(isinstance(event, BrowserFrameEvent) for event in events)
        assert {
            event.test_result.status for event in events if isinstance(event, CodeDiffEvent)
        } >= {"failed", "not_run", "passed"}
        sequences = [event.sequence for event in events]
        assert sequences == sorted(set(sequences))
        assert all(str(event.mission_id) == mission_id for event in events)
        with client.websocket_connect(
            f"/ws/missions/{mission_id}?after={sequences[-2]}"
        ) as websocket:
            assert websocket.receive_json()["sequence"] == sequences[-1]
        paths = {
            artifact["file_path"]
            for artifact in client.get(f"/api/missions/{mission_id}/files").json()
        }
        assert {"SPEC.md", "STATE.md", "README.md", "app.py", "tests/test_app.py"} <= paths
        qa_id = by_department["qa_self_healer"]["id"]
        growth_id = by_department["growth_career"]["id"]
        research_id = by_department["researcher"]["id"]
        root = config.mission_workspace(mission_id)
        report = json.loads((root / f"qa/{qa_id}/report.json").read_text())
        assert report["passed"] and report["repairs"] == 1
        assert [round_["passed"] for round_ in report["history"]] == [False, True]
        assert (root / "app.py").read_text() == FIXED_APP
        assert len(list((root / f"qa/{qa_id}").glob("bug-*.json"))) == 1
        assert f"research/{research_id}/manifest.json" in paths
        assert {
            f"growth/{growth_id}/{name}"
            for name in [
                "README.md",
                "architecture.mmd",
                "show_hn.md",
                "reddit.md",
                "x.md",
                "sources.json",
                "manifest.json",
            ]
        } <= paths
        assert "```mermaid" in (root / "README.md").read_text()
        source_context = json.loads(provider_calls[-1]["messages"][1]["content"])
        assert {task["department"] for task in source_context["completed_tasks"]} == {
            "chief_of_staff",
            "researcher",
            "engineer",
            "qa_self_healer",
        }
        assert any(
            source.get("verification") == {"passed": True, "repairs": 1}
            for source in source_context["artifacts"]
        )
        assert requested_sources == ["https://example.com/addition"]
        assert len(provider_calls) == 6
        assert all(call["num_retries"] == 0 and call["fallbacks"] == [] for call in provider_calls)
        expected_cost = sum(
            (
                Decimal(200 + index * 20) * Decimal("0.000001")
                + Decimal(1000 + index * 100) * Decimal("0.000002")
                for index in range(6)
            ),
            Decimal(0),
        )
        assert Decimal(detail["total_cost_usd"]) == expected_cost == Decimal("0.016500000")
        assert (
            Decimal(detail["reserved_cost_usd"]) == 0
            and expected_cost < config.max_mission_cost_usd
        )

        async def accounting_and_memory():
            async with client.app.state.database.session() as session:
                ledger = list(
                    (
                        await session.scalars(
                            select(LLMAttempt).where(LLMAttempt.mission_id == UUID(mission_id))
                        )
                    ).all()
                )
            memory = MemoryVault(mission_id, storage_root=config.memory_root)
            return ledger, await memory.search("subtracts", kind=MemoryKind.BUG_REPORT)

        ledger, bugs = client.portal.call(accounting_and_memory)
        assert len(ledger) == 6 and all(
            attempt.status == AttemptStatus.SUCCEEDED for attempt in ledger
        )
        assert sum(attempt.cost_usd for attempt in ledger) == expected_cost
        assert sum(attempt.prompt_tokens for attempt in ledger) == 1500
        assert sum(attempt.completion_tokens for attempt in ledger) == 7500
        assert bugs and len(bugs) == 1
        catalog = client.get("/api/plugins").json()
        assert {plugin["name"] for plugin in catalog["plugins"] if plugin["enabled"]} >= set(
            by_department
        )
        assert catalog["sandbox"]["execution_mode"] == "guarded"
