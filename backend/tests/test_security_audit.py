"""Adversarial publication-audit regressions; all credentials are ephemeral sentinels."""

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect
from uvicorn.logging import AccessFormatter

from backend.app.core.research_worker import ResearchWorkerError, run_research_worker
from backend.app.core.security_logging import install_log_redaction
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments import _qa_probe_worker as probes
from backend.app.departments import _research_web as web
from backend.app.departments._research_documents import parse_document
from backend.app.departments.engineering_contracts import VerificationPlan
from backend.app.main import create_app

SENSITIVE_NAMES = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "OPENFOUNDRY_API_TOKEN",
    "CODEX_AUTH_TOKEN",
    "AZURE_FEDERATED_TOKEN_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTEST_ADDOPTS",
    "LD_PRELOAD",
    "NODE_OPTIONS",
)


@pytest.mark.parametrize("mode", ["guarded", "auto"])
async def test_real_agent_test_child_cannot_inherit_host_credentials(tmp_path, monkeypatch, mode):
    for name in SENSITIVE_NAMES:
        monkeypatch.setenv(name, "audit-sentinel-" + uuid4().hex)
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode=mode) as sandbox:
        if mode == "auto":
            monkeypatch.setattr(sandbox, "_probe_isolation", AsyncMock(return_value=False))
        await sandbox.write_file(
            "test_environment.py",
            "import os\ndef test_environment():\n"
            + f"    assert not (set({SENSITIVE_NAMES!r}) & set(os.environ))\n",
        )
        result = await sandbox.run_test_command(["pytest", "-q", "test_environment.py"])
        assert result.succeeded, result.stdout + result.stderr
        assert result.execution_mode == "guarded"


async def test_browser_worker_and_its_sdk_grandchild_receive_no_host_credentials(monkeypatch):
    original = asyncio.create_subprocess_exec
    for name in SENSITIVE_NAMES:
        monkeypatch.setenv(name, "audit-sentinel-" + uuid4().hex)
    child_check = f"import os; assert not (set({SENSITIVE_NAMES!r}) & set(os.environ))"
    script = (
        "import sys,subprocess,json; sys.stdin.buffer.read(); "
        + child_check
        + f"; subprocess.run([sys.executable, '-I', '-c', {child_check!r}], check=True); "
        + "print(json.dumps('<main>Isolated rendering</main>'))"
    )

    async def substituted_worker(*args, **kwargs):
        assert args[:3] == (sys.executable, "-I", "-m")
        assert args[3] == "backend.app.departments._research_browser"
        assert not set(SENSITIVE_NAMES) & set(kwargs["env"])
        return await original(sys.executable, "-I", "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substituted_worker)
    assert await web.browser_html("https://example.com/") == "<main>Isolated rendering</main>"


async def test_document_worker_ignores_host_pythonpath_startup_injection(tmp_path, monkeypatch):
    marker = tmp_path / "startup-was-executed"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    parsed = await parse_document(b"Audited document", "note.txt", prefer_docling=False)
    assert parsed.text == "Audited document"
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["timeout", "overflow"])
async def test_research_worker_kills_bounded_subprocesses(monkeypatch, failure):
    original = asyncio.create_subprocess_exec
    children = []
    script = (
        "import sys,time; sys.stdin.buffer.read(); time.sleep(30)"
        if failure == "timeout"
        else "import sys; sys.stdin.buffer.read(); print('x'*4096)"
    )

    async def substituted_worker(*args, **kwargs):
        child = await original(sys.executable, "-I", "-c", script, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substituted_worker)
    with pytest.raises(TimeoutError if failure == "timeout" else ResearchWorkerError):
        await run_research_worker(
            "backend.app.departments._research_browser",
            b"{}",
            timeout_seconds=0.1 if failure == "timeout" else 5,
            max_output_bytes=64,
        )
    assert children and children[0].returncode is not None


def test_every_mission_inspection_and_mutation_route_requires_operator_auth(settings):
    secured = settings.model_copy(update={"api_token": SecretStr("audit-" + uuid4().hex)})
    app = create_app(secured)
    checked = []
    with TestClient(app) as client:
        for template, methods in app.openapi()["paths"].items():
            if not template.startswith("/api/"):
                continue
            path = template.replace("{mission_id}", str(uuid4())).replace("{task_id}", str(uuid4()))
            for method in methods:
                if method not in {"get", "post"}:
                    continue
                response = client.request(
                    method.upper(),
                    path,
                    params={"path": "SPEC.md"},
                    json={"goal": "Audit authorization"} if path == "/api/missions" else {},
                )
                assert response.status_code == 401, (method, path, response.text)
                checked.append((method, template))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/missions/{uuid4()}"):
                pytest.fail("Anonymous WebSocket accepted")
        assert client.get("/health").status_code == 200
    assert len(checked) >= 10


