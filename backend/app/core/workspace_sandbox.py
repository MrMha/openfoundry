"""Mission file capabilities, defensive Git operations, and bounded test execution.

POSIX file operations use directory descriptors; other hosts use portable path guards.
Auto mode probes Bubblewrap, then uses guarded host execution if isolation is unavailable.
Guarded execution is not an OS security boundary for generated programs.
"""

from __future__ import annotations

import asyncio
import difflib
import importlib.util
import os
import re
import shlex
import shutil
import signal
import stat
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from tempfile import gettempdir
from typing import Literal
from uuid import UUID, uuid4

from backend.app.core._sandbox_portable import PortablePathError, PortableWorkspace

SandboxMode = Literal["auto", "strict", "guarded"]

MAX_COMMAND_SECONDS = 60.0
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class SandboxError(RuntimeError):
    """Base class for mission workspace failures."""


class UnsafePathError(SandboxError, ValueError):
    """A path is not a regular, private workspace file or directory."""


class CommandNotAllowedError(SandboxError, ValueError):
    """A command or option is outside the supported test-command grammar."""


class SandboxUnavailableError(SandboxError):
    """The required isolation runtime or executable is unavailable."""


class GitOperationError(SandboxError):
    """A bounded local Git command failed."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_limit_exceeded: bool = False
    execution_mode: Literal["isolated", "guarded"] | None = None
    fallback_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0 and not self.timed_out and not self.output_limit_exceeded


class _OutputLimitError(Exception):
    """Internal signal to stop a process producing excessive output."""


class WorkspaceSandbox:
    """One mission under ``./workspaces/{mission_id}`` unless a root is supplied.

    ``root`` is operator configuration, never agent input. Only relative POSIX paths
    are accepted. Keep the instance open while using it, preferably with ``async with``.
    The file API rejects symlinks, hard links, special files, and access to Git metadata.
    Atomic replacement avoids truncating existing files on failed writes.
    """

    def __init__(
        self,
        mission_id: UUID | str,
        *,
        root: Path | str = Path("workspaces"),
        allow_host_execution: bool = False,
        sandbox_mode: SandboxMode = "auto",
        timeout_seconds: float = MAX_COMMAND_SECONDS,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        if sandbox_mode not in {"auto", "strict", "guarded"}:
            raise ValueError("sandbox_mode must be auto, strict, or guarded")
        if allow_host_execution and sandbox_mode == "strict":
            raise ValueError("strict mode cannot enable host execution")
        self.sandbox_mode: SandboxMode = "guarded" if allow_host_execution else sandbox_mode
        self._posix_files = os.open in os.supports_dir_fd and os.name == "posix"
        self._fd_paths = sys.platform == "linux" and Path("/proc/self/fd").is_dir()
        self._isolation_available: bool | None = None
        self._isolation_reason: str | None = None
        self._probe_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()
        if not 0 < timeout_seconds <= MAX_COMMAND_SECONDS:
            raise ValueError("Command timeout must be positive and at most 60 seconds")
        if not 0 < max_file_bytes <= 64 * 1024 * 1024:
            raise ValueError("File size limit must be between one byte and 64 MiB")
        if not 0 < max_output_bytes <= 16 * 1024 * 1024:
            raise ValueError("Output limit must be between one byte and 16 MiB per stream")
        self.mission_id = UUID(str(mission_id))
        self.root = Path(root).expanduser().absolute() / str(self.mission_id)
        self.allow_host_execution = allow_host_execution
        self.timeout_seconds = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.max_output_bytes = max_output_bytes
        self._fd_lock = threading.Lock()
        self._git_lock = asyncio.Lock()
        self._closed = False
        self.root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root_fd = -1
        if not self._posix_files:
            try:
                self._portable = PortableWorkspace(self.root, self.max_file_bytes)
            except (OSError, PortablePathError) as error:
                raise UnsafePathError("Cannot open a safe workspace directory") from error
            return
        try:
            parent_fd = os.open(self.root.parent, _DIRECTORY_FLAGS)
            try:
                try:
                    os.mkdir(str(self.mission_id), mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    # Opening below verifies an existing entry is a directory, not a link.
                    if not stat.S_ISDIR(
                        os.stat(
                            str(self.mission_id), dir_fd=parent_fd, follow_symlinks=False
                        ).st_mode
                    ):
                        raise UnsafePathError("Mission workspace is not a real directory") from None
                self._root_fd = os.open(str(self.mission_id), _DIRECTORY_FLAGS, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        except OSError as error:
            raise UnsafePathError("Cannot open a safe workspace directory") from error

    @staticmethod
    def _parts(path: str, *, directory: bool = False) -> tuple[str, ...]:
        if directory and path == ".":
            return ()
        parts = tuple(path.split("/"))
        if (
            not path
            or len(path) > 4096
            or "\\" in path
            or ":" in path
            or any(
                part.endswith((".", " "))
                or re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                for part in parts
            )
            or PureWindowsPath(path).drive
            or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts)
            or any(ord(character) < 32 for character in path)
        ):
            raise UnsafePathError("Paths must be canonical relative paths without Git metadata")
        return parts

    @contextmanager
    def _directory(self, parts: Sequence[str], *, create: bool = False) -> Iterator[int]:
        with self._fd_lock:
            if self._closed:
                raise SandboxError("Workspace is closed")
            descriptor = os.dup(self._root_fd)
        try:
            try:
                for part in parts:
                    if create:
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
            except OSError as error:
                raise UnsafePathError(
                    "Workspace path is missing, a symlink, or not a directory"
                ) from error
            yield descriptor
        finally:
            os.close(descriptor)

    def _read_bytes(self, relative_path: str) -> bytes:
        parts = self._parts(relative_path)
        if not self._posix_files:
            with self._fd_lock:
                if self._closed:
                    raise SandboxError("Workspace is closed")
                try:
                    return self._portable.read(parts)
                except PortablePathError as error:
                    raise UnsafePathError(str(error)) from error
        with self._directory(parts[:-1]) as parent:
            try:
                descriptor = os.open(
                    parts[-1],
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent,
                )
            except FileNotFoundError:
                raise
            except OSError as error:
                raise UnsafePathError("Cannot safely open workspace file") from error
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafePathError("Only regular files with a single hard link are readable")
                if info.st_size > self.max_file_bytes:
                    raise UnsafePathError("File exceeds the read limit")
                chunks = bytearray()
                while len(chunks) <= self.max_file_bytes:
                    chunk = os.read(descriptor, min(65_536, self.max_file_bytes + 1 - len(chunks)))
                    if not chunk:
                        return bytes(chunks)
                    chunks.extend(chunk)
                raise UnsafePathError("File exceeds the read limit")
            finally:
                os.close(descriptor)

    async def read_bytes(self, relative_path: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes, relative_path)

    async def read_file(self, relative_path: str) -> str:
        return (await self.read_bytes(relative_path)).decode("utf-8")

    def _write_bytes(self, relative_path: str, content: bytes) -> None:
        if len(content) > self.max_file_bytes:
            raise UnsafePathError("File exceeds the write limit")
        parts = self._parts(relative_path)
        if not self._posix_files:
            with self._fd_lock:
                if self._closed:
                    raise SandboxError("Workspace is closed")
                try:
                    self._portable.write(parts, content)
                except PortablePathError as error:
                    raise UnsafePathError(str(error)) from error
            return
        with self._directory(parts[:-1], create=True) as parent:
            try:
                existing = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and (
                not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
            ):
                raise UnsafePathError("Only regular files with a single hard link are writable")
            temporary = f".openfoundry-write-{uuid4().hex}"
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    async def write_bytes(self, relative_path: str, content: bytes) -> None:
        await asyncio.to_thread(self._write_bytes, relative_path, content)

    async def write_file(self, relative_path: str, content: str) -> None:
        await self.write_bytes(relative_path, content.encode("utf-8"))

    @staticmethod
    def _environment() -> dict[str, str]:
        """Explicit environment: no API keys, Git authentication, or caller preload flags."""
        trusted_paths = [str(Path(sys.executable).parent)]
        # Keep only selected tool directories, including Homebrew/Windows Git installs.
        for tool in ("git", "node", "npm"):
            executable = shutil.which(tool)
            if executable:
                trusted_paths.append(str(Path(executable).resolve().parent))
        trusted_paths.extend(("/usr/local/bin", "/usr/bin", "/bin"))
        environment = {
            "PATH": os.pathsep.join(dict.fromkeys(trusted_paths)),
            "HOME": gettempdir(),
            "TMPDIR": gettempdir(),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "CI": "1",
            "NO_COLOR": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONNOUSERSITE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_AUTHOR_NAME": "OpenFoundry",
            "GIT_AUTHOR_EMAIL": "openfoundry@localhost",
            "GIT_COMMITTER_NAME": "OpenFoundry",
            "GIT_COMMITTER_EMAIL": "openfoundry@localhost",
        }
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return environment

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        if sys.platform == "win32":
            # taskkill terminates descendants, unlike Process.kill() on Windows.
            import subprocess

            subprocess.run(
                [
                    str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/taskkill.exe"),
                    "/F",
                    "/T",
                    "/PID",
                    str(process.pid),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)

    def _checked_path(self, parts: tuple[str, ...], *, directory: bool = False) -> Path:
        if self._closed:
            raise SandboxError("Workspace is closed")
        try:
            if self._posix_files:
                root_info = os.fstat(self._root_fd)
                current = self.root.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (root_info.st_dev, root_info.st_ino):
                    raise UnsafePathError("Workspace root was replaced")
                guard = PortableWorkspace(self.root, self.max_file_bytes)
                if guard.identity != (root_info.st_dev, root_info.st_ino):
                    raise UnsafePathError("Workspace root was replaced")
            else:
                guard = self._portable
            return guard.path(parts, directory=directory)
        except PortablePathError as error:
            raise UnsafePathError(str(error)) from error

    @contextmanager
    def _command_directory(self, parts: tuple[str, ...]) -> Iterator[tuple[str, tuple[int, ...]]]:
        if self._fd_paths and self._posix_files:
            with self._directory(parts) as descriptor:
                yield f"/proc/self/fd/{descriptor}", (descriptor,)
        else:
            yield str(self._checked_path(parts, directory=True)), ()

    async def _run_process(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        pass_fds: tuple[int, ...],
        display_command: tuple[str, ...] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=self._environment(),
                pass_fds=pass_fds,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            # Cancellation can arrive after fork but before asyncio returns the handle.
            outcomes = await asyncio.gather(launch, return_exceptions=True)
            if isinstance(outcomes[0], asyncio.subprocess.Process):
                orphan = outcomes[0]
                try:
                    self._kill_process(orphan)
                except ProcessLookupError:
                    pass
                await orphan.communicate()
            raise
        except OSError as error:
            raise SandboxUnavailableError("Cannot start the configured executable") from error
        buffers = [bytearray(), bytearray()]

        async def drain(stream: asyncio.StreamReader | None, buffer: bytearray) -> None:
            if stream is None:
                return
            while chunk := await stream.read(65_536):
                available = self.max_output_bytes - len(buffer)
                buffer.extend(chunk[:available])
                if len(chunk) > available:
                    raise _OutputLimitError

        async def kill_group() -> None:
            try:
                self._kill_process(process)
            except ProcessLookupError:
                pass
            for reader in tasks[:2]:
                reader.cancel()
            await asyncio.gather(*tasks[:2], return_exceptions=True)

            # A full pipe can keep Process.wait() pending after SIGKILL. Discard the
            # bounded remainder without retaining it, so transport closure can complete.
            async def discard(stream: asyncio.StreamReader | None) -> None:
                if stream is not None:
                    while await stream.read(65_536):
                        pass

            await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())

        tasks = [
            asyncio.create_task(drain(process.stdout, buffers[0])),
            asyncio.create_task(drain(process.stderr, buffers[1])),
            asyncio.create_task(process.wait()),
        ]
        timed_out = False
        output_limited = False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await asyncio.gather(*tasks)
        except TimeoutError:
            timed_out = True
            await kill_group()
        except _OutputLimitError:
            output_limited = True
            await kill_group()
        except BaseException:
            await kill_group()
            raise
        finally:
            # Kill descendants even if the command exited but left background children.
            try:
                self._kill_process(process)
            except ProcessLookupError:
                pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return CommandResult(
            display_command or tuple(argv),
            process.returncode if process.returncode is not None else -9,
            buffers[0].decode("utf-8", errors="replace"),
            buffers[1].decode("utf-8", errors="replace"),
            time.monotonic() - started,
            timed_out,
            output_limited,
        )

    @contextmanager
    def _git_directories(self, *, initialize: bool) -> Iterator[tuple[str, str, tuple[int, ...]]]:
        root = self._checked_path((), directory=True)
        metadata = root / ".git"
        if initialize:
            metadata.mkdir(mode=0o700, exist_ok=True)
        try:
            PortableWorkspace._regular(metadata, directory=True)
        except (OSError, PortablePathError) as error:
            raise UnsafePathError("Git metadata must be a local directory") from error
        if self._fd_paths and self._posix_files:
            with self._directory(()) as root_fd, self._directory((".git",)) as git_fd:
                yield f"/proc/self/fd/{root_fd}", f"/proc/self/fd/{git_fd}", (root_fd, git_fd)
        else:
            yield str(root), str(metadata), ()

    async def commit_files(self, paths: Sequence[str], message: str) -> str:
        """Commit only supplied regular files; unrelated staged paths are preserved."""
        if not paths or len(paths) > 100 or not message.strip() or len(message) > 500:
            raise ValueError("A bounded file list and commit message are required")
        for path in paths:
            await self.read_bytes(path)
        await self.initialize_git()
        async with self._git_lock:
            added = await self._git("add", "--", *paths)
            if not added.succeeded:
                raise GitOperationError("Cannot stage validated engineering files")
            changed = await self._git("diff", "--cached", "--quiet", "--", *paths)
            if (
                changed.return_code not in {0, 1}
                or changed.timed_out
                or changed.output_limit_exceeded
            ):
                raise GitOperationError("Cannot inspect staged engineering changes")
            if changed.return_code == 1:
                committed = await self._git(
                    "commit", "--only", "--no-verify", "-m", message, "--", *paths
                )
                if not committed.succeeded:
                    raise GitOperationError("Cannot commit engineering changes")
            head = await self._git("rev-parse", "HEAD")
            if not head.succeeded:
                raise GitOperationError("Cannot resolve engineering commit")
            return head.stdout.strip()

    async def _git(self, *arguments: str) -> CommandResult:
        executable = shutil.which("git", path=self._environment()["PATH"])
        if executable is None:
            raise SandboxUnavailableError("Git is not installed")
        with self._git_directories(initialize=arguments[0] == "init") as (
            root_path,
            git_path,
            descriptors,
        ):
            prefix = (
                executable,
                "--no-pager",
                "--literal-pathspecs",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "submodule.recurse=false",
                "-c",
                f"core.attributesFile={os.devnull}",
                "-c",
                f"core.excludesFile={os.devnull}",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "credential.helper=",
                "-c",
                "protocol.allow=never",
                "-c",
                "color.ui=false",
                f"--git-dir={git_path}",
                f"--work-tree={root_path}",
            )
            overrides: list[str] = []
            if arguments[0] != "init":
                # Git clean/smudge/process filters can execute even when external
                # diffs and textconv are disabled. Neutralize every configured driver.
                filters = await self._run_process(
                    (*prefix, "config", "--null", "--name-only", "--get-regexp", r"^filter\."),
                    cwd=root_path,
                    pass_fds=descriptors,
                    display_command=("git", "config", "--name-only"),
                )
                if (
                    filters.return_code not in {0, 1}
                    or filters.timed_out
                    or filters.output_limit_exceeded
                ):
                    raise GitOperationError("Cannot safely inspect local Git filter configuration")
                for key in filter(None, filters.stdout.split("\0")):
                    option = key.rsplit(".", 1)[-1].casefold()
                    if option in {"clean", "smudge", "process", "required"}:
                        value = "false" if option == "required" else ""
                        overrides.extend(("-c", f"{key}={value}"))
            return await self._run_process(
                (*prefix, *overrides, *arguments),
                cwd=root_path,
                pass_fds=descriptors,
                display_command=("git", *arguments),
            )

    async def initialize_git(self) -> None:
        async with self._git_lock:
            metadata = self._checked_path((), directory=True) / ".git"
            if metadata.is_symlink() or (metadata.exists() and not metadata.is_dir()):
                raise UnsafePathError("Git metadata must be a local directory")
            if metadata.exists():
                try:
                    PortableWorkspace._regular(metadata, directory=True)
                except PortablePathError as error:
                    raise UnsafePathError("Git metadata must be a real directory") from error
            initialize = not metadata.exists() or not any(metadata.iterdir())
            if initialize:
                # An empty template disables both system and user-installed Git hooks.
                result = await self._git("init", "--initial-branch=main", "--template=")
                if not result.succeeded:
                    raise GitOperationError(result.stderr)
            head = await self._git("rev-parse", "--verify", "HEAD^{commit}")
            if not head.succeeded:
                symbolic = await self._git("symbolic-ref", "--quiet", "HEAD")
                if not symbolic.succeeded or not symbolic.stdout.strip().startswith("refs/heads/"):
                    raise GitOperationError("Git HEAD is invalid; existing metadata was preserved")
                branch = await self._git("show-ref", "--verify", "--quiet", symbolic.stdout.strip())
                if branch.return_code != 1:
                    raise GitOperationError(
                        "Git HEAD is unreadable; existing metadata was preserved"
                    )
                result = await self._git(
                    "commit", "--allow-empty", "--no-verify", "-m", "Initialize mission workspace"
                )
                if not result.succeeded:
                    raise GitOperationError(result.stderr)

    async def create_branch(self, name: str, *, checkout: bool = True) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", name):
            raise ValueError("Invalid Git branch name")
        await self.initialize_git()
        check = await self._git("check-ref-format", "--branch", name)
        if not check.succeeded:
            raise ValueError("Invalid Git branch name")
        result = await self._git(*(("switch", "-c", name) if checkout else ("branch", name)))
        if not result.succeeded:
            raise GitOperationError(result.stderr)

    async def git_diff(self, *, include_untracked: bool = True) -> str:
        await self.initialize_git()
        result = await self._git(
            "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3", "HEAD", "--", "."
        )
        if not result.succeeded:
            raise GitOperationError(result.stderr or "Git diff exceeded its execution limits")
        chunks = [result.stdout]
        size = len(result.stdout.encode("utf-8"))
        if include_untracked:
            untracked = await self._git("ls-files", "--others", "--exclude-standard", "-z")
            if not untracked.succeeded:
                raise GitOperationError("Cannot enumerate untracked files within execution limits")
            for name in filter(None, untracked.stdout.split("\0")):
                content = await self.read_bytes(name)
                destination = self._quote_git_path(f"b/{name}")
                try:
                    text = content.decode("utf-8")
                    if "\0" in text:
                        raise UnicodeError("Binary file")
                except UnicodeError:
                    diff = f"Binary files /dev/null and {destination} differ\n"
                else:
                    lines = difflib.unified_diff(
                        [],
                        text.splitlines(keepends=True),
                        fromfile="/dev/null",
                        tofile=destination,
                    )
                    diff = "".join(
                        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                        for line in lines
                    )
                    if not content:
                        origin = self._quote_git_path(f"a/{name}")
                        diff = f"diff --git {origin} {destination}\nnew file mode 100644\n"
                size += len(diff.encode("utf-8"))
                if size > self.max_output_bytes:
                    raise GitOperationError("Combined Git diff exceeds the output limit")
                chunks.append(diff)
        return "".join(chunks)

    @staticmethod
    def _quote_git_path(path: str) -> str:
        encoded = path.encode("utf-8")
        if all(33 <= byte <= 126 and byte not in {34, 92} for byte in encoded):
            return path
        escaped = "".join(
            chr(byte) if 32 <= byte <= 126 and byte not in {34, 92} else f"\\{byte:03o}"
            for byte in encoded
        )
        return '"' + escaped + '"'

    def validate_test_command(self, command: Sequence[str]) -> None:
        """Validate tool availability and argument grammar without executing project code."""
        self._test_argv(command)

    def _test_argv(self, command: Sequence[str]) -> tuple[str, ...]:
        if isinstance(command, (str, bytes)) or not command or len(command) > 100:
            raise CommandNotAllowedError("Provide a nonempty argument list, never a shell string")
        if any(
            not isinstance(item, str) or not item or len(item) > 4096 or "\0" in item
            for item in command
        ):
            raise CommandNotAllowedError("Invalid command arguments")
        name, *arguments = command
        if name == "npm":
            if arguments != ["test"]:
                raise CommandNotAllowedError("The only supported npm invocation is npm test")
            executable = shutil.which("npm", path=self._environment()["PATH"])
            if executable is None:
                raise SandboxUnavailableError("npm is not installed")
            return (executable, "--ignore-scripts", "test")
        if name not in {"pytest", "ruff", "flake8"}:
            raise CommandNotAllowedError("Only pytest, npm test, ruff, and flake8 are allowed")
        if name == "ruff":
            if not arguments or arguments[0] != "check":
                raise CommandNotAllowedError("Only ruff check is supported")
            arguments = arguments[1:]
        switches = {
            "pytest": {
                "-q",
                "-v",
                "-vv",
                "-x",
                "--disable-warnings",
                "--strict-markers",
                "--strict-config",
                "--collect-only",
            },
            "ruff": {"--output-format=concise", "--output-format=full"},
            "flake8": {"--count", "--statistics", "--show-source"},
        }
        for argument in arguments:
            if argument.startswith("-"):
                if argument not in switches[name]:
                    raise CommandNotAllowedError("Command option is not allowlisted")
            else:
                path = argument.split("::", 1)[0] if name == "pytest" else argument
                try:
                    self._parts(path, directory=True)
                except UnsafePathError:
                    raise CommandNotAllowedError(
                        "Command paths must stay within the workspace"
                    ) from None
        if importlib.util.find_spec(name) is None:
            raise SandboxUnavailableError(f"Install {name} in the backend's Python environment")
        # Fresh cache lookup plus -B avoids stale same-second/same-size module bytecode
        # after a repair, including caches left by an earlier external test run.
        cache_prefix = str(Path(gettempdir()) / ("openfoundry-pycache-" + uuid4().hex))
        prefix = (sys.executable, "-I", "-B", "-X", f"pycache_prefix={cache_prefix}", "-m", name)
        if name == "pytest":
            # Enable only the bundled async plugin and contained project import roots.
            arguments = [
                "-p",
                "pytest_asyncio.plugin",
                "--override-ini=asyncio_mode=auto",
                "--override-ini=pythonpath=. src",
                "--confcutdir=.",
                "--rootdir=.",
                *arguments,
            ]
        elif name in {"ruff", "flake8"}:
            # Host/ancestor lint configuration is outside the mission's verification scope.
            arguments = ["--isolated", "--extend-exclude=.openfoundry-qa", *arguments]
            if name == "ruff":
                arguments.insert(0, "--force-exclude")
        return (*prefix, *(("check",) if name == "ruff" else ()), *arguments)

    def _isolated_argv(
        self, argv: tuple[str, ...], root_fd: int, git_fd: int, cwd: str
    ) -> tuple[str, ...]:
        executable = shutil.which("bwrap", path=self._environment()["PATH"])
        if executable is None:
            raise SandboxUnavailableError("Bubblewrap is required for isolated test execution")
        arguments = [
            executable,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
        ]
        mounts = {
            Path(path) for path in ("/usr", "/bin", "/lib", "/lib64", sys.prefix, sys.base_prefix)
        }
        node = shutil.which("node", path=self._environment()["PATH"])
        if node:
            mounts.add(Path(node).resolve().parent.parent)
        for mount in sorted(mounts, key=lambda path: (len(path.parts), str(path))):
            if mount.is_dir():
                arguments.extend(("--ro-bind", str(mount), str(mount)))
        arguments.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home"))
        arguments.extend(("--bind", f"/proc/self/fd/{root_fd}", "/work"))
        arguments.extend(("--ro-bind", f"/proc/self/fd/{git_fd}", "/work/.git"))
        for name, value in self._environment().items():
            arguments.extend(("--setenv", name, value))
        arguments.extend(("--chdir", "/work" if cwd == "." else f"/work/{cwd}", "--", *argv))
        return tuple(arguments)

    async def _probe_isolation(self) -> bool:
        async with self._probe_lock:
            if self._isolation_available is not None:
                return self._isolation_available
            if (
                not self._fd_paths
                or not self._posix_files
                or shutil.which("bwrap", path=self._environment()["PATH"]) is None
            ):
                self._isolation_reason = "Bubblewrap or Linux user namespaces are unavailable"
                self._isolation_available = False
                return False
            await self.initialize_git()
            try:
                with self._directory(()) as root_fd, self._directory((".git",)) as git_fd:
                    async with asyncio.timeout(5):
                        probe = await self._run_process(
                            self._isolated_argv(("/usr/bin/true",), root_fd, git_fd, "."),
                            cwd="/",
                            pass_fds=(root_fd, git_fd),
                            display_command=("bwrap", "--probe"),
                        )
                self._isolation_available = probe.succeeded
                if not probe.succeeded:
                    self._isolation_reason = "Bubblewrap user namespace capability probe failed"
            except (SandboxUnavailableError, TimeoutError):
                self._isolation_reason = "Bubblewrap capability probe could not complete"
                self._isolation_available = False
            except asyncio.CancelledError:
                self._isolation_available = None
                raise
            return self._isolation_available

    def _list_files(self) -> tuple[str, ...]:
        root = self._checked_path((), directory=True)
        files: list[str] = []
        count = 0
        for directory, subdirectories, names in os.walk(root, followlinks=False):
            relative = Path(directory).relative_to(root)
            if len(relative.parts) > 32:
                raise UnsafePathError("Workspace exceeds directory depth limit")
            for name in (*subdirectories, *names):
                count += 1
                if count > 10000:
                    raise UnsafePathError("Workspace exceeds entry limit")
                if name == ".git":
                    continue
                path = Path(directory) / name
                PortableWorkspace._regular(path, directory=name in subdirectories)
            subdirectories[:] = [
                name
                for name in subdirectories
                if name
                not in {
                    ".git",
                    "node_modules",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                }
            ]
            files.extend((relative / name).as_posix() for name in names if name != ".git")
        return tuple(sorted(files))

    async def list_files(self) -> tuple[str, ...]:
        """Return contained regular paths, excluding tool caches and dependency trees."""
        try:
            return await asyncio.to_thread(self._list_files)
        except PortablePathError as error:
            raise UnsafePathError(str(error)) from error

    async def run_test_command(self, command: Sequence[str], *, cwd: str = ".") -> CommandResult:
        """Run an allowlisted command, choosing isolation before executing project code.

        Auto falls back only after a harmless capability probe. A failed project command
        is never replayed on the host. Guarded mode sanitizes credentials and bounds paths,
        output and process lifetime; generated code still has the host user's privileges.
        """
        argv = self._test_argv(command)
        parts = self._parts(cwd, directory=True)
        isolated = self.sandbox_mode != "guarded" and await self._probe_isolation()
        if not isolated and self.sandbox_mode == "strict":
            raise SandboxUnavailableError(self._isolation_reason or "Bubblewrap is unavailable")
        files = await self.list_files()

        def configured_argv(working_directory: str) -> tuple[str, ...]:
            if command[0] != "pytest":
                return argv
            # Pytest otherwise discovers the service checkout's ancestor pyproject,
            # resolving project imports and testpaths against /app instead of the mission.
            prefix = "/".join(parts)
            configuration = next(
                (
                    name
                    for name in (
                        "pytest.toml",
                        ".pytest.toml",
                        "pytest.ini",
                        ".pytest.ini",
                        "pyproject.toml",
                        "tox.ini",
                        "setup.cfg",
                    )
                    if (f"{prefix}/{name}" if prefix else name) in files
                ),
                os.devnull,
            )
            import_roots = shlex.join([working_directory, working_directory.rstrip("/") + "/src"])
            return (
                *(
                    f"--override-ini=pythonpath={import_roots}"
                    if argument.startswith("--override-ini=pythonpath=")
                    else argument
                    for argument in argv
                ),
                "-c",
                configuration,
            )

        if not isolated:
            with self._command_directory(parts) as (working, descriptors):
                result = await self._run_process(
                    configured_argv(working),
                    cwd=working,
                    pass_fds=descriptors,
                    display_command=tuple(command),
                )
            return replace(result, execution_mode="guarded", fallback_reason=self._isolation_reason)
        with (
            self._directory(parts),
            self._directory(()) as root_fd,
            self._directory((".git",)) as git_fd,
        ):
            result = await self._run_process(
                self._isolated_argv(
                    configured_argv("/work" + ("/" + "/".join(parts) if parts else "")),
                    root_fd,
                    git_fd,
                    cwd,
                ),
                cwd="/",
                pass_fds=(root_fd, git_fd),
                display_command=tuple(command),
            )
        if not result.succeeded and result.stderr.startswith("bwrap:"):
            raise SandboxUnavailableError(
                "Bubblewrap could not establish isolation; command was not retried"
            )
        return replace(result, execution_mode="isolated")

    def close(self) -> None:
        with self._fd_lock:
            if not self._closed:
                if self._root_fd >= 0:
                    os.close(self._root_fd)
                self._closed = True

    async def __aenter__(self) -> WorkspaceSandbox:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()
