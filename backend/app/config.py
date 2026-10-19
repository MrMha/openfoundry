"""Validated, explicitly loaded configuration. Importing this module has no side effects."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy.engine import make_url

from backend.app.core.mcp_config import MCPServerConfig

MAX_MISSION_COST_USD = Decimal("2.00")
USD_QUANTUM = Decimal("0.000000001")
Provider = Literal["openai", "gemini", "anthropic", "ollama"]


class MissionPreferences(BaseModel):
    """Non-secret, per-mission routing and budget overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Provider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=255)
    budget_usd: Decimal | None = Field(default=None, gt=0, le=1_000_000, decimal_places=9)

    @model_validator(mode="after")
    def model_matches_provider(self) -> MissionPreferences:
        if self.model is not None:
            prefix = "ollama_chat/" if self.provider == "ollama" else f"{self.provider}/"
            if self.provider is None or not self.model.startswith(prefix):
                raise ValueError("Select a provider and use its qualified model name")
            if not self.model.removeprefix(prefix).strip() or any(
                char.isspace() for char in self.model
            ):
                raise ValueError("Model names must not contain whitespace")
        return self


class MissionExecution(MissionPreferences):
    mode: Literal["live", "demo"] = "live"
    blueprint_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    blueprint_parameters: dict[str, str] = Field(default_factory=dict, max_length=16)


def mission_settings(settings: Settings, preferences: MissionPreferences) -> Settings:
    """Apply validated overrides without persisting or mutating server credentials."""
    overrides: dict[str, object] = {}
    if preferences.budget_usd is not None:
        if preferences.budget_usd > settings.max_mission_cost_usd:
            raise ValueError("Mission budget cannot exceed the server cost ceiling")
        overrides["max_mission_cost_usd"] = preferences.budget_usd
    if preferences.provider:
        provider = preferences.provider
        configured = (
            settings.ollama_base_url
            if provider == "ollama"
            else getattr(settings, f"{provider}_api_key")
        )
        if not configured:
            raise ValueError("Selected provider is not configured on the server")
        overrides["provider_order"] = (provider,)
        if preferences.model:
            overrides[f"{provider}_model"] = preferences.model
    return Settings.model_validate({**settings.model_dump(), **overrides})


