"""Versioned, discriminated War Room events with JSON-safe payloads."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    RootModel,
    field_validator,
    model_validator,
)

from backend.app.models.mission import MissionStatus, TaskStatus

MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
NonemptyText = Annotated[str, Field(min_length=1, max_length=32_768, pattern=r"\S")]
DepartmentName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Department(StrEnum):
    CHIEF_OF_STAFF = "chief_of_staff"
    RESEARCH = "research"
    ENGINEERING = "engineering"
    QA = "qa"
    GROWTH = "growth"


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    event_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: Literal[1] = 1
    sequence: int | None = Field(default=None, ge=1)

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class MissionStatusChangeEvent(Event):
    type: Literal["mission_status_change"] = "mission_status_change"
    previous_status: MissionStatus | None = None
    status: MissionStatus
    title: str
    goal: str
    error: str | None = None


class AgentThoughtEvent(Event):
    type: Literal["agent_thought"] = "agent_thought"
    department: Department | DepartmentName
    task_id: UUID | None = None
    thought: NonemptyText
    level: Literal["info", "warning", "error"] = "info"


class TaskStatusChangeEvent(Event):
    type: Literal["task_status_change"] = "task_status_change"
    department: Department | DepartmentName
    task_id: UUID
    previous_status: TaskStatus | None = None
    status: TaskStatus
    output_summary: str | None = Field(default=None, max_length=65_536)
    title: str | None = None
    description: str | None = None
    dependencies: tuple[UUID, ...] = ()
    requires_approval: bool = False

    @model_validator(mode="after")
    def changed_status(self) -> TaskStatusChangeEvent:
        if self.previous_status == self.status:
            raise ValueError("A status change must change the task's status")
        return self


class BrowserFrameEvent(Event):
    type: Literal["browser_frame"] = "browser_frame"
    department: Department | DepartmentName = Department.QA
    task_id: UUID | None = None
    frame_number: int = Field(default=0, ge=0, strict=True)
    screenshot_base64: str = Field(min_length=1, max_length=4 * ((MAX_SCREENSHOT_BYTES + 2) // 3))
    screenshot_mime_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png"
    current_url: HttpUrl | Literal["about:blank"]
    console_errors: tuple[Annotated[str, Field(min_length=1, max_length=4096)], ...] = Field(
        default=(), max_length=100
    )

    @model_validator(mode="after")
    def valid_frame(self) -> BrowserFrameEvent:
        try:
            decoded = base64.b64decode(self.screenshot_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError(
                "Screenshot must be standard base64 without a data-URL prefix"
            ) from None
        if len(decoded) > MAX_SCREENSHOT_BYTES:
            raise ValueError("Screenshot exceeds the decoded size limit")
        signatures = {
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/webp": decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP",
        }
        if not signatures[self.screenshot_mime_type]:
            raise ValueError("Screenshot signature does not match its declared image type")
        if isinstance(self.current_url, HttpUrl) and (
            self.current_url.username is not None or self.current_url.password is not None
        ):
            raise ValueError("Browser URLs must not embed credentials")
        return self


class TestResult(BaseModel):
    """A command result that can be attached to a generated code diff."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    status: Literal["passed", "failed", "error", "skipped", "not_run", "timed_out"]
    command: tuple[str, ...] = ()
    return_code: int | None = Field(default=None, strict=True)
    duration_seconds: float = Field(default=0, ge=0)
    output_summary: str = Field(default="", max_length=65_536)

    @model_validator(mode="after")
    def consistent_result(self) -> TestResult:
        if self.status == "passed" and self.return_code != 0:
            raise ValueError("A passing test result must have return code zero")
        if self.status == "failed" and (self.return_code is None or self.return_code == 0):
            raise ValueError("A failed test result must have a nonzero return code")
        if self.status in {"skipped", "not_run"} and self.return_code is not None:
            raise ValueError("Unexecuted tests cannot have a return code")
        return self


class CodeDiffEvent(Event):
    type: Literal["code_diff"] = "code_diff"
    department: Department | DepartmentName = Department.ENGINEERING
    task_id: UUID | None = None
    file_path: str = Field(min_length=1, max_length=4096)
    unified_diff: str = Field(max_length=2 * 1024 * 1024)
    test_result: TestResult

    @field_validator("file_path")
    @classmethod
    def relative_file_path(cls, value: str) -> str:
        parts = value.split("/")
        if (
            "\\" in value
            or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in parts)
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Diff paths must be canonical, relative workspace file paths")
        return value


class ApprovalRequestEvent(Event):
    type: Literal["approval_request"] = "approval_request"
    approval_id: UUID = Field(default_factory=uuid4)
    department: Department | DepartmentName
    task_id: UUID | None = None
    title: Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
    description: NonemptyText
    approval_type: Literal["plan", "command", "code_change", "budget", "publication"]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def future_expiry(self) -> ApprovalRequestEvent:
        if self.expires_at is not None and self.expires_at <= self.timestamp:
            raise ValueError("Approval expiration must follow the event timestamp")
        return self


class ApprovalDecisionEvent(Event):
    type: Literal["approval_decision"] = "approval_decision"
    approval_id: UUID
    task_id: UUID
    department: Department | DepartmentName
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=4096)


WarRoomEvent = Annotated[
    MissionStatusChangeEvent
    | AgentThoughtEvent
    | TaskStatusChangeEvent
    | BrowserFrameEvent
    | CodeDiffEvent
    | ApprovalRequestEvent
    | ApprovalDecisionEvent,
    Field(discriminator="type"),
]


class WarRoomEventEnvelope(RootModel[WarRoomEvent]):
    """A JSON root union, preserving precise types across Pydantic versions."""


def parse_event(payload: str | bytes) -> WarRoomEvent:
    """Validate an incoming serialized event without guessing its event type."""
    return WarRoomEventEnvelope.model_validate_json(payload).root
