"""Committed browser contracts stay aligned with the backend's public wire formats."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from backend.app.departments.qa_self_healer import ProbeReport
from backend.app.main import create_app
from backend.app.models.events import WarRoomEvent, parse_event

CONTRACTS = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_committed_browser_event_schema_and_every_event_example_match_pydantic():
    expected = TypeAdapter(WarRoomEvent).json_schema(mode="serialization")
    assert json.loads((CONTRACTS / "services/events.schema.json").read_text()) == expected
    assert json.loads((CONTRACTS / "services/probes.schema.json").read_text()) == (
        ProbeReport.model_json_schema(mode="serialization")
    )
    examples = json.loads((CONTRACTS / "test/events.json").read_text())
    assert len({example["type"] for example in examples}) == 7
    for example in examples:
        assert parse_event(json.dumps(example)).model_dump(mode="json") == example


def test_committed_openapi_paths_payloads_and_responses_match_fastapi():
    current = create_app().openapi()
    saved = json.loads((CONTRACTS / "services/openapi.json").read_text())
    assert saved["paths"] == current["paths"]
    # Event schemas intentionally augment the OpenAPI document for WebSocket clients.
    for name, schema in current["components"]["schemas"].items():
        assert saved["components"]["schemas"][name] == schema
