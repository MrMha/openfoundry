import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import SecretStr, ValidationError

from backend.app.config import Settings
from backend.app.core.blueprints import Blueprint, load_blueprints
from backend.app.core.mcp_bridge import MCPBridge, MCPBridgeError, validate_schema
from backend.app.core.mcp_config import MCPServerConfig
from backend.app.main import create_app
from backend.app.models.mission import Mission, MissionStatus, TaskNode, TaskStatus


def test_blueprints_validate_defaults_dags_parameters_and_gates():
    templates = load_blueprints()
    assert {item.id for item in templates} == {
        "saas_mvp_builder",
        "website_self_healer",
        "career_opportunity_hunter",
    }
    for template in templates:
        goal, tasks, values = template.resolve({})
        assert "$" not in goal and values and tasks
        assert all(task.requires_approval for task in tasks if task.department == "engineer")
        with pytest.raises(ValueError, match="Unknown"):
            template.resolve({"unrecognized": "data"})
    raw = templates[0].model_dump()
    raw["tasks"][0]["dependencies"] = [raw["tasks"][0]["key"]]
    with pytest.raises(ValidationError):
        Blueprint.model_validate(raw)
    raw = templates[0].model_dump()
    raw["goal_template"] = "$unknown"
    with pytest.raises(ValidationError):
        Blueprint.model_validate(raw)


def test_blueprint_api_propagates_parameterized_task_context_and_execution_profile(
    settings, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.core.orchestrator.MissionOrchestrator._enqueue", lambda *args: None
    )
    with TestClient(create_app(settings)) as client:
        templates = client.get("/api/blueprints")
        assert templates.status_code == 200 and len(templates.json()) == 3
        created = client.post(
            "/api/missions",
            json={
                "goal": "My custom product scope",
                "blueprint_id": "saas_mvp_builder",
                "blueprint_parameters": {"idea": "a community library"},
            },
        )
        assert created.status_code == 202
        detail = client.get(f"/api/missions/{created.json()['id']}").json()
        assert detail["execution"]["blueprint_parameters"]["idea"] == "a community library"
        assert "template_tasks" in detail["tasks"][0]["description"]
        assert "a community library" in detail["tasks"][0]["description"]
        assert (
            client.post(
                "/api/missions", json={"goal": "bad", "blueprint_id": "missing"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/missions",
                json={
                    "goal": "bad",
                    "blueprint_id": "website_self_healer",
                    "blueprint_parameters": {"target_url": "file:///etc/passwd"},
                },
            ).status_code
            == 422
        )
        assert client.get("/api/plugins").json()["mcp"]["server_tools"]
    token_settings = settings.model_copy(update={"api_token": SecretStr("operator-token")})
    with TestClient(create_app(token_settings)) as client:
        assert client.get("/api/blueprints").status_code == 401


def fixture_config(**updates):
    return MCPServerConfig(
        name="fixture",
        transport="stdio",
        command=sys.executable,
        args=(str(Path(__file__).with_name("mcp_fixture_server.py")),),
        allowed_tools=("add", "slow"),
        departments=("researcher",),
        requires_approval=False,
        timeout_seconds=30 if sys.platform == "win32" else 5,
        **updates,
    )


async def active_task(database):
    async with database.session(write=True) as session:
        mission = Mission(
            id=uuid4(), title="MCP", goal="Add two integers", status=MissionStatus.RUNNING
        )
        session.add(mission)
        await session.flush()
        task = TaskNode(
            id=uuid4(),
            mission_id=mission.id,
            department="researcher",
            title="Use allowed tool",
            description="Add",
            status=TaskStatus.RUNNING,
        )
        session.add(task)
    return task


async def test_real_external_mcp_stdio_discovery_invocation_and_capability_guard(
    settings, database
):
    config = fixture_config()
    configured = settings.model_copy(
        update={"mcp_servers": (config,), "mcp_command_allowlist": (sys.executable,)}
    )
    bridge = MCPBridge(configured, database)
    tools = await bridge.discover("fixture")
    assert {tool.name for tool in tools} == {"add", "slow"}
    task = await active_task(database)
    result = await bridge.call_tool(task, "fixture", "add", {"a": 2, "b": 3})
    assert result.structuredContent == {"value": 5}
    with pytest.raises(MCPBridgeError, match="no retry"):
        await bridge.call_tool(task, "fixture", "add", {"a": "not an integer", "b": 3})
    with pytest.raises(MCPBridgeError, match="not granted"):
        await bridge.call_tool(task, "fixture", "shell", {})
    task.department = "growth_career"
    with pytest.raises(MCPBridgeError, match="not granted"):
        await bridge.call_tool(task, "fixture", "add", {"a": 1, "b": 1})
    await bridge.close()


async def test_mcp_approval_and_stale_task_guards_precede_transport(
    settings, database, monkeypatch
):
    config = fixture_config().model_copy(update={"requires_approval": True})
    bridge = MCPBridge(settings.model_copy(update={"mcp_servers": (config,)}), database)
    task = await active_task(database)
    session = AsyncMock()
    monkeypatch.setattr(bridge, "_session", session)
    with pytest.raises(MCPBridgeError, match="approval"):
        await bridge.call_tool(task, "fixture", "add", {"a": 1, "b": 2})
    async with database.session(write=True) as db:
        current = await db.get(TaskNode, task.id)
        current.status = TaskStatus.COMPLETED
    with pytest.raises(MCPBridgeError, match="active persisted"):
        await bridge.call_tool(task, "fixture", "add", {"a": 1, "b": 2})
    session.assert_not_called()


async def test_mcp_timeouts_cover_lock_queue_and_redact_status(settings, database):
    config = fixture_config().model_copy(update={"timeout_seconds": 0.05})
    bridge = MCPBridge(settings.model_copy(update={"mcp_servers": (config,)}), database)
    await bridge._locks["fixture"].acquire()
    try:
        with pytest.raises(MCPBridgeError):
            await bridge.discover("fixture")
    finally:
        bridge._locks["fixture"].release()
    status = await bridge.inspect()
    assert status.external_servers[0].state == "error"
    assert "TimeoutError" in status.model_dump_json()
    assert sys.executable not in status.model_dump_json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"command": "python"},
        {"env": {"LD_PRELOAD": "bad"}},
        {"transport": "streamable_http", "url": "https://user:secret@server/mcp"},
    ],
)
def test_mcp_operator_configuration_rejects_uncontained_transport(overrides):
    raw = fixture_config().model_dump()
    raw.update(overrides)
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate(raw)


