import asyncio
import json
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.core.workspace_sandbox import (
    CommandNotAllowedError,
    SandboxError,
    SandboxUnavailableError,
    UnsafePathError,
    WorkspaceSandbox,
)


@pytest.fixture
async def sandbox(tmp_path: Path) -> AsyncIterator[WorkspaceSandbox]:
    async with WorkspaceSandbox(
        uuid4(), root=tmp_path / "workspaces", allow_host_execution=True
    ) as sandbox:
        yield sandbox


async def test_mission_files_are_isolated_and_persist(sandbox: WorkspaceSandbox) -> None:
    await sandbox.write_file("src/app.py", "answer = 42\n")
    assert await sandbox.read_file("src/app.py") == "answer = 42\n"
    await sandbox.write_file("src/app.py", "answer = 43\n")
    assert await sandbox.read_file("src/app.py") == "answer = 43\n"
    async with WorkspaceSandbox(sandbox.mission_id, root=sandbox.root.parent) as reopened:
        assert await reopened.read_file("src/app.py") == "answer = 43\n"
    async with WorkspaceSandbox(uuid4(), root=sandbox.root.parent) as other:
        assert not (other.root / "src/app.py").exists()
    assert not list((sandbox.root / "src").glob(".openfoundry-write-*"))


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "a/../../secret",
        "/etc/passwd",
        r"C:\secret",
        ".git/config",
        "a/.GIT/config",
        "a//b",
        "a/./b",
        "a\x00b",
        "",
    ],
)
async def test_unsafe_paths_are_rejected(sandbox: WorkspaceSandbox, path: str) -> None:
    with pytest.raises(UnsafePathError):
        await sandbox.write_file(path, "unsafe")
    with pytest.raises(UnsafePathError):
        await sandbox.read_file(path)


