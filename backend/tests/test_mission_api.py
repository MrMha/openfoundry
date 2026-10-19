import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from orchestration_helpers import ExecutionTrace, Planner, Worker, node, plan
from starlette.websockets import WebSocketDisconnect

from backend.app.config import Settings
from backend.app.core.llm_router import LLMRouter
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models.events import AgentThoughtEvent


def api(
    settings: Settings, output: dict | None = None, *, trace: ExecutionTrace | None = None
) -> TestClient:
    router = LLMRouter(settings, Database(settings))
    trace = trace or ExecutionTrace()
    return TestClient(
        create_app(
            settings,
            departments={
                "chief_of_staff": Planner(router, output or plan()),
                "engineering": Worker(router, trace),
            },
        )
    )


def wait_status(client: TestClient, mission_id: str, expected: str) -> dict:
    last = {}
    for _ in range(300):
        response = client.get(f"/api/missions/{mission_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == expected:
            return last
        if last["status"] in {"failed", "cancelled"}:
            pytest.fail(f"Mission ended in {last['status']}: {last}")
        time.sleep(0.01)
    pytest.fail(f"Mission did not reach {expected}: {last}")


def test_create_list_details_and_generated_files(settings: Settings) -> None:
    with api(settings) as client:
        response = client.post(
            "/api/missions", json={"goal": "Build an accessible product", "title": "Notes"}
        )
        assert response.status_code == 202
        mission_id = response.json()["id"]
        detail = wait_status(client, mission_id, "completed")
        assert detail["title"] == "Notes"
        assert len(detail["tasks"]) == 2
        assert all(task["status"] == "completed" for task in detail["tasks"])
        assert detail["last_event_id"] > 0
        assert client.get("/api/missions").json()[0]["id"] == mission_id
        files = client.get(f"/api/missions/{mission_id}/files").json()
        assert {file["file_path"] for file in files} == {
            "SPEC.md",
            "STATE.md",
            "outputs/implementation.txt",
        }
        file = client.get(f"/api/missions/{mission_id}/files/content", params={"path": "SPEC.md"})
        assert file.status_code == 200
        assert file.json()["encoding"] == "utf-8"
        assert file.json()["content"].startswith("# SPEC")
        for path in ("../private", "/etc/passwd", ".git/config"):
            assert (
                client.get(
                    f"/api/missions/{mission_id}/files/content", params={"path": path}
                ).status_code
                == 400
            )
        assert (
            client.get(
                f"/api/missions/{mission_id}/files/content", params={"path": "missing.txt"}
            ).status_code
            == 404
        )


def test_api_approval_and_websocket_replay(settings: Settings) -> None:
    trace = ExecutionTrace()
    with api(settings, plan(node("approved_work", approval=True)), trace=trace) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "Wait for an operator decision"}
        ).json()["id"]
        pending = wait_status(client, mission_id, "awaiting_approval")
        assert not trace.timeline
        approval = pending["approvals"][0]
        events = []
        with client.websocket_connect(f"/ws/missions/{mission_id}") as websocket:
            websocket.send_text("ping")
            response = client.post(
                f"/api/missions/{mission_id}/tasks/{approval['task_id']}/approve",
                json={"reason": "Reviewed"},
            )
            assert response.status_code == 200
            for _ in range(100):
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "mission_status_change" and event["status"] in {
                    "failed",
                    "cancelled",
                }:
                    detail = client.get(f"/api/missions/{mission_id}").json()
                    pytest.fail(f"Mission failed while streaming: {detail}")
                if event["type"] == "mission_status_change" and event["status"] == "completed":
                    break
            else:
                pytest.fail("No terminal event delivered")
        assert any(event["type"] == "approval_request" for event in events)
        assert any(
            event["type"] == "approval_decision" and event["decision"] == "approved"
            for event in events
        )
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(set(sequences))
        assert all(event["mission_id"] == mission_id for event in events)
        assert (
            client.post(
                f"/api/missions/{mission_id}/tasks/{approval['task_id']}/approve"
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/missions/{mission_id}/tasks/{approval['task_id']}/reject"
            ).status_code
            == 409
        )
        assert len([item for item in trace.timeline if item[0] == "start"]) == 1
        with client.websocket_connect(
            f"/ws/missions/{mission_id}?after={sequences[-2]}"
        ) as websocket:
            assert websocket.receive_json()["sequence"] == sequences[-1]


def test_rejection_and_missing_approval_resources(settings: Settings) -> None:
    with api(settings, plan(node("blocked", approval=True))) as client:
        mission_id = client.post("/api/missions", json={"goal": "Reject this plan"}).json()["id"]
        pending = wait_status(client, mission_id, "awaiting_approval")
        task_id = pending["approvals"][0]["task_id"]
        assert client.post(f"/api/missions/{uuid4()}/tasks/{task_id}/approve").status_code == 404
        response = client.post(
            f"/api/missions/{mission_id}/tasks/{task_id}/reject", json={"reason": "Change scope"}
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "rejected"
        assert client.get(f"/api/missions/{mission_id}").json()["status"] == "cancelled"
        assert client.post(f"/api/missions/{mission_id}/tasks/{task_id}/approve").status_code == 409


def test_rest_and_websocket_token_and_origin_enforcement(settings: Settings) -> None:
    secured = Settings.model_validate({**settings.model_dump(), "api_token": "test-operator-token"})
    with api(secured) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/missions").status_code == 401
        client.headers["Authorization"] = "Bearer test-operator-token"
        assert client.get("/api/missions").status_code == 200
        assert (
            client.get("/api/missions", headers={"Origin": "https://evil.example"}).status_code
            == 401
        )
        assert (
            client.get("/api/missions", headers={"Origin": "http://localhost:5173"}).status_code
            == 200
        )
        mission_id = client.post("/api/missions", json={"goal": "Protected mission"}).json()["id"]
        wait_status(client, mission_id, "completed")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws/missions/{mission_id}", headers={"Authorization": "Bearer wrong-token"}
            ):
                pytest.fail("Unauthorized socket accepted")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws/missions/{mission_id}", headers={"Origin": "https://evil.example"}
            ):
                pytest.fail("Cross-origin socket accepted")
        with client.websocket_connect(
            f"/ws/missions/{mission_id}",
            subprotocols=["openfoundry", "bearer.test-operator-token"],
            headers={"Authorization": ""},
        ) as websocket:
            assert websocket.accepted_subprotocol == "openfoundry"
            assert websocket.receive_json()["mission_id"] == mission_id


