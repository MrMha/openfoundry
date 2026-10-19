from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Keep unit accounting examples on their explicit small, single-attempt budget.
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="unit-test-key",
        max_output_tokens=1024,
        provider_max_attempts=1,
    )


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    async with Database(settings) as database:
        yield database
