"""Operator-owned external MCP configuration; never accept transport settings from agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    transport: Literal["stdio", "streamable_http"]
    enabled: bool = True
    command: str | None = None
    args: tuple[str, ...] = Field(default=(), max_length=64)
    url: str | None = None
    headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    env: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    allowed_tools: tuple[str, ...] = Field(default=(), max_length=100)
    departments: tuple[str, ...] = Field(default=(), max_length=32)
    requires_approval: bool = True
    timeout_seconds: float = Field(default=15, gt=0, le=60)

    @model_validator(mode="after")
    def guarded_transport(self) -> MCPServerConfig:
        if self.transport == "stdio":
            if not self.command or not Path(self.command).is_absolute() or self.url or self.headers:
                raise ValueError("Stdio MCP needs an absolute operator-approved executable only")
        else:
            parsed = urlsplit(self.url or "")
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or self.command
                or self.args
                or self.env
            ):
                raise ValueError("HTTP MCP needs an explicit HTTP(S) endpoint without URL secrets")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) for name in self.allowed_tools):
            raise ValueError("Invalid MCP tool allowlist")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) for name in self.departments):
            raise ValueError("Invalid MCP department allowlist")
        if any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key.startswith(("LD_", "DYLD_", "PYTHON"))
            or key in {"PATH", "HOME", "SHELL", "NODE_OPTIONS"}
            for key in self.env
        ):
            raise ValueError(
                "MCP environment overrides may contain only explicit service credentials"
            )
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key)
            or key.lower() in {"host", "content-length", "connection", "transfer-encoding"}
            for key in self.headers
        ):
            raise ValueError("Invalid MCP HTTP header override")
        return self