async def test_symlinks_hardlinks_and_special_files_are_blocked(
    sandbox: WorkspaceSandbox, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("private data")
    (sandbox.root / "link").symlink_to(outside)
    (sandbox.root / "parent").symlink_to(tmp_path, target_is_directory=True)
    os.link(outside, sandbox.root / "hardlink")
    os.mkfifo(sandbox.root / "fifo")
    for path in ("link", "parent/outside.txt", "hardlink", "fifo"):
        with pytest.raises(UnsafePathError):
            await sandbox.read_file(path)
        with pytest.raises(UnsafePathError):
            await sandbox.write_file(path, "overwrite")
    assert outside.read_text() == "private data"


def test_symlinked_mission_directory_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    mission_id = uuid4()
    (root / str(mission_id)).symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        WorkspaceSandbox(mission_id, root=root)


async def test_root_descriptor_stays_pinned_after_rename(
    sandbox: WorkspaceSandbox, tmp_path: Path
) -> None:
    original = sandbox.root
    moved = original.with_name("moved")
    original.rename(moved)
    original.symlink_to(tmp_path)
    await sandbox.write_file("safe.txt", "pinned root")
    assert (moved / "safe.txt").read_text() == "pinned root"
    assert not (tmp_path / "safe.txt").exists()


async def test_file_size_limits_and_closed_lifecycle(tmp_path: Path) -> None:
    sandbox = WorkspaceSandbox(uuid4(), root=tmp_path, max_file_bytes=4)
    await sandbox.write_file("small.txt", "1234")
    with pytest.raises(UnsafePathError):
        await sandbox.write_file("small.txt", "12345")
    assert await sandbox.read_file("small.txt") == "1234"
    (sandbox.root / "oversized.txt").write_text("12345")
    with pytest.raises(UnsafePathError):
        await sandbox.read_file("oversized.txt")
    sandbox.close()
    sandbox.close()
    with pytest.raises(SandboxError, match="closed"):
        await sandbox.read_file("small.txt")


async def test_git_initialization_branches_and_untracked_diffs(sandbox: WorkspaceSandbox) -> None:
    await sandbox.initialize_git()
    await sandbox.initialize_git()
    await sandbox.create_branch("engineering/feature-1")
    assert (
        await sandbox._git("branch", "--show-current")
    ).stdout.strip() == "engineering/feature-1"
    await sandbox.write_file("src/app.py", "answer = 42\n")
    diff = await sandbox.git_diff()
    assert "--- /dev/null" in diff
    assert "+++ b/src/app.py" in diff
    assert "+answer = 42" in diff
    assert await sandbox.git_diff(include_untracked=False) == ""
    await sandbox._git("add", "--", "src/app.py")
    await sandbox._git("commit", "--no-verify", "-m", "Add app")
    await sandbox.write_file("src/app.py", "answer = 43\n")
    diff = await sandbox.git_diff()
    assert "-answer = 42" in diff
    assert "+answer = 43" in diff
    await sandbox.create_branch("qa/review", checkout=False)
    assert (
        await sandbox._git("branch", "--show-current")
    ).stdout.strip() == "engineering/feature-1"


@pytest.mark.parametrize("content", ["", "no trailing newline", "with newline\n"])
@pytest.mark.parametrize("name", ["new.txt", "spaced name.txt", 'quote".txt', "résumé.txt"])
async def test_untracked_diffs_are_valid_git_patches(
    sandbox: WorkspaceSandbox, content: str, name: str
) -> None:
    await sandbox.write_file(name, content)
    patch = await sandbox.git_diff()
    await sandbox.write_file("change.patch", patch)
    result = await sandbox._git("apply", "--check", "--reverse", "change.patch")
    assert result.succeeded, result.stderr


async def test_git_metadata_symlinks_and_corrupt_head_are_rejected(
    sandbox: WorkspaceSandbox, tmp_path: Path
) -> None:
    (sandbox.root / ".git").symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        await sandbox.initialize_git()
    (sandbox.root / ".git").unlink()
    (sandbox.root / ".git").mkdir()
    await sandbox.initialize_git()
    head = sandbox.root / ".git/HEAD"
    head.write_text("invalid HEAD\n")
    with pytest.raises(SandboxError):
        await sandbox.initialize_git()
    assert head.read_text() == "invalid HEAD\n"


async def test_git_does_not_execute_configured_filters_or_hooks(sandbox: WorkspaceSandbox) -> None:
    await sandbox.initialize_git()
    hook = sandbox.root / ".git/hooks/pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\ntouch hook-ran.txt\n")
    hook.chmod(0o700)
    await sandbox.write_file("app.txt", "first\n")
    assert (await sandbox._git("add", "--", "app.txt")).succeeded
    assert (await sandbox._git("commit", "-m", "Baseline")).succeeded
    assert not (sandbox.root / "hook-ran.txt").exists()
    configuration = sandbox.root / ".git/config"
    with configuration.open("a") as stream:
        stream.write(
            '[filter "dangerous"]\n\tclean = "touch filter-ran.txt; cat"\n\trequired = true\n'
        )
    await sandbox.write_file(".gitattributes", "*.txt filter=dangerous\n")
    await sandbox.write_file("app.txt", "second\n")
    assert "+second" in await sandbox.git_diff()
    assert not (sandbox.root / "filter-ran.txt").exists()


