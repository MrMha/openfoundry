"""Apply trusted Ruff safe import fixes and formatting before initial file protection."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import sys

from backend.app.core.workspace_sandbox import WorkspaceSandbox


async def format_generated_python(
    source: str, filename: str, *, first_party: tuple[str, ...] = ()
) -> str:
    """Format source via stdin without importing it or reading project configuration."""
    ast.parse(source, filename=filename)
    commands = [
        ["check", "--fix-only", "--select=I,F401"],
        ["format"],
    ]
    for arguments in commands:
        if arguments[0] == "check" and first_party:
            arguments = [
                *arguments,
                "--config",
                "lint.isort.known-first-party=" + json.dumps(first_party),
            ]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "ruff",
            *arguments,
            "--isolated",
            "--stdin-filename",
            filename,
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=WorkspaceSandbox._environment(),
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(10):
                output, _ = await process.communicate(source.encode())
            if process.returncode != 0 or len(output) > 2 * 1024 * 1024:
                raise ValueError("Generated Python could not be formatted within limits")
            source = output.decode()
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    WorkspaceSandbox._kill_process(process)
                await process.wait()
    ast.parse(source, filename=filename)
    return source
