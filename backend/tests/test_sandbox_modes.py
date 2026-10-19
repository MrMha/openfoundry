"""Auto/strict selection, portable guards, and local committed-change capabilities."""

import asyncio
import os
import py_compile
import shutil
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.config import Settings
from backend.app.core.workspace_sandbox import (
    CommandNotAllowedError,
    CommandResult,
    SandboxUnavailableError,
    UnsafePathError,
    WorkspaceSandbox,
)


def no_bwrap(monkeypatch):
    original = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda command, **kwargs: None if command == "bwrap" else original(command, **kwargs),
    )


async def test_auto_falls_back_when_bwrap_is_missing_without_inheriting_credentials(
    tmp_path, monkeypatch
):
    no_bwrap(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PYTHONPATH", "/outside/path")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p unsafe_plugin")
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="auto") as sandbox:
        await sandbox.write_file(
            "test_env.py",
            "import os\n\ndef test_env():\n"
            "    assert 'OPENAI_API_KEY' not in os.environ\n"
            "    assert 'PYTHONPATH' not in os.environ\n"
            "    assert 'PYTEST_ADDOPTS' not in os.environ\n",
        )
        result = await sandbox.run_test_command(["pytest", "-q", "test_env.py"])
        assert result.succeeded and result.execution_mode == "guarded"
        assert "Bubblewrap" in result.fallback_reason


async def test_auto_probe_detects_blocked_user_namespaces_and_is_cached(tmp_path, monkeypatch):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.initialize_git()
        calls = []
        real_run = sandbox._run_process

        async def blocked_probe(argv, **kwargs):
            if kwargs.get("display_command") == ("bwrap", "--probe"):
                calls.append(tuple(argv))
                return CommandResult(("bwrap",), 1, "", "bwrap: setting up uid map: denied", 0.01)
            return await real_run(argv, **kwargs)

        original_which = shutil.which
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, **kwargs: (
                "/usr/bin/bwrap" if name == "bwrap" else original_which(name, **kwargs)
            ),
        )
        monkeypatch.setattr(sandbox, "_run_process", blocked_probe)
        await sandbox.write_file("test_pass.py", "def test_pass():\n    assert True\n")
        for _ in range(2):
            result = await sandbox.run_test_command(["pytest", "-q", "test_pass.py"])
            assert result.succeeded and result.execution_mode == "guarded"
        assert len(calls) == 1
        assert calls[0][-1] == "/usr/bin/true"


async def test_supported_bwrap_is_selected_without_host_execution(tmp_path, monkeypatch):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.initialize_git()
        monkeypatch.setattr(sandbox, "_probe_isolation", AsyncMock(return_value=True))
        monkeypatch.setattr(sandbox, "_isolated_argv", lambda *args: ("controlled-bwrap",))
        runner = AsyncMock(return_value=CommandResult(("pytest",), 0, "passed", "", 0.01))
        monkeypatch.setattr(sandbox, "_run_process", runner)
        result = await sandbox.run_test_command(["pytest"])
        assert result.execution_mode == "isolated" and result.succeeded
        assert runner.call_args.args[0] == ("controlled-bwrap",)
        runner.assert_awaited_once()


async def test_failed_isolated_command_is_never_replayed_on_host(tmp_path, monkeypatch):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.initialize_git()
        monkeypatch.setattr(sandbox, "_probe_isolation", AsyncMock(return_value=True))
        monkeypatch.setattr(sandbox, "_isolated_argv", lambda *args: ("controlled-bwrap",))
        runner = AsyncMock(return_value=CommandResult(("pytest",), 1, "failure", "", 0.01))
        monkeypatch.setattr(sandbox, "_run_process", runner)
        result = await sandbox.run_test_command(["pytest"])
        assert result.execution_mode == "isolated" and not result.succeeded
        runner.assert_awaited_once()
        runner.return_value = CommandResult(("pytest",), 1, "", "bwrap: runtime failure", 0.01)
        with pytest.raises(SandboxUnavailableError, match="not retried"):
            await sandbox.run_test_command(["pytest"])
        assert runner.await_count == 2


