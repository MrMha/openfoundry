"""Fixed-module research workers with credential-free environments and bounded output."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import Literal

from backend.app.core.workspace_sandbox import WorkspaceSandbox

ResearchModule = Literal[
    "backend.app.departments._research_documents",
    "backend.app.departments._research_browser",
]


class ResearchWorkerError(RuntimeError):
    """A worker failed without exposing raw child diagnostics or source content."""


async def run_research_worker(
    module: ResearchModule,
    payload: bytes,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> bytes:
    if module not in {
        "backend.app.departments._research_documents",
        "backend.app.departments._research_browser",
    }:
        raise ValueError("Research worker module is not allowlisted")
    if not 0 < timeout_seconds <= 60 or not 0 < max_output_bytes <= 16 * 1024 * 1024:
        raise ValueError("Research worker limits are invalid")
    if len(payload) > 6 * 1024 * 1024:
        raise ValueError("Research worker input exceeds its byte limit")
    environment = {
        **WorkspaceSandbox._environment(),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            module,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
    )
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            try:
                process = await asyncio.shield(launch)
            except asyncio.CancelledError:
                outcomes = await asyncio.gather(launch, return_exceptions=True)
                if isinstance(outcomes[0], asyncio.subprocess.Process):
                    process = outcomes[0]
                raise
            assert process.stdin is not None and process.stdout is not None
            incoming, outgoing = process.stdin, process.stdout

            async def send_input() -> None:
                incoming.write(payload)
                await incoming.drain()
                incoming.close()

            sender = asyncio.create_task(send_input())
            try:
                output = bytearray()
                while chunk := await outgoing.read(65536):
                    output.extend(chunk)
                    if len(output) > max_output_bytes:
                        raise ResearchWorkerError("Research worker output exceeds byte limit")
                await sender
                await process.wait()
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            if process.returncode != 0:
                raise ResearchWorkerError("Research worker failed or is unavailable")
            return bytes(output)
    finally:
        if process is not None:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    WorkspaceSandbox._kill_process(process)
            await process.wait()
