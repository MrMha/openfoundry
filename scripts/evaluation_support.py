"""Independent, bounded grading and atomic UTF-8 evidence checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from backend.app.config import Settings
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.departments.engineering_contracts import test_path


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checkpoint(path: Path, report: dict, *, secret: str) -> None:
    """Keep the last complete receipt even if a process dies during the next write."""
    payload = json.dumps(report, indent=2, default=str, ensure_ascii=True)
    if secret:
        payload = payload.replace(secret, "[REDACTED]")
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def production_source(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        path.endswith(".py")
        and not test_path(path)
        and not any(part.startswith(".") or part == "__pycache__" for part in parts)
        and parts[0] not in {"qa", "research", "engineering"}
        and parts[-1] not in {"conftest.py", "sitecustomize.py", "usercustomize.py"}
    )


async def independently_check(settings: Settings, mission_id: UUID, checks: str) -> dict:
    """Grade all production modules in a fresh workspace with fixed external tests.

    No generated test, conftest, pytest configuration, or project plugin is copied.
    This prevents accidental grading contamination; guarded execution is not a
    security boundary against actively malicious generated Python.
    """
    files: dict[str, bytes] = {}
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as source:
        for path in await source.list_files():
            if production_source(path):
                files[path] = await source.read_bytes(path)
        if not files:
            return {"status": "not_run", "reason": "No production Python modules delivered"}
        if len(files) > 100 or sum(map(len, files.values())) > 2 * 1024 * 1024:
            return {"status": "failed", "reason": "Production source exceeds grading limits"}
    check_id = uuid4()
    async with WorkspaceSandbox(
        check_id, root=settings.data_dir / "acceptance", sandbox_mode=settings.sandbox_mode
    ) as sandbox:
        for path, data in files.items():
            await sandbox.write_bytes(path, data)
        await sandbox.write_file("test_independent.py", checks)
        result = await sandbox.run_test_command(["pytest", "-q", "test_independent.py"])
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    return {
        "status": "passed" if result.succeeded else "failed",
        "application_sha256": hashes.get("app.py"),
        "source_sha256": hashes,
        "checks_sha256": digest(checks),
        "workspace": str(settings.data_dir / "acceptance" / str(check_id)),
        **asdict(result),
    }
