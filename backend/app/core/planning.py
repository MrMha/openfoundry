"""Provider-independent schemas for bounded plans and concrete task deliverables."""

from __future__ import annotations

from collections import deque
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.models.events import CodeDiffEvent, DepartmentName


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    department: DepartmentName
    title: str = Field(min_length=1, max_length=255, pattern=r"\S")
    description: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    dependencies: list[str] = Field(max_length=100)
    requires_approval: bool


class MissionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specification: str = Field(min_length=1, max_length=100_000, pattern=r"\S")
    tasks: list[TaskSpec] = Field(min_length=1, max_length=100)

    def validate_dag(self, departments: Collection[str]) -> None:
        tasks = {task.key: task for task in self.tasks}
        if len(tasks) != len(self.tasks):
            raise ValueError("Task keys must be unique")
        incoming: dict[str, int] = {}
        children: dict[str, list[str]] = {key: [] for key in tasks}
        for task in self.tasks:
            if task.department == "chief_of_staff" or task.department not in departments:
                raise ValueError("Plan references an unavailable execution department")
            if len(set(task.dependencies)) != len(task.dependencies):
                raise ValueError("Dependencies must be unique")
            incoming[task.key] = len(task.dependencies)
            for dependency in task.dependencies:
                if dependency not in tasks or dependency == task.key:
                    raise ValueError("Task dependency is missing or self-referencing")
                children[dependency].append(task.key)
        ready = deque(key for key, count in incoming.items() if not count)
        visited = 0
        while ready:
            key = ready.popleft()
            visited += 1
            for child in children[key]:
                incoming[child] -= 1
                if not incoming[child]:
                    ready.append(child)
        if visited != len(tasks):
            raise ValueError("Task dependencies contain a cycle")


class GeneratedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=1_000_000)
    artifact_type: str = Field(min_length=1, max_length=100)

    @field_validator("file_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return CodeDiffEvent.relative_file_path(value)


class WorkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=32_768, pattern=r"\S")
    files: list[GeneratedFile] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_paths(self) -> WorkResult:
        if len({file.file_path.casefold() for file in self.files}) != len(self.files):
            raise ValueError("Generated file paths must be unique, including case variants")
        return self


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Normalize Pydantic schemas to the strict structured-output object subset."""
    schema = model.model_json_schema()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema
