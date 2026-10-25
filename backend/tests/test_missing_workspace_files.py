"""Absent optional nested artifacts have the same file semantics on every OS."""

from uuid import uuid4

import pytest

from backend.app.core.workspace_sandbox import WorkspaceSandbox


@pytest.mark.parametrize("path", ["missing.json", "engineering/latest.json"])
async def test_absent_file_or_parent_raises_file_not_found(settings, path):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        with pytest.raises(FileNotFoundError):
            await sandbox.read_file(path)