async def test_strict_mode_does_not_run_project_code_when_isolation_unavailable(
    tmp_path, monkeypatch
):
    no_bwrap(monkeypatch)
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="strict") as sandbox:
        runner = AsyncMock()
        monkeypatch.setattr(sandbox, "_run_process", runner)
        with pytest.raises(SandboxUnavailableError):
            await sandbox.run_test_command(["pytest"])
        runner.assert_not_awaited()


@pytest.mark.parametrize("mode", ["invalid", "AUTO", ""])
def test_invalid_sandbox_modes_fail_configuration(tmp_path, mode):
    with pytest.raises(ValueError):
        WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode=mode)
    with pytest.raises(ValueError):
        Settings(sandbox_mode=mode)


def test_environment_mode_loading_and_legacy_explicit_host_mode(tmp_path):
    assert Settings.from_env({"OPENFOUNDRY_SANDBOX_MODE": "strict"}).sandbox_mode == "strict"
    with pytest.raises(ValueError):
        WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="strict", allow_host_execution=True)
    sandbox = WorkspaceSandbox(uuid4(), root=tmp_path, allow_host_execution=True)
    assert sandbox.sandbox_mode == "guarded"
    sandbox.close()


async def test_guarded_mode_rejects_symlink_test_arguments_and_non_allowlisted_commands(tmp_path):
    external = tmp_path / "outside.py"
    external.write_text("raise RuntimeError('must not execute')")
    async with WorkspaceSandbox(
        uuid4(), root=tmp_path / "workspaces", sandbox_mode="guarded"
    ) as sandbox:
        (sandbox.root / "test_escape.py").symlink_to(external)
        with pytest.raises(UnsafePathError):
            await sandbox.run_test_command(["pytest", "test_escape.py"])
        with pytest.raises(CommandNotAllowedError):
            await sandbox.run_test_command(["sh", "-c", "id"])


async def test_auto_timeout_is_still_bounded_and_reaped(tmp_path, monkeypatch):
    no_bwrap(monkeypatch)
    async with WorkspaceSandbox(uuid4(), root=tmp_path, timeout_seconds=0.5) as sandbox:
        await sandbox.write_file(
            "test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n"
        )
        result = await sandbox.run_test_command(["pytest", "test_slow.py"])
        assert result.timed_out and not result.succeeded
        assert result.execution_mode == "guarded" and result.duration_seconds < 5


async def test_same_size_same_timestamp_source_patch_does_not_use_old_bytecode(tmp_path):
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="guarded") as sandbox:
        await sandbox.write_file("value.py", "VALUE = 1\n")
        py_compile.compile(str(sandbox.root / "value.py"), doraise=True)
        original_stat = (sandbox.root / "value.py").stat()
        await sandbox.write_file("value.py", "VALUE = 2\n")
        os.utime(
            sandbox.root / "value.py", ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns)
        )
        await sandbox.write_file(
            "test_value.py", "from value import VALUE\n\ndef test_value():\n    assert VALUE == 2\n"
        )
        result = await sandbox.run_test_command(["pytest", "-q", "test_value.py"])
        assert result.succeeded, result.stdout + result.stderr


async def test_portable_file_and_git_execution_paths_without_proc_descriptors(
    tmp_path, monkeypatch
):
    # Exercise the portable implementation on this host without claiming native OS coverage.
    with monkeypatch.context() as context:
        context.setattr(os, "supports_dir_fd", set())
        sandbox = WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="auto")
    sandbox._fd_paths = False
    try:
        await sandbox.write_file("src/value.py", "VALUE = 42\n")
        assert await sandbox.read_file("src/value.py") == "VALUE = 42\n"
        await sandbox.create_branch("engineer/portable")
        commit = await sandbox.commit_files(["src/value.py"], "Add portable module")
        assert len(commit) == 40
        await sandbox.write_file("test_value.py", "def test_answer():\n    assert 6 * 7 == 42\n")
        result = await sandbox.run_test_command(["pytest", "-q", "test_value.py"])
        assert result.succeeded and result.execution_mode == "guarded"
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (sandbox.root / "link.txt").symlink_to(outside)
        with pytest.raises(UnsafePathError):
            await sandbox.read_file("link.txt")
        with pytest.raises(UnsafePathError):
            await sandbox.write_file("link.txt", "overwrite")
    finally:
        sandbox.close()
    with pytest.raises(RuntimeError, match="closed"):
        await sandbox.read_file("src/value.py")


