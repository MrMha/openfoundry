import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.models.events import (
    AgentThoughtEvent,
    ApprovalRequestEvent,
    BrowserFrameEvent,
    CodeDiffEvent,
    Department,
    TaskStatusChangeEvent,
    parse_event,
)
from backend.app.models.events import (
    TestResult as WarRoomTestResult,
)
from backend.app.models.mission import TaskStatus

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1cAAAAASUVORK5CYII="


def test_all_event_types_round_trip_as_discriminated_json() -> None:
    mission_id, task_id = uuid4(), uuid4()
    events = [
        AgentThoughtEvent(
            mission_id=mission_id, department=Department.RESEARCH, thought="Reviewing sources"
        ),
        TaskStatusChangeEvent(
            mission_id=mission_id,
            department=Department.ENGINEERING,
            task_id=task_id,
            previous_status=TaskStatus.PENDING,
            status=TaskStatus.RUNNING,
        ),
        BrowserFrameEvent(
            mission_id=mission_id,
            screenshot_base64=PNG,
            current_url="https://example.com/app",
            console_errors=("ReferenceError: missing",),
        ),
        CodeDiffEvent(
            mission_id=mission_id,
            file_path="src/app.py",
            unified_diff="@@ -1 +1 @@\n-old\n+new\n",
            test_result=WarRoomTestResult(status="passed", return_code=0, command=("pytest", "-q")),
        ),
        ApprovalRequestEvent(
            mission_id=mission_id,
            department=Department.CHIEF_OF_STAFF,
            title="Review plan",
            description="Approve the mission specification",
            approval_type="plan",
            context={"files": ["SPEC.md"], "budget_usd": 2},
        ),
    ]
    for event in events:
        restored = parse_event(event.model_dump_json())
        assert type(restored) is type(event)
        assert restored == event
        assert restored.timestamp.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "image",
    ["not base64!", "data:image/png;base64," + PNG, base64.b64encode(b"not an image").decode()],
)
def test_screenshot_validation(image: str) -> None:
    with pytest.raises(ValidationError):
        BrowserFrameEvent(mission_id=uuid4(), screenshot_base64=image, current_url="about:blank")


def test_image_type_must_match_and_urls_must_not_contain_credentials() -> None:
    with pytest.raises(ValidationError):
        BrowserFrameEvent(
            mission_id=uuid4(),
            screenshot_base64=PNG,
            screenshot_mime_type="image/jpeg",
            current_url="about:blank",
        )
    with pytest.raises(ValidationError):
        BrowserFrameEvent(
            mission_id=uuid4(),
            screenshot_base64=PNG,
            current_url="https://user:password@example.com",
        )


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", r"C:\secret", "a/../../b", ".git/config", "a//b", "a\x00b"]
)
def test_diff_paths_are_relative_and_canonical(path: str) -> None:
    with pytest.raises(ValidationError):
        CodeDiffEvent(
            mission_id=uuid4(),
            file_path=path,
            unified_diff="",
            test_result=WarRoomTestResult(status="not_run"),
        )


def test_unknown_types_fields_and_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event('{"type":"unknown","mission_id":"' + str(uuid4()) + '"}')
    with pytest.raises(ValidationError):
        AgentThoughtEvent.model_validate(
            {"mission_id": uuid4(), "department": "research", "thought": "Think", "extra": True}
        )
    with pytest.raises(ValidationError):
        AgentThoughtEvent(
            mission_id=uuid4(),
            department=Department.RESEARCH,
            thought="Think",
            timestamp=datetime(2026, 1, 1),
        )


def test_invalid_status_transitions_and_test_results_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskStatusChangeEvent(
            mission_id=uuid4(),
            department=Department.QA,
            task_id=uuid4(),
            status=TaskStatus.RUNNING,
            previous_status=TaskStatus.RUNNING,
        )
    for status, code in (("passed", 1), ("failed", 0), ("not_run", 0)):
        with pytest.raises(ValidationError):
            WarRoomTestResult.model_validate({"status": status, "return_code": code})


def test_approval_expiration_must_follow_timestamp() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ApprovalRequestEvent(
            mission_id=uuid4(),
            department=Department.CHIEF_OF_STAFF,
            title="Review",
            description="Review spec",
            approval_type="plan",
            timestamp=now,
            expires_at=now - timedelta(seconds=1),
        )
