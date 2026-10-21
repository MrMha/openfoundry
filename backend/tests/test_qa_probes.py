"""Probe fallbacks, browser adapter contracts, and real HTTP/ASGI evidence boundaries."""

import asyncio
import base64
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from orchestration_helpers import Planner, node, plan
from sqlalchemy import select
from test_departments import result
from test_engineering_qa import implementation, setup_project, task
from test_orchestrator import mission_state

from backend.app.api.ws_war_room import ConnectionManager
from backend.app.core.llm_router import LLMRouter
from backend.app.core.orchestrator import MissionOrchestrator
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments import _qa_probe_worker as worker
from backend.app.departments.base_department import DepartmentExecutionError
from backend.app.departments.engineer import Engineer
from backend.app.departments.engineering_contracts import EndpointProbe, VerificationPlan
from backend.app.departments.qa_self_healer import QASelfHealer
from backend.app.models.events import BrowserFrameEvent
from backend.app.models.mission import Artifact, MissionStatus

PROBE = {"path": "/", "expected_status": 200, "contains": "Ready", "selectors": ["h1"]}
HTML = "<h1>Ready</h1><p>Verified HTML evidence</p>"


async def test_missing_optional_browsers_emit_labeled_dom_diagnostic(monkeypatch):
    monkeypatch.setattr(worker, "_playwright", AsyncMock(side_effect=ImportError))
    monkeypatch.setattr(worker, "_browser_use", AsyncMock(side_effect=RuntimeError))
    rendered = await worker.render_response(HTML, PROBE, "http://asgi.local/", 200, True)
    assert rendered.backend == "http-asgi-dom" and not rendered.failures
    assert rendered.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(rendered.warnings) == 3
    assert "DOM-only" in rendered.warnings[-1]


