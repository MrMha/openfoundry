"""Bounded, validated mission templates shared by API, planning, and UI."""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.config import MissionPreferences
from backend.app.core.planning import MissionPlan, TaskSpec
from backend.app.models.events import CodeDiffEvent

BLUEPRINT_ROOT = Path(__file__).resolve().parents[3] / "blueprints"
DEPARTMENTS = {"researcher", "engineer", "qa_self_healer", "growth_career"}


class BlueprintParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    kind: Literal["text", "url", "path"]
    required: bool = True
    default: str = Field(max_length=4000)


class Blueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    goal_template: str = Field(min_length=1, max_length=12000)
    parameters: list[BlueprintParameter] = Field(max_length=16)
    suggested_configuration: MissionPreferences
    tasks: list[TaskSpec] = Field(min_length=1, max_length=30)
    deliverables: list[str] = Field(min_length=1, max_length=30)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_template(self) -> Blueprint:
        keys = {parameter.key for parameter in self.parameters}
        if len(keys) != len(self.parameters):
            raise ValueError("Blueprint parameter keys must be unique")
        MissionPlan(specification=self.description, tasks=self.tasks).validate_dag(DEPARTMENTS)
        for text in [self.goal_template, *(task.description for task in self.tasks)]:
            template = Template(text)
            if not template.is_valid() or not set(template.get_identifiers()) <= keys:
                raise ValueError("Blueprint contains unknown or invalid parameter references")
        if any(task.department == "engineer" and not task.requires_approval for task in self.tasks):
            raise ValueError("Blueprint engineering tasks must retain an approval gate")
        return self

    def resolve(self, values: dict[str, str]) -> tuple[str, list[TaskSpec], dict[str, str]]:
        if set(values) - {parameter.key for parameter in self.parameters}:
            raise ValueError("Unknown blueprint parameter")
        resolved: dict[str, str] = {}
        for parameter in self.parameters:
            value = values.get(parameter.key, parameter.default).strip()
            if len(value) > 4000 or parameter.required and not value:
                raise ValueError(f"Blueprint parameter {parameter.key} is required and bounded")
            if value and parameter.kind == "url":
                from urllib.parse import urlsplit

                url = urlsplit(value)
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.fragment
                ):
                    raise ValueError("Blueprint URL must be HTTP(S) without credentials")
            if value and parameter.kind == "path":
                CodeDiffEvent.relative_file_path(value)
            resolved[parameter.key] = value
        goal = Template(self.goal_template).substitute(resolved)
        tasks = [
            task.model_copy(update={"description": Template(task.description).substitute(resolved)})
            for task in self.tasks
        ]
        if len(goal) > 16384:
            raise ValueError("Resolved blueprint goal exceeds mission limits")
        return goal, tasks, resolved


def load_blueprints(root: Path | None = None) -> list[Blueprint]:
    directory = root if root is not None else BLUEPRINT_ROOT
    paths = sorted(directory.glob("*.json"))
    if not paths or len(paths) > 32:
        raise ValueError("Blueprint directory must contain between one and 32 templates")
    result = []
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 131072:
            raise ValueError("Blueprint files must be regular bounded JSON files")
        template = Blueprint.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != template.id:
            raise ValueError("Blueprint filename and identifier must match")
        template.resolve({})
        result.append(template)
    return result


def planning_blueprint(identifier: str, parameters: dict[str, str]) -> tuple[str, dict[str, str]]:
    template = next((item for item in load_blueprints() if item.id == identifier), None)
    if template is None:
        raise ValueError("Blueprint does not exist")
    _, tasks, resolved = template.resolve(parameters)
    return json.dumps(
        {
            "blueprint_id": template.id,
            "template_tasks": [task.model_dump() for task in tasks],
            "deliverables": template.deliverables,
            "acceptance_criteria": template.acceptance_criteria,
            "parameters": resolved,
        }
    ), resolved