class Settings(BaseModel):
    """Environment defaults can be overridden with per-user Settings instances.

    API keys are deliberately not persisted. Keep one router per user's configuration;
    never mutate LiteLLM's process-global credentials in a multi-user server.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".openfoundry")
    database_url: str | None = None
    workspace_root_dir: Path | None = None
    max_mission_cost_usd: Decimal = Field(default=MAX_MISSION_COST_USD, gt=0, le=1_000_000)
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    api_token: SecretStr | None = None
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    department_plugins: tuple[str, ...] = ()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    mcp_command_allowlist: tuple[str, ...] = ()
    sandbox_mode: Literal["auto", "strict", "guarded"] = "auto"
    demo_step_delay_seconds: float = Field(default=0.15, ge=0, le=5)
    max_parallel_tasks: int = Field(default=4, ge=1, le=32)
    department_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    ollama_base_url: str | None = None
    openai_model: str = "openai/gpt-4o-mini"
    gemini_model: str = "gemini/gemini-2.5-flash"
    anthropic_model: str = "anthropic/claude-haiku-4-5"
    ollama_model: str = "ollama_chat/llama3.2"
    provider_order: tuple[Provider, ...] = ("openai", "gemini", "anthropic", "ollama")
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_output_tokens: int = Field(default=1024, gt=0, le=131_072)
    sqlite_busy_timeout_ms: int = Field(default=30_000, ge=100, le=300_000)

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("max_mission_cost_usd")
    @classmethod
    def exact_budget(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value != value.quantize(USD_QUANTUM):
            raise ValueError("Budget must be finite with at most nine decimal places")
        return value

    @field_validator("openai_api_key", "gemini_api_key", "anthropic_api_key", "api_token")
    @classmethod
    def nonempty_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("API keys must not be blank")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def valid_ollama_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama endpoint must be an HTTP(S) URL without credentials or query")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str | None) -> str | None:
        if value is not None:
            url = make_url(value)
            if url.drivername != "sqlite+aiosqlite":
                raise ValueError("DATABASE_URL must use sqlite+aiosqlite")
            if not url.database:
                raise ValueError("DATABASE_URL must name a database or :memory:")
        return value

    @model_validator(mode="after")
    def valid_routing(self) -> Settings:
        if len({server.name for server in self.mcp_servers}) != len(self.mcp_servers):
            raise ValueError("MCP server names must be unique")
        if any(
            server.transport == "stdio" and server.command not in self.mcp_command_allowlist
            for server in self.mcp_servers
        ):
            raise ValueError("Stdio MCP executable must appear in mcp_command_allowlist")
        if not self.provider_order or len(set(self.provider_order)) != len(self.provider_order):
            raise ValueError("Provider order must be nonempty and contain no duplicates")
        for provider, prefix in (
            ("openai", "openai/"),
            ("gemini", "gemini/"),
            ("anthropic", "anthropic/"),
            ("ollama", "ollama_chat/"),
        ):
            model: str = getattr(self, f"{provider}_model")
            if not model.startswith(prefix) or not model.removeprefix(prefix).strip():
                raise ValueError(f"{provider} model must start with {prefix}")
        return self

    @property
    def workspace_root(self) -> Path:
        return self.workspace_root_dir or self.data_dir / "workspaces"

    @property
    def memory_root(self) -> Path:
        return self.data_dir / "memory"

    def mission_workspace(self, mission_id: UUID | str) -> Path:
        """Return a canonical mission directory without accepting arbitrary path segments."""
        return self.workspace_root / str(UUID(str(mission_id)))

    def workspace_path(self, mission_id: UUID | str, relative_path: str) -> Path:
        """Reject traversal and existing symlink escapes; this is not a process sandbox.

        An OS/container sandbox is still required for untrusted code and for protection
        against concurrent symlink replacement between validation and opening a file.
        """
        if Path(relative_path).is_absolute():
            raise ValueError("Workspace paths must be relative")
        root = self.mission_workspace(mission_id).resolve()
        if not root.is_relative_to(self.workspace_root.resolve()):
            raise ValueError("Mission workspace escapes the workspace root")
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Path escapes the mission workspace")
        return candidate

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        fields = {
            "OPENFOUNDRY_HOME": "data_dir",
            "DATABASE_URL": "database_url",
            "OPENFOUNDRY_WORKSPACE_ROOT": "workspace_root_dir",
            "MAX_MISSION_COST_USD": "max_mission_cost_usd",
            "OPENAI_API_KEY": "openai_api_key",
            "GEMINI_API_KEY": "gemini_api_key",
            "ANTHROPIC_API_KEY": "anthropic_api_key",
            "OPENFOUNDRY_API_TOKEN": "api_token",
            "OPENFOUNDRY_SANDBOX_MODE": "sandbox_mode",
            "OPENFOUNDRY_DEMO_STEP_DELAY_SECONDS": "demo_step_delay_seconds",
            "OPENFOUNDRY_MAX_PARALLEL_TASKS": "max_parallel_tasks",
            "OPENFOUNDRY_DEPARTMENT_TIMEOUT_SECONDS": "department_timeout_seconds",
            "OLLAMA_BASE_URL": "ollama_base_url",
            "OPENFOUNDRY_OPENAI_MODEL": "openai_model",
            "OPENFOUNDRY_GEMINI_MODEL": "gemini_model",
            "OPENFOUNDRY_ANTHROPIC_MODEL": "anthropic_model",
            "OPENFOUNDRY_OLLAMA_MODEL": "ollama_model",
            "OPENFOUNDRY_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
            "OPENFOUNDRY_MAX_OUTPUT_TOKENS": "max_output_tokens",
            "OPENFOUNDRY_SQLITE_BUSY_TIMEOUT_MS": "sqlite_busy_timeout_ms",
        }
        values: dict[str, object] = {
            field: source[name] for name, field in fields.items() if source.get(name)
        }
        for variable, field in (
            ("OPENFOUNDRY_MCP_SERVERS", "mcp_servers"),
            ("OPENFOUNDRY_MCP_COMMAND_ALLOWLIST", "mcp_command_allowlist"),
        ):
            if source.get(variable):
                values[field] = json.loads(source[variable])
        if "gemini_api_key" not in values and source.get("GOOGLE_API_KEY"):
            values["gemini_api_key"] = source["GOOGLE_API_KEY"]
        if source.get("OPENFOUNDRY_PROVIDER_ORDER"):
            values["provider_order"] = tuple(
                part.strip() for part in source["OPENFOUNDRY_PROVIDER_ORDER"].split(",")
            )
        for name, field in (
            ("OPENFOUNDRY_CORS_ORIGINS", "cors_origins"),
            ("OPENFOUNDRY_DEPARTMENT_PLUGINS", "department_plugins"),
        ):
            if source.get(name):
                values[field] = tuple(
                    part.strip() for part in source[name].split(",") if part.strip()
                )
        return cls.model_validate(values)