async def test_browser_cancellation_does_not_turn_into_successful_fallback(monkeypatch):
    fallback = AsyncMock()
    monkeypatch.setattr(worker, "_playwright", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(worker, "_browser_use", fallback)
    with pytest.raises(asyncio.CancelledError):
        await worker.render_response(HTML, PROBE, "http://asgi.local/", 200, True)
    fallback.assert_not_awaited()


@pytest.mark.parametrize("html", ["<h1>Wrong text</h1>", "<p>Ready</p>"])
def test_dom_checks_fail_on_missing_text_or_selector(html):
    assert worker.validate_dom(html, PROBE)


async def test_playwright_adapter_captures_real_frame_and_console_errors(monkeypatch):
    handlers = {}
    png = worker.diagnostic_frame("http://asgi.local/", 200, HTML, [])
    page = SimpleNamespace(
        set_content=AsyncMock(),
        content=AsyncMock(return_value=HTML),
        screenshot=AsyncMock(return_value=png),
        on=lambda name, callback: handlers.update({name: callback}),
    )
    context = SimpleNamespace(route=AsyncMock(), new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    launch = AsyncMock(return_value=browser)
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    monkeypatch.setattr(
        worker.importlib,
        "import_module",
        lambda name: SimpleNamespace(async_playwright=lambda: manager),
    )
    rendered = await worker._playwright(HTML, PROBE)
    assert rendered.backend == "playwright-dom" and not rendered.failures
    assert rendered.png == png
    assert launch.call_args.kwargs["chromium_sandbox"] is True
    assert launch.call_args.kwargs["proxy"]["bypass"] == "<-loopback>"
    assert context.route.await_count == 1
    browser.close.assert_awaited_once()
    handlers["console"](SimpleNamespace(type="error", text="Uncaught render error"))
    handlers["pageerror"](RuntimeError("page error"))
    assert rendered.errors == ["Uncaught render error", "page error"]


async def test_browser_use_adapter_screenshots_and_cleans_session(monkeypatch):
    png = worker.diagnostic_frame("http://asgi.local/", 200, HTML, [])
    page = SimpleNamespace(
        evaluate=AsyncMock(side_effect=["ready", HTML, '["render error"]']),
        screenshot=AsyncMock(return_value=base64.b64encode(png).decode()),
    )
    browser = SimpleNamespace(
        start=AsyncMock(), new_page=AsyncMock(return_value=page), kill=AsyncMock()
    )
    factory = MagicMock(return_value=SimpleNamespace(get_args=lambda: []))
    session = SimpleNamespace(BrowserSession=lambda **kwargs: browser)
    profiles = SimpleNamespace(BrowserProfile=factory, ProxySettings=lambda **kwargs: kwargs)
    monkeypatch.setattr(
        worker.importlib,
        "import_module",
        lambda name: session if name.endswith("session") else profiles,
    )
    rendered = await worker._browser_use(HTML, PROBE)
    assert rendered.backend == "browser-use-dom" and rendered.errors == ["render error"]
    assert rendered.png == png
    browser.kill.assert_awaited_once()
    assert factory.call_args.kwargs["use_cloud"] is False
    assert factory.call_args.kwargs["enable_default_extensions"] is False


async def test_dns_rebinding_cannot_turn_public_host_into_loopback_probe(monkeypatch):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    import httpx

    with pytest.raises(ValueError, match="allowed"):
        await worker._addresses(httpx.URL("https://public.example/"))
    assert await worker._addresses(httpx.URL("http://localhost:8000/")) == ("127.0.0.1",)


async def test_real_loopback_http_probes_execute_inside_sandbox_worker(settings, database):
    requests = []

    async def respond(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        requests.append(request)
        body = HTML.encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        async with WorkspaceSandbox(
            uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
        ) as sandbox:
            router, memory, _, _ = await setup_project(settings, database, sandbox, good=True)
            router.complete = AsyncMock()
            qa_task = task(sandbox.mission_id, "qa_self_healer")
            qa_task.description = VerificationPlan(
                base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                probes=[EndpointProbe(contains="Ready", selectors=["h1"])],
                prefer_browser=False,
            ).model_dump_json()
            emitter = AsyncMock()
            output = await QASelfHealer(router).execute_task(qa_task, sandbox, memory, emitter)
            assert output["passed"] is True and requests[0].startswith(b"GET / HTTP/1.1")
            router.complete.assert_not_awaited()
            assert any(
                isinstance(call.args[0], BrowserFrameEvent) for call in emitter.await_args_list
            )
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "invalid",
    [
        {"max_repairs": 4},
        {"asgi_app": "app.py"},
        {"base_url": "file:///etc/passwd"},
        {"base_url": "http://169.254.169.254/"},
        {"base_url": "https://user:pass@example.com/"},
        {"probes": [{"path": "//other.example/"}], "asgi_app": "app:app"},
        {"commands": [{"command": ["sh", "-c", "id"]}]},
        {"commands": [{"command": ["pytest", "--collect-only"]}]},
    ],
)
def test_invalid_verification_scope_and_retry_limits_are_rejected(invalid):
    with pytest.raises(ValueError):
        VerificationPlan.model_validate(invalid)


async def test_failed_qa_persists_diagnostic_artifacts_through_orchestrator(settings, database):
    router = LLMRouter(settings, database)

    async def completion(mission_id, messages, **kwargs):
        return result(mission_id, implementation(max_repairs=0))

    router.complete = AsyncMock(side_effect=completion)
    manager = ConnectionManager(database)
    coordinator = MissionOrchestrator(
        settings,
        database,
        manager,
        departments={
            "chief_of_staff": Planner(
                router,
                plan(
                    node("code", department="engineer"),
                    node("verify", ["code"], department="qa_self_healer"),
                ),
            ),
            "engineer": Engineer(router),
            "qa_self_healer": QASelfHealer(router),
        },
    )
    await coordinator.start()
    try:
        mission = await coordinator.submit("Verify failed calculator behavior")
        await asyncio.wait_for(coordinator.wait(mission.id), 180)
        assert await mission_state(database, mission.id) == MissionStatus.FAILED
        async with database.session() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.mission_id == mission.id))
            ).all()
        assert any(artifact.artifact_type == "qa_report" for artifact in artifacts)
        assert any(artifact.artifact_type == "dom_diagnostic" for artifact in artifacts)
        assert router.complete.await_count == 2  # Initial draft and static review only.
    finally:
        await coordinator.close()
        await manager.close()


async def test_qa_cancellation_propagates_without_generating_a_repair(
    settings, database, monkeypatch
):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox, good=True)
        router.complete = AsyncMock()
        monkeypatch.setattr(
            sandbox, "run_test_command", AsyncMock(side_effect=asyncio.CancelledError)
        )
        with pytest.raises(asyncio.CancelledError):
            await QASelfHealer(router).execute_task(
                task(sandbox.mission_id, "qa_self_healer"), sandbox, memory, AsyncMock()
            )
        router.complete.assert_not_awaited()


async def test_test_tampering_is_detected_before_diagnosis_or_patch(
    settings, database, monkeypatch
):
    async with WorkspaceSandbox(
        uuid4(), root=settings.workspace_root, sandbox_mode="guarded"
    ) as sandbox:
        router, memory, _, _ = await setup_project(settings, database, sandbox, good=True)
        router.complete = AsyncMock()
        qa = QASelfHealer(router)

        async def tamper(*args):
            await sandbox.write_file("tests/test_app.py", "def test_fake():\n    assert True\n")
            return []

        monkeypatch.setattr(qa, "_checks", tamper)
        with pytest.raises(DepartmentExecutionError):
            await qa.execute_task(
                task(sandbox.mission_id, "qa_self_healer"), sandbox, memory, AsyncMock()
            )
        router.complete.assert_not_awaited()
