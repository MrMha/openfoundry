from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", openai_api_key="unit-test-key")


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    async with Database(settings) as database:
        yield database