def test_mcp_schema_never_resolves_external_references():
    with pytest.raises(ValueError, match="Remote"):
        validate_schema({"$ref": "https://untrusted.example/schema"}, {})
    with pytest.raises(ValueError, match="Remote"):
        validate_schema({"$dynamicRef": "file:///private"}, {})
    with pytest.raises(ValidationError):
        Settings(mcp_servers=(fixture_config(),))


async def start_server(app):
    import socket

    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    runner = asyncio.create_task(server.serve(sockets=[listener]))
    async with asyncio.timeout(10):
        while not server.started:
            if runner.done():
                await runner
                raise AssertionError("Fixture API failed to start")
            await asyncio.sleep(0.01)
    return server, runner, f"http://127.0.0.1:{port}"


async def test_official_stdio_openfoundry_tools_drive_real_demo_and_authentication(tmp_path):
    config = Settings(
        data_dir=tmp_path, api_token=SecretStr("mcp-test-operator"), sandbox_mode="guarded"
    )
    api, runner, url = await start_server(create_app(config))
    try:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "backend.app.main", "mcp", "--api-url", url],
            env={"OPENFOUNDRY_API_TOKEN": "mcp-test-operator"},
        )
        async with (
            # Includes cold stdio startup plus the real subprocess repair mission.
            asyncio.timeout(180),
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {
                "start_mission",
                "get_mission_status",
                "run_demo_self_healing_mission",
                "list_departments",
                "decide_mission_task",
                "list_blueprints",
            } == names
            catalog = await session.call_tool("list_departments", {})
            assert not catalog.isError and len(catalog.structuredContent["plugins"]) >= 5
            templates = await session.call_tool("list_blueprints", {})
            assert len(templates.structuredContent["items"]) == 3
            demo = await session.call_tool("run_demo_self_healing_mission", {})
            mission_id = demo.structuredContent["id"]
            while True:
                result = await session.call_tool("get_mission_status", {"mission_id": mission_id})
                state = result.structuredContent
                if state["status"] == "awaiting_approval":
                    break
                assert state["status"] not in {"failed", "cancelled"}, state
                await asyncio.sleep(0.05)
            approved = await session.call_tool(
                "decide_mission_task",
                {
                    "mission_id": mission_id,
                    "task_id": state["approvals"][0]["task_id"],
                    "decision": "approve",
                },
            )
            assert not approved.isError
            while True:
                result = await session.call_tool("get_mission_status", {"mission_id": mission_id})
                state = result.structuredContent
                if state["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            assert state["status"] == "completed", state
            assert len(state["tasks"]) == 5 and float(state["total_cost_usd"]) == 0
            invalid = await session.call_tool("get_mission_status", {"mission_id": "not-a-uuid"})
            assert invalid.isError
    finally:
        api.should_exit = True
        await runner


async def test_real_streamable_http_mcp_transport(settings, database):
    from mcp.server.fastmcp import FastMCP

    remote = FastMCP("HTTP fixture", stateless_http=True, json_response=True)

    @remote.tool()
    def add(a: int, b: int) -> dict[str, int]:
        return {"value": a + b}

    server, runner, url = await start_server(remote.streamable_http_app())
    config = MCPServerConfig(
        name="remote",
        transport="streamable_http",
        url=url + "/mcp",
        allowed_tools=("add",),
        departments=("researcher",),
        requires_approval=False,
    )
    bridge = MCPBridge(settings.model_copy(update={"mcp_servers": (config,)}), database)
    try:
        assert (await bridge.discover("remote"))[0].name == "add"
        result = await bridge.call_tool(
            await active_task(database), "remote", "add", {"a": 4, "b": 5}
        )
        assert result.structuredContent == {"value": 9}
    finally:
        await bridge.close()
        server.should_exit = True
        await runner
