"""Key-free demo isolation, approvals, real failure/repair and browser bootstrap."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import litellm
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models.mission import LLMAttempt


def test_key_free_demo_uses_real_loop_without_mutating_live_routing(tmp_path, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Demo attempted external provider I/O"))
    monkeypatch.setattr(litellm, "acompletion", forbidden)
    config = Settings(data_dir=tmp_path, sandbox_mode="guarded")
    with TestClient(create_app(config)) as client:
        runtime = client.get("/api/runtime").json()
        assert runtime["auth_required"] is False
        assert not any(provider["configured"] for provider in runtime["providers"])
        first = client.post("/api/missions/demo")
        assert first.status_code == 202
        mission_id = first.json()["id"]
        events = []
        with client.websocket_connect(f"/ws/missions/{mission_id}?after=0") as socket:
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "approval_request":
                    task_id = event["task_id"]
                    assert (
                        client.get(f"/api/missions/{mission_id}").json()["status"]
                        == "awaiting_approval"
                    )
                    assert (
                        client.post(
                            f"/api/missions/{mission_id}/tasks/{task_id}/approve"
                        ).status_code
                        == 200
                    )
                if event["type"] == "mission_status_change" and event["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    assert event["status"] == "completed", client.get(
                        f"/api/missions/{mission_id}"
                    ).json()
                    break
        detail = client.get(f"/api/missions/{mission_id}").json()
        assert detail["execution"]["mode"] == "demo"
        assert len(detail["tasks"]) == 5
        assert all(task["status"] == "completed" for task in detail["tasks"])
        assert Decimal(detail["total_cost_usd"]) == Decimal(detail["reserved_cost_usd"]) == 0
        assert {
            event["test_result"]["status"] for event in events if event["type"] == "code_diff"
        } >= {"failed", "passed"}
        assert any(event["type"] == "browser_frame" for event in events)
        qa_task = next(task for task in detail["tasks"] if task["department"] == "qa_self_healer")
        report = client.get(
            f"/api/missions/{mission_id}/files/content",
            params={"path": f"qa/{qa_task['id']}/report.json"},
        ).json()
        assert json.loads(report["content"])["repairs"] == 1
        assert any(artifact["file_path"].endswith("show_hn.md") for artifact in detail["artifacts"])

        async def attempts():
            async with client.app.state.database.session() as session:
                return list(
                    (
                        await session.scalars(
                            select(LLMAttempt).where(LLMAttempt.mission_id == UUID(mission_id))
                        )
                    ).all()
                )

        ledger = client.portal.call(attempts)
        assert len(ledger) == 5 and all(row.cost_usd == 0 for row in ledger)
        assert all(row.model == "ollama_chat/openfoundry-offline-demo" for row in ledger)
        # A subsequent live mission retains the original key-free routing and fails clearly.
        live = client.post("/api/missions", json={"goal": "A real live mission"}).json()
        client.portal.call(client.app.state.orchestrator.wait, UUID(live["id"]))
        live_detail = client.get(f"/api/missions/{live['id']}").json()
        assert live_detail["execution"]["mode"] == "live"
        assert live_detail["status"] == "failed"
    forbidden.assert_not_awaited()


def test_demo_and_runtime_require_operator_auth_without_disclosing_secrets(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token=SecretStr("private-operator"))
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/missions/demo").status_code == 401
        assert client.get("/api/runtime").status_code == 401
        response = client.get("/api/runtime", headers={"Authorization": "Bearer private-operator"})
        assert response.status_code == 200 and response.json()["auth_required"]
        assert "private-operator" not in response.text
        assert (
            client.post(
                "/api/missions",
                headers={"Authorization": "Bearer private-operator"},
                json={"goal": "test", "configuration": {"mode": "demo"}},
            ).status_code
            == 422
        )


def test_per_mission_configuration_validation(settings):
    with TestClient(create_app(settings)) as client:
        for configuration in [
            {"provider": "anthropic"},
            {"provider": "openai", "model": "gemini/not-an-openai-model"},
            {"budget_usd": "3"},
            {"budget_usd": "0"},
            {"budget_usd": "0.1234567891"},
        ]:
            assert (
                client.post(
                    "/api/missions", json={"goal": "test", "configuration": configuration}
                ).status_code
                == 422
            )


def test_demo_approval_wait_recovers_its_offline_profile_after_restart(tmp_path, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("External I/O after demo restart"))
    monkeypatch.setattr(litellm, "acompletion", forbidden)
    config = Settings(data_dir=tmp_path, sandbox_mode="guarded")
    with TestClient(create_app(config)) as client:
        mission_id = client.post("/api/missions/demo").json()["id"]
        with client.websocket_connect(f"/ws/missions/{mission_id}") as socket:
            while socket.receive_json()["type"] != "approval_request":
                pass
    with TestClient(create_app(config)) as client:
        detail = client.get(f"/api/missions/{mission_id}").json()
        assert detail["execution"]["mode"] == "demo"
        task_id = detail["approvals"][0]["task_id"]
        assert client.post(f"/api/missions/{mission_id}/tasks/{task_id}/approve").status_code == 200
        client.portal.call(client.app.state.orchestrator.wait, UUID(mission_id))
        assert client.get(f"/api/missions/{mission_id}").json()["status"] == "completed"
    forbidden.assert_not_awaited()


def test_preferences_keep_credentials_and_server_policy_unchanged(settings):
    from backend.app.config import MissionPreferences, mission_settings

    original = settings.model_dump()
    preferences = MissionPreferences(provider="openai", model="openai/gpt-4.1-mini", budget_usd="1")
    selected = mission_settings(settings, preferences)
    assert selected.provider_order == ("openai",)
    assert selected.openai_model == "openai/gpt-4.1-mini"
    assert selected.max_mission_cost_usd == 1
    assert selected.openai_api_key == settings.openai_api_key
    assert settings.model_dump() == original
    assert "unit-test-key" not in preferences.model_dump_json()
