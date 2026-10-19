"""Portable path guards for hosts without POSIX descriptor-relative file operations.

These checks reject links/reparse points and replacement of the mission root. They do
not provide kernel isolation or the race resistance of the POSIX descriptor backend.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4


class PortablePathError(ValueError):
    """A portable workspace operation encountered an unsafe filesystem entry."""


class PortableWorkspace:
    def __init__(self, root: Path, max_file_bytes: int) -> None:
        self.root = root
        self.max_file_bytes = max_file_bytes
        if root.exists() or root.is_symlink():
            self._regular(root, directory=True)
        else:
            root.mkdir(mode=0o700)
        info = root.stat(follow_symlinks=False)
        self.identity = (info.st_dev, info.st_ino)

    @staticmethod
    def _regular(path: Path, *, directory: bool) -> os.stat_result:
        info = path.stat(follow_symlinks=False)
        reparse = getattr(info, "st_file_attributes", 0) & 0x400
        if (
            reparse
            or stat.S_ISLNK(info.st_mode)
            or (
                not stat.S_ISDIR(info.st_mode)
                if directory
                else not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            )
        ):
            raise PortablePathError(
                "Workspace links, reparse points, and special files are blocked"
            )
        return info

    def path(
        self, parts: tuple[str, ...], *, directory: bool = False, create: bool = False
    ) -> Path:
        root_info = self._regular(self.root, directory=True)
        if (root_info.st_dev, root_info.st_ino) != self.identity:
            raise PortablePathError("Mission workspace directory was replaced")
        current = self.root
        parents = parts if directory else parts[:-1]
        for part in parents:
            current = current / part
            if create:
                current.mkdir(mode=0o700, exist_ok=True)
            self._regular(current, directory=True)
        target = current if directory else current / parts[-1]
        if not directory:
            try:
                self._regular(target, directory=False)
            except FileNotFoundError:
                pass
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise PortablePathError("Workspace path escapes the mission root")
        return target

    def read(self, parts: tuple[str, ...]) -> bytes:
        path = self.path(parts)
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PortablePathError("Only private regular files are readable")
            data = stream.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise PortablePathError("File exceeds the read limit")
        return data

    def write(self, parts: tuple[str, ...], data: bytes) -> None:
        path = self.path(parts, create=True)
        if len(data) > self.max_file_bytes:
            raise PortablePathError("File exceeds the write limit")
        temporary = path.with_name(".openfoundry-write-" + uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.path(parts)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