async def test_local_git_commit_preserves_unrelated_staged_work_and_disables_filters(tmp_path):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.initialize_git()
        await sandbox.write_file("unrelated.txt", "existing staged work\n")
        await sandbox._git("add", "--", "unrelated.txt")
        await sandbox.write_file("src/app.py", "answer = 42\n")
        await sandbox.create_branch("engineer/feature")
        commit = await sandbox.commit_files(["src/app.py"], "Implement app")
        files = (await sandbox._git("show", "--format=", "--name-only", commit)).stdout.splitlines()
        assert files == ["src/app.py"]
        assert (
            await sandbox._git("diff", "--cached", "--name-only")
        ).stdout.strip() == "unrelated.txt"


async def test_subprocess_probe_cancellation_does_not_cache_an_unproven_fallback(
    tmp_path, monkeypatch
):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.initialize_git()
        original = shutil.which
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, **kwargs: (
                "/usr/bin/bwrap" if name == "bwrap" else original(name, **kwargs)
            ),
        )
        monkeypatch.setattr(sandbox, "_run_process", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await sandbox._probe_isolation()
        assert sandbox._isolation_available is None


@pytest.mark.parametrize(
    "path", ["file.py:stream", "CON", "NUL.txt", "dir/LPT1", "module.py.", "module.py "]
)
async def test_portable_ambiguous_and_reserved_paths_are_blocked(tmp_path, path):
    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        with pytest.raises(UnsafePathError):
            await sandbox.write_file(path, "data")


@pytest.mark.parametrize(
    "content,name", [("", "empty.py"), ("no newline", "spaced name.txt"), ("value\n", 'quote".txt')]
)
async def test_engineering_unified_diffs_are_valid_reverse_git_patches(tmp_path, content, name):
    from backend.app.departments.engineer import unified_diff

    async with WorkspaceSandbox(uuid4(), root=tmp_path) as sandbox:
        await sandbox.write_file(name, content)
        patch = unified_diff(name, "", content, new_file=True)
        await sandbox.commit_files([name], "Add file")
        await sandbox.write_file("changes.patch", patch)
        result = await sandbox._git("apply", "--check", "--reverse", "changes.patch")
        assert result.succeeded, result.stderr


async def test_async_tests_and_src_layout_work_without_inherited_pythonpath(tmp_path):
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="guarded") as sandbox:
        await sandbox.write_file("src/calculator/__init__.py", "VALUE = 42\n")
        await sandbox.write_file(
            "tests/test_async.py",
            "import asyncio\nfrom calculator import VALUE\n\n"
            "async def test_async():\n    await asyncio.sleep(0)\n    assert VALUE == 42\n",
        )
        result = await sandbox.run_test_command(["pytest", "-q", "tests"])
        assert result.succeeded, result.stdout + result.stderr


@pytest.mark.parametrize("local_config", [False, True])
async def test_pytest_uses_mission_configuration_and_imports_not_ancestor_settings(
    tmp_path, local_config
):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--invalid-service-option'\n"
        "testpaths = ['service_tests']\n"
    )
    async with WorkspaceSandbox(
        uuid4(), root=tmp_path / "mission workspaces", sandbox_mode="guarded"
    ) as sandbox:
        if local_config:
            await sandbox.write_file(
                "pytest.ini", "[pytest]\nmarkers = mission: local acceptance check\n"
            )
        await sandbox.write_file("app.py", "VALUE = 42\n")
        await sandbox.write_file("src/calculator/__init__.py", "EXPECTED = 42\n")
        marker = "@pytest.mark.mission\n" if local_config else ""
        await sandbox.write_file(
            "tests/test_app.py",
            "import pytest\nfrom app import VALUE\nfrom calculator import EXPECTED\n\n"
            + marker
            + "def test_application():\n    assert VALUE == EXPECTED\n",
        )
        result = await sandbox.run_test_command(["pytest", "-q", "--strict-markers"])
        assert result.succeeded, result.stdout + result.stderr
        assert "1 passed" in result.stdout