def test_missing_mission_invalid_request_and_websocket_control(settings: Settings) -> None:
    with api(settings) as client:
        assert client.post("/api/missions", json={"goal": "   "}).status_code == 422
        assert (
            client.post("/api/missions", json={"goal": "Valid", "extra": True}).status_code == 422
        )
        assert client.get("/api/missions?limit=0").status_code == 422
        assert client.get(f"/api/missions/{uuid4()}").status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/missions/{uuid4()}"):
                pytest.fail("Unknown mission accepted")
        mission_id = client.post(
            "/api/missions", json={"goal": "Control messages use REST"}
        ).json()["id"]
        wait_status(client, mission_id, "completed")
        with client.websocket_connect(f"/ws/missions/{mission_id}") as websocket:
            websocket.send_text(json.dumps({"approve": True}))
            with pytest.raises(WebSocketDisconnect):
                for _ in range(100):
                    websocket.receive_json()


def test_default_mission_without_provider_keys_fails_visibly(settings: Settings) -> None:
    settings = settings.model_copy(update={"openai_api_key": None})
    with TestClient(create_app(settings)) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "This needs a configured provider"}
        ).json()["id"]
        for _ in range(200):
            detail = client.get(f"/api/missions/{mission_id}").json()
            if detail["status"] == "failed":
                break
            time.sleep(0.01)
        assert detail["status"] == "failed"
        assert "Configure an API key" in detail["tasks"][0]["output_summary"]


def test_file_symlink_and_binary_handling(settings: Settings, tmp_path: Path) -> None:
    with api(settings) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "Inspect bounded mission files"}
        ).json()["id"]
        wait_status(client, mission_id, "completed")
        root = settings.mission_workspace(mission_id)
        (root / "escape").symlink_to(tmp_path)
        (tmp_path / "private.txt").write_text("private")
        assert (
            client.get(
                f"/api/missions/{mission_id}/files/content", params={"path": "escape/private.txt"}
            ).status_code
            == 400
        )
        (root / "image.bin").write_bytes(b"\x00\xff")
        response = client.get(
            f"/api/missions/{mission_id}/files/content", params={"path": "image.bin"}
        )
        assert response.status_code == 200
        assert response.json()["encoding"] == "base64"


def test_websocket_after_cursor_delivers_only_new_events(settings: Settings) -> None:
    with api(settings) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "Resume streaming from a durable cursor"}
        ).json()["id"]
        detail = wait_status(client, mission_id, "completed")
        with client.websocket_connect(
            f"/ws/missions/{mission_id}?after={detail['last_event_id']}"
        ) as websocket:
            event = AgentThoughtEvent(
                mission_id=mission_id, department="engineering", thought="A new committed event"
            )
            client.portal.call(client.app.state.connection_manager.publish, event)
            delivered = websocket.receive_json()
            assert delivered["event_id"] == str(event.event_id)
            assert delivered["sequence"] > detail["last_event_id"]


def test_rapid_socket_disconnects_release_database_resources(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with api(settings) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "Disconnect during replay safely"}
        ).json()["id"]
        wait_status(client, mission_id, "completed")
        for _ in range(20):
            with client.websocket_connect(f"/ws/missions/{mission_id}") as websocket:
                assert websocket.receive_json()["mission_id"] == mission_id
        assert client.get(f"/api/missions/{mission_id}").status_code == 200
        assert not client.app.state.connection_manager._subscribers
    assert not [
        record
        for record in caplog.records
        if record.name.startswith("sqlalchemy.pool") and record.levelname == "ERROR"
    ]


def test_event_history_survives_application_restart(settings: Settings) -> None:
    with api(settings) as client:
        mission_id = client.post("/api/missions", json={"goal": "Persist the event stream"}).json()[
            "id"
        ]
        detail = wait_status(client, mission_id, "completed")
        cursor = detail["last_event_id"]
    with api(settings) as restarted:
        assert restarted.get(f"/api/missions/{mission_id}").json()["status"] == "completed"
        with restarted.websocket_connect(
            f"/ws/missions/{mission_id}?after={cursor - 1}"
        ) as websocket:
            terminal = websocket.receive_json()
            assert terminal["sequence"] == cursor
            assert terminal["status"] == "completed"


def test_binary_websocket_messages_are_rejected_cleanly(settings: Settings) -> None:
    with api(settings) as client:
        mission_id = client.post(
            "/api/missions", json={"goal": "Only supported control frames"}
        ).json()["id"]
        detail = wait_status(client, mission_id, "completed")
        with client.websocket_connect(
            f"/ws/missions/{mission_id}?after={detail['last_event_id']}"
        ) as websocket:
            websocket.send_bytes(b"unsupported")
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()