async def test_cancellation_while_spawning_does_not_orphan_processes(
    sandbox: WorkspaceSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sandbox.write_file(
        "test_spawning.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    )
    started = asyncio.Event()
    release = asyncio.Event()
    original = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def delayed_spawn(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await original(*args, **kwargs)
        children.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(sandbox.run_test_command(["pytest", "test_spawning.py"]))
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(children[0].pid, 0)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "branch", ["--help", "../outside", "a..b", "bad branch", "name.lock", "a//b"]
)
async def test_invalid_branch_names(sandbox: WorkspaceSandbox, branch: str) -> None:
    with pytest.raises(ValueError):
        await sandbox.create_branch(branch)


@pytest.mark.parametrize(
    "command",
    [
        ["sh", "-c", "id"],
        ["python", "-c", "print(1)"],
        ["npm", "install"],
        ["npm", "test", "--", "--unsafe"],
        ["pytest", "-p", "evil_plugin"],
        ["pytest", "../outside"],
        ["pytest", "--basetemp=/tmp/outside"],
        ["ruff", "check", "--config=/tmp/evil.toml"],
        ["ruff", "check", "--fix"],
        ["flake8", "--append-config=/tmp/evil"],
        "pytest; id",
        [],
    ],
)
async def test_commands_and_options_are_allowlisted(
    sandbox: WorkspaceSandbox, command: list[str]
) -> None:
    with pytest.raises(CommandNotAllowedError):
        await sandbox.run_test_command(command)


async def test_python_test_commands_run_and_report_real_failures(sandbox: WorkspaceSandbox) -> None:
    await sandbox.write_file("test_example.py", "def test_answer():\n    assert 6 * 7 == 42\n")
    result = await sandbox.run_test_command(["pytest", "-q", "test_example.py"])
    assert result.succeeded, result.stderr
    assert "1 passed" in result.stdout
    result = await sandbox.run_test_command(["ruff", "check", "test_example.py"])
    assert result.succeeded, result.stderr
    result = await sandbox.run_test_command(["flake8", "test_example.py"])
    assert result.succeeded, result.stdout + result.stderr
    await sandbox.write_file("test_example.py", "def test_failure():\n    assert False\n")
    result = await sandbox.run_test_command(["pytest", "-q", "test_example.py"])
    assert not result.succeeded
    assert result.return_code == 1
    assert "1 failed" in result.stdout


async def test_npm_test_runs_without_inheriting_keys_or_pretest_hooks(
    sandbox: WorkspaceSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js is required to exercise npm test")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    await sandbox.write_file(
        "package.json",
        json.dumps(
            {
                "scripts": {
                    "pretest": "node -e \"require('fs').writeFileSync('unexpected.txt','ran')\"",
                    "test": 'node -e "if(process.env.OPENAI_API_KEY)process.exit(1);'
                    "console.log('npm test passed')\"",
                }
            }
        ),
    )
    result = await sandbox.run_test_command(["npm", "test"])
    assert result.succeeded, result.stderr
    assert "npm test passed" in result.stdout
    assert not (sandbox.root / "unexpected.txt").exists()


async def test_execution_timeout_kills_process_group(tmp_path: Path) -> None:
    async with WorkspaceSandbox(
        uuid4(), root=tmp_path, timeout_seconds=1, allow_host_execution=True
    ) as sandbox:
        await sandbox.write_file(
            "test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n"
        )
        started = time.monotonic()
        result = await sandbox.run_test_command(["pytest", "-q", "test_slow.py"])
        assert result.timed_out
        assert not result.succeeded
        assert time.monotonic() - started < 5


async def test_output_limit_stops_noisy_command(tmp_path: Path) -> None:
    async with WorkspaceSandbox(
        uuid4(), root=tmp_path, allow_host_execution=True, max_output_bytes=1024
    ) as sandbox:
        await sandbox.write_file(
            "package.json",
            json.dumps(
                {
                    "scripts": {
                        "test": "node -e \"process.stdout.write('x'.repeat(1000000));"
                        'setTimeout(()=>{},30000)"'
                    }
                }
            ),
        )
        result = await sandbox.run_test_command(["npm", "test"])
        assert result.output_limit_exceeded
        assert not result.succeeded
        assert len(result.stdout.encode()) <= 1024


async def test_cancellation_reaps_the_test_process(sandbox: WorkspaceSandbox) -> None:
    await sandbox.write_file(
        "test_cancel.py",
        "import os\nimport time\nfrom pathlib import Path\n\ndef test_cancel():\n"
        "    Path('running.pid').write_text(str(os.getpid()))\n    time.sleep(30)\n",
    )
    task = asyncio.create_task(sandbox.run_test_command(["pytest", "-q", "test_cancel.py"]))
    try:
        async with asyncio.timeout(5):
            for _ in range(250):
                if (sandbox.root / "running.pid").exists():
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Test process did not start")
        pid = int((sandbox.root / "running.pid").read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_symlink_working_directory_is_rejected(
    sandbox: WorkspaceSandbox, tmp_path: Path
) -> None:
    (sandbox.root / "outside").symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        await sandbox.run_test_command(["pytest"], cwd="outside")


@pytest.mark.parametrize("timeout", [0, -1, 61, float("inf"), float("nan")])
def test_timeout_cannot_exceed_sixty_seconds(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError):
        WorkspaceSandbox(uuid4(), root=tmp_path, timeout_seconds=timeout)


async def test_strict_execution_never_falls_back_to_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with WorkspaceSandbox(uuid4(), root=tmp_path, sandbox_mode="strict") as sandbox:
        await sandbox.write_file(
            "test_forbidden.py",
            "from pathlib import Path\nPath('executed.txt').write_text('ran')\n",
        )
        original = shutil.which

        def without_bwrap(command: str, **kwargs: str) -> str | None:
            return None if command == "bwrap" else original(command, **kwargs)

        monkeypatch.setattr(shutil, "which", without_bwrap)
        with pytest.raises(SandboxUnavailableError, match="Bubblewrap"):
            await sandbox.run_test_command(["pytest", "test_forbidden.py"])
        assert not (sandbox.root / "executed.txt").exists()
