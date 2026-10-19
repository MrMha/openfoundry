"""Authenticated discovery, truthful isolation status, and extension metadata."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.api.routes_plugins import SandboxInspector, plugin_metadata
from backend.app.core.workspace_sandbox import SandboxUnavailableError, WorkspaceSandbox
from backend.app.departments.base_department import (
    BaseDepartment,
    load_department_plugins,
    register_department,
)
from backend.app.departments.growth_career import GrowthCareer
from backend.app.main import create_app

CORE = {"chief_of_staff", "researcher", "engineer", "qa_self_healer", "growth_career"}


def test_catalog_lists_all_plugins_capabilities_and_no_credentials(settings):
    config = settings.model_copy(update={"sandbox_mode": "guarded"})
    with TestClient(create_app(config)) as client:
        response = client.get("/api/plugins")
        assert response.status_code == 200
        catalog = response.json()
        plugins = {plugin["name"]: plugin for plugin in catalog["plugins"]}
        assert CORE <= plugins.keys()
        assert all(
            plugins[name]["enabled"] and plugins[name]["builtin"] and plugins[name]["capabilities"]
            for name in CORE
        )
        assert plugins["growth_career"]["class_name"] == "GrowthCareer"
        assert plugins["growth"]["legacy_replacement"] == "growth_career"
        assert catalog["sandbox"]["configured_mode"] == "guarded"
        assert catalog["sandbox"]["execution_mode"] == "guarded"
        assert catalog["sandbox"]["isolation_available"] is None
        assert catalog["sandbox"]["capability_probe_performed"] is False
        assert catalog["sandbox"]["max_command_seconds"] == 60
        assert catalog["sandbox"]["guarded_execution_is_os_isolation"] is False
        assert "unit-test-key" not in response.text
        assert str(settings.data_dir) not in response.text
        assert client.get("/openapi.json").json()["paths"]["/api/plugins"]["get"]["responses"][
            "200"
        ]


def test_catalog_authentication_and_origins_precede_capability_probe(settings, monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(SandboxInspector, "inspect", probe)
    config = settings.model_copy(update={"api_token": SecretStr("catalog-token")})
    with TestClient(create_app(config)) as client:
        assert client.get("/api/plugins").status_code == 401
        assert (
            client.get("/api/plugins", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        assert (
            client.get(
                "/api/plugins",
                headers={
                    "Authorization": "Bearer catalog-token",
                    "Origin": "https://untrusted.example",
                },
            ).status_code
            == 401
        )
    probe.assert_not_awaited()


@pytest.mark.parametrize(
    "mode,available,expected",
    [
        ("auto", True, "isolated"),
        ("auto", False, "guarded"),
        ("strict", True, "isolated"),
        ("strict", False, "unavailable"),
    ],
)
async def test_catalog_probes_kernel_in_temporary_workspace_and_caches_result(
    settings, database, monkeypatch, mode, available, expected
):
    checked = []

    async def probe(sandbox):
        checked.append(sandbox.root)
        sandbox._isolation_reason = None if available else "User namespaces are blocked"
        return available

    monkeypatch.setattr(WorkspaceSandbox, "_probe_isolation", probe)
    inspector = SandboxInspector(settings.model_copy(update={"sandbox_mode": mode}))
    results = await asyncio.gather(*(inspector.inspect() for _ in range(5)))
    assert len(checked) == 1
    assert results[0].execution_mode == expected
    assert results[0].isolation_available is available
    assert all(result == results[0] for result in results)
    assert results[0].capability_probe_performed
    assert not checked[0].exists()
    assert not list(settings.data_dir.glob("sandbox-probe-*"))
    assert not list(settings.workspace_root.iterdir())


async def test_status_probe_errors_are_unavailable_and_do_not_expose_raw_errors(
    settings, database, monkeypatch
):
    monkeypatch.setattr(
        WorkspaceSandbox,
        "_probe_isolation",
        AsyncMock(side_effect=SandboxUnavailableError("secret /host/private/path")),
    )
    result = await SandboxInspector(settings).inspect()
    assert result.execution_mode == "unavailable" and result.isolation_available is None
    assert "secret" not in result.model_dump_json() and "/host" not in result.model_dump_json()


async def test_status_probe_cancellation_is_not_cached_as_success(settings, database, monkeypatch):
    probe = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(WorkspaceSandbox, "_probe_isolation", probe)
    inspector = SandboxInspector(settings)
    with pytest.raises(asyncio.CancelledError):
        await inspector.inspect()
    assert inspector._status is None
    probe.side_effect = None
    probe.return_value = True
    assert (await inspector.inspect()).execution_mode == "isolated"
    assert probe.await_count == 2


def test_third_party_plugin_metadata_and_disabled_registry_entries():
    custom_name = "catalog_test_" + uuid4().hex

    class Custom(BaseDepartment):
        name = custom_name
        role_description = "Draft a custom deployment audit"
        plugin_version = "1.2.3"
        capabilities = ("deployment_audit",)

        async def execute_task(self, task, sandbox, memory, emit_event):
            return {"summary": "Custom audit"}

    register_department(Custom)
    records = {record.name: record for record in plugin_metadata({})}
    assert records[custom_name].version == "1.2.3"
    assert records[custom_name].capabilities == ("deployment_audit",)
    assert not records[custom_name].builtin and not records[custom_name].enabled
    load_department_plugins()
    assert {record.name: record for record in plugin_metadata({})}[
        "growth_career"
    ].class_name == GrowthCareer.__name__


@pytest.mark.parametrize(
    "capabilities,version",
    [(("not valid",), None), (("same", "same"), None), (("valid",), ""), (("valid",), 123)],
)
def test_invalid_capability_or_version_metadata_fails_registration(capabilities, version):
    class Invalid(BaseDepartment):
        name = "invalid_metadata_" + uuid4().hex
        role_description = "Test invalid metadata"

        async def execute_task(self, task, sandbox, memory, emit_event):
            return {}

    Invalid.capabilities = capabilities
    Invalid.plugin_version = version
    with pytest.raises(ValueError):
        register_department(Invalid)
