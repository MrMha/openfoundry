"""Validated engineering deliverables, verification plans, and repair evidence."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.core.planning import GeneratedFile, WorkResult
from backend.app.models.events import CodeDiffEvent, TestResult

QA_CONFIG = "openfoundry.qa.json"
PROTECTED_CONFIG = frozenset(
    {
        "SPEC.md",
        "STATE.md",
        QA_CONFIG,
        "pyproject.toml",
        "pytest.ini",
        "ruff.toml",
        ".ruff.toml",
        "setup.cfg",
        "tox.ini",
        "package.json",
        "conftest.py",
    }
)


def test_path(path: str) -> bool:
    parts = path.split("/")
    return any(part in {"tests", "test"} for part in parts[:-1]) or (
        parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
    )


def editable_path(path: str) -> str:
    CodeDiffEvent.relative_file_path(path)
    if (
        path in {"SPEC.md", "STATE.md", QA_CONFIG}
        or any(part.startswith(".openfoundry-") for part in path.split("/"))
        or path.split("/")[0] in {"qa", "research", "engineering"}
    ):
        raise ValueError(
            "Engineering cannot overwrite specifications, verification plans, or evidence"
        )
    return path


class CommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: list[str] = Field(min_length=1, max_length=100)
    cwd: str = "."

    @field_validator("command")
    @classmethod
    def supported(cls, command: list[str]) -> list[str]:
        if (
            len(command) >= 3
            and command[0] in {"python", "python3"}
            and command[1] == "-m"
            and command[2] in {"pytest", "ruff"}
        ):
            # Canonicalize only these fixed module launchers. The sandbox still builds
            # its own isolated Python argv and validates every remaining argument.
            command = command[2:]
        if command[0] not in {"pytest", "ruff"}:
            raise ValueError("QA commands must use pytest or ruff")
        return command

    @field_validator("cwd")
    @classmethod
    def relative_directory(cls, value: str) -> str:
        return value if value == "." else CodeDiffEvent.relative_file_path(value)


class EndpointProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="/", min_length=1, max_length=2048)
    expected_status: int = Field(default=200, ge=100, le=599)
    contains: str | None = Field(default=None, min_length=1, max_length=1000)
    selectors: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("path")
    @classmethod
    def relative_route(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("Probe paths must be absolute routes on the configured origin")
        return value


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commands: list[CommandSpec] = Field(
        default_factory=lambda: [
            CommandSpec(command=["pytest", "-q"]),
            CommandSpec(command=["ruff", "check", "."]),
        ],
        min_length=1,
        max_length=4,
    )
    asgi_app: str | None = None
    app_factory: bool = False
    base_url: str | None = None
    probes: list[EndpointProbe] = Field(default_factory=list, max_length=8)
    prefer_browser: bool = True
    max_repairs: int = Field(default=3, ge=0, le=3)

    @field_validator("asgi_app")
    @classmethod
    def module_reference(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value
        ):
            raise ValueError("ASGI app must use a module:attribute reference")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = httpx.URL(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError("QA target must be an HTTP origin without credentials or query")
        if url.path not in {"", "/"}:
            raise ValueError("QA base_url must identify an origin")
        if url.host != "localhost":
            try:
                address = ipaddress.ip_address(url.host)
            except ValueError:
                pass
            else:
                if not (address.is_global or address.is_loopback) or address.is_multicast:
                    raise ValueError("QA target must be public or explicitly loopback")
        return str(url).rstrip("/")

    @model_validator(mode="after")
    def endpoint_source(self) -> VerificationPlan:
        if any("--collect-only" in check.command for check in self.commands):
            raise ValueError("QA must execute tests, not only collect them")
        if self.asgi_app and self.base_url:
            raise ValueError("Select an ASGI application or an HTTP origin")
        if self.probes and not (self.asgi_app or self.base_url):
            raise ValueError("Endpoint probes require an ASGI app or HTTP origin")
        if (self.asgi_app or self.base_url) and not self.probes:
            self.probes = [EndpointProbe()]
        return self


class EngineeringResult(WorkResult):
    verification: VerificationPlan

    @model_validator(mode="after")
    def modular_files(self) -> EngineeringResult:
        for file in self.files:
            editable_path(file.file_path)
            if len(file.content.splitlines()) >= 200:
                raise ValueError("Generated files must contain fewer than 200 lines")
        return self


class BugDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_cause: str = Field(min_length=1, max_length=8000)
    affected_files: list[str] = Field(min_length=1, max_length=20)
    fix_strategy: str = Field(min_length=1, max_length=8000)
    regression_test: str = Field(min_length=1, max_length=8000)

    @field_validator("affected_files")
    @classmethod
    def safe_files(cls, values: list[str]) -> list[str]:
        return [CodeDiffEvent.relative_file_path(value) for value in values]


class CheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    result: TestResult
    execution_mode: Literal["isolated", "guarded"] | None = None


class BugReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    task_id: UUID
    iteration: int = Field(ge=0, le=3)
    diagnosis: BugDiagnosis
    checks: list[CheckRecord]


class RepairResult(WorkResult):
    files: list[GeneratedFile] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def modular_fix(self) -> RepairResult:
        for file in self.files:
            editable_path(file.file_path)
            if len(file.content.splitlines()) >= 200:
                raise ValueError("Patched files must contain fewer than 200 lines")
        return self