def test_tokenless_authorization_is_limited_to_loopback_clients(settings):
    with TestClient(create_app(settings), client=("198.51.100.5", 45000)) as client:
        assert client.get("/api/missions").status_code == 401
        assert client.post("/api/missions/demo").status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/missions/{uuid4()}"):
                pytest.fail("Remote tokenless WebSocket accepted")


async def test_file_inspection_never_reads_traversal_links_or_absolute_paths(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "backend.app.core.orchestrator.MissionOrchestrator._enqueue", lambda *a: None
    )
    outside = tmp_path / "private-file.txt"
    outside.write_text("audit-private-evidence")
    with TestClient(create_app(settings)) as client:
        mission_id = client.post("/api/missions", json={"goal": "Inspect paths"}).json()["id"]
        async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as sandbox:
            await sandbox.write_file("safe.txt", "Public test note")
            (sandbox.root / "escape").symlink_to(tmp_path, target_is_directory=True)
            os.link(outside, sandbox.root / "hardlink")
            for path in (
                "../private-file.txt",
                str(outside),
                "escape/private-file.txt",
                "hardlink",
            ):
                response = client.get(
                    f"/api/missions/{mission_id}/files/content", params={"path": path}
                )
                assert response.status_code == 400
                assert "audit-private-evidence" not in response.text
            assert (
                client.get(
                    f"/api/missions/{mission_id}/files/content", params={"path": "safe.txt"}
                ).json()["content"]
                == "Public test note"
            )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://169.254.169.254/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://10.0.0.1/",
        "http://[fe80::1]/",
    ],
)
def test_research_and_qa_reject_non_http_and_metadata_destinations(url):
    with pytest.raises(ValueError):
        web.public_url(url)
    with pytest.raises(ValueError):
        VerificationPlan(base_url=url)


async def test_research_and_qa_dns_rebinding_to_metadata_is_blocked(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (2, 1, 6, "", ("169.254.169.254", 80)),
            ]
        ),
    )
    with pytest.raises(web.SourceError):
        await web.resolve_public("public-looking.example", 80)
    with pytest.raises(ValueError):
        await probes._addresses(httpx.URL("http://public-looking.example/"))


def test_sdk_access_websocket_and_exception_logs_redact_credentials(settings, monkeypatch, caplog):
    credential = "audit-provider-" + uuid4().hex
    cloud_token = "audit-cloud-" + uuid4().hex
    bearer = "audit-bearer-" + uuid4().hex
    query_secret = "audit-query-" + uuid4().hex
    encoded = "".join(f"%{ord(character):02X}" for character in query_secret)
    monkeypatch.setenv("AWS_SESSION_TOKEN", cloud_token)
    # Earlier real-server integration tests may set Uvicorn's explicit logger levels
    # and disable propagation. Capture emitted access/error records independently.
    for name in ("uvicorn.access", "uvicorn.error"):
        target = logging.getLogger(name)
        monkeypatch.setattr(target, "level", logging.INFO)
        monkeypatch.setattr(target, "propagate", True)
        monkeypatch.setattr(target, "disabled", False)
    install_log_redaction(settings.model_copy(update={"openai_api_key": SecretStr(credential)}))
    with caplog.at_level(logging.INFO):
        logger = logging.getLogger("audit.sdk")
        logger.warning("provider error: %s; cloud: %s; Bearer %s", credential, cloud_token, bearer)
        logging.getLogger("uvicorn.error").info("websocket protocol bearer.%s", bearer)
        logging.getLogger("uvicorn.error").info("websocket /ws/missions/test?%%74oken=%s", encoded)
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:12345",
            "GET",
            "/api/missions?token=" + query_secret,
            "1.1",
            401,
        )
        try:
            raise RuntimeError("provider failed: " + credential)
        except RuntimeError:
            logger.exception("provider exception")
    assert all(
        secret not in caplog.text for secret in (credential, cloud_token, bearer, query_secret)
    )
    access = next(record for record in caplog.records if record.name == "uvicorn.access")
    assert query_secret not in AccessFormatter().format(access)
    assert "[REDACTED]" in caplog.text
    assert encoded not in caplog.text


def test_invalid_request_does_not_echo_accidentally_submitted_secrets(settings):
    marker = "audit-accidental-input-" + uuid4().hex
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/missions", json={"goal": "Audit", "OPENAI_API_KEY": marker})
    assert response.status_code == 422
    assert marker not in response.text
    assert all("input" not in item and "ctx" not in item for item in response.json()["detail"])
