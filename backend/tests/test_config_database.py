from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models.mission import Artifact, LLMAttempt, Mission, TaskNode


def test_environment_and_user_keys_are_isolated(tmp_path: Path) -> None:
    first = Settings.from_env(
        {
            "OPENFOUNDRY_HOME": str(tmp_path),
            "MAX_MISSION_COST_USD": "1.25",
            "OPENAI_API_KEY": "test-first",
            "GOOGLE_API_KEY": "test-gemini",
            "OLLAMA_BASE_URL": "http://localhost:11434/",
            "OPENFOUNDRY_PROVIDER_ORDER": "ollama,openai",
        }
    )
    second = Settings(openai_api_key="test-second")
    assert first.max_mission_cost_usd == Decimal("1.25")
    assert first.provider_order == ("ollama", "openai")
    assert first.ollama_base_url == "http://localhost:11434"
    assert first.gemini_api_key.get_secret_value() == "test-gemini"
    assert first.openai_api_key.get_secret_value() != second.openai_api_key.get_secret_value()
    assert "test-first" not in repr(first)
    assert not (tmp_path / "workspaces").exists()


@pytest.mark.parametrize("budget", ["-1", "0", "NaN", "Infinity", "0.0000000001"])
def test_invalid_budgets_fail(budget: str) -> None:
    with pytest.raises(ValidationError):
        Settings(max_mission_cost_usd=budget)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"database_url": "sqlite:///wrong-driver.db"},
        {"ollama_base_url": "http://user:password@localhost:11434"},
        {"ollama_base_url": "file:///tmp/model"},
        {"openai_model": "anthropic/claude-haiku-4-5"},
        {"provider_order": ("openai", "openai")},
        {"openai_api_key": "   "},
    ],
)
def test_invalid_configuration_fails(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(kwargs)


def test_workspace_paths_reject_escape_and_symlink(settings: Settings, tmp_path: Path) -> None:
    mission_id = uuid4()
    root = settings.mission_workspace(mission_id)
    root.mkdir(parents=True)
    (root / "escape").symlink_to(tmp_path)
    assert settings.workspace_path(mission_id, "src/app.py") == root / "src/app.py"
    for path in ("../other", "/etc/passwd", "escape/secret"):
        with pytest.raises(ValueError):
            settings.workspace_path(mission_id, path)
    with pytest.raises(ValueError):
        settings.mission_workspace("../invalid")


async def test_persistence_exact_costs_and_cascade(database: Database, settings: Settings) -> None:
    async with database.session() as session:
        mission = Mission(
            title="Build", goal="Ship a product", total_cost_usd=Decimal("0.100000001")
        )
        session.add(mission)
        await session.flush()
        mission_id = mission.id
        session.add_all(
            [
                TaskNode(
                    mission_id=mission_id,
                    department="engineering",
                    title="Implement",
                    description="Build the product",
                    requires_approval=True,
                ),
                Artifact(
                    mission_id=mission_id,
                    file_path="SPEC.md",
                    artifact_type="specification",
                    content_preview="# Product",
                ),
            ]
        )
    async with Database(settings) as reopened:
        async with reopened.session() as session:
            persisted = await session.get(Mission, mission_id)
            assert persisted.total_cost_usd == Decimal("0.100000001")
            assert persisted.created_at.utcoffset().total_seconds() == 0
            task = await session.scalar(select(TaskNode))
            assert task.requires_approval is True
            assert task.dependencies_json == "[]"
            await session.execute(delete(Mission).where(Mission.id == mission_id))
        async with reopened.session() as session:
            for model in (TaskNode, Artifact, LLMAttempt):
                assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_transaction_rollback_and_foreign_keys(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            session.add(Mission(title="Rollback", goal="Not persisted"))
            await session.flush()
            raise RuntimeError("test rollback")
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(Mission)) == 0
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(Artifact(mission_id=uuid4(), file_path="orphan", artifact_type="text"))


@pytest.mark.parametrize("dependencies", ["not json", "{}", '"text"'])
async def test_task_dependencies_must_be_json_array(database: Database, dependencies: str) -> None:
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            mission = Mission(title="Build", goal="Ship")
            session.add(mission)
            await session.flush()
            session.add(
                TaskNode(
                    mission_id=mission.id,
                    department="qa",
                    title="Test",
                    description="Verify",
                    dependencies_json=dependencies,
                )
            )


def test_app_health_initializes_and_queries_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
