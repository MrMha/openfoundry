"""Regenerate browser contracts from the installed backend, without server startup."""

import base64
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from backend.app.departments.qa_self_healer import ProbeReport
from backend.app.main import create_app
from backend.app.models.events import (
    AgentThoughtEvent,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    BrowserFrameEvent,
    CodeDiffEvent,
    MissionStatusChangeEvent,
    TaskStatusChangeEvent,
    TestResult,
    WarRoomEvent,
)

root = Path(__file__).resolve().parents[1] / "src" / "services"
api = create_app().openapi()
events = TypeAdapter(WarRoomEvent).json_schema(mode="serialization")
api["components"]["schemas"].update(deepcopy(events["$defs"]))
probes = ProbeReport.model_json_schema(mode="serialization")
api["components"]["schemas"].update(deepcopy(probes["$defs"]))
api["components"]["schemas"]["ProbeReport"] = {
    key: deepcopy(value) for key, value in probes.items() if key != "$defs"
}


def refs(value: object) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("$ref"), str):
            value["$ref"] = value["$ref"].replace("#/$defs/", "#/components/schemas/")
        for child in value.values():
            refs(child)
    elif isinstance(value, list):
        for child in value:
            refs(child)


api["components"]["schemas"]["WarRoomEvent"] = {"oneOf": deepcopy(events["oneOf"])}
refs(api)
(root / "openapi.json").write_text(json.dumps(api, indent=2) + "\n")
(root / "events.schema.json").write_text(json.dumps(events, indent=2) + "\n")
(root / "probes.schema.json").write_text(json.dumps(probes, indent=2) + "\n")

# Every concrete event variant, serialized by the backend itself, for browser tests.
mission = UUID("00000000-0000-4000-8000-000000000001")
task = UUID("00000000-0000-4000-8000-000000000002")
approval = UUID("00000000-0000-4000-8000-000000000003")
base = dict(mission_id=mission, timestamp=datetime(2026, 1, 1, tzinfo=UTC))
examples = [
    MissionStatusChangeEvent(**base, status="running", title="Calculator", goal="Add integers"),
    TaskStatusChangeEvent(
        **base,
        task_id=task,
        department="engineer",
        status="running",
        title="Build calculator",
        description="Implement addition",
    ),
    AgentThoughtEvent(**base, task_id=task, department="engineer", thought="Building calculator"),
    BrowserFrameEvent(
        **base,
        task_id=task,
        current_url="http://localhost/",
        screenshot_base64=base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(),
    ),
    CodeDiffEvent(
        **base,
        task_id=task,
        file_path="app.py",
        unified_diff="-a - b\n+a + b\n",
        test_result=TestResult(status="passed", return_code=0),
    ),
    ApprovalRequestEvent(
        **base,
        task_id=task,
        approval_id=approval,
        department="engineer",
        title="Review plan",
        description="Review SPEC.md",
        approval_type="plan",
    ),
    ApprovalDecisionEvent(
        **base, task_id=task, approval_id=approval, department="engineer", decision="approved"
    ),
]
(root.parent / "test" / "events.json").write_text(
    json.dumps(
        [
            event.model_copy(update={"sequence": i, "event_id": UUID(int=i + 10)}).model_dump(
                mode="json"
            )
            for i, event in enumerate(examples, 1)
        ],
        indent=2,
    )
    + "\n"
)
