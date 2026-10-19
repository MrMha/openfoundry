import asyncio
import math
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.core.memory_vault import (
    MemoryCapacityError,
    MemoryKind,
    MemoryVault,
    MemoryVaultError,
)
from backend.app.models.events import Department


@pytest.fixture
def vault(tmp_path: Path) -> MemoryVault:
    return MemoryVault(uuid4(), storage_root=tmp_path / "memory")


async def test_all_departments_share_persistent_memory(vault: MemoryVault) -> None:
    created = []
    for department in Department:
        entry = await vault.remember(
            MemoryKind.RESEARCH_FINDING,
            f"Finding from {department}",
            "Database persistence uses SQLite WAL",
            department=department,
        )
        created.append(entry.id)
    reopened = MemoryVault(vault.mission_id, storage_root=vault.path.parent)
    matches = await reopened.search("database persistence", limit=10)
    assert {match.entry.id for match in matches} == set(created)
    assert {match.entry.department for match in matches} == set(Department)
    assert all(match.entry.created_at.utcoffset().total_seconds() == 0 for match in matches)


async def test_mission_isolation(vault: MemoryVault) -> None:
    entry = await vault.remember(MemoryKind.BUG_REPORT, "Secret bug", "Renderer crashes on submit")
    other = MemoryVault(uuid4(), storage_root=vault.path.parent)
    assert await other.get(entry.id) is None
    assert await other.search("renderer") == ()
    assert not await other.delete(entry.id)
    assert await vault.get(entry.id) == entry


async def test_ranked_retrieval_filters_and_unrelated_documents(vault: MemoryVault) -> None:
    best = await vault.remember(
        MemoryKind.ARCHITECTURE_DECISION,
        "Database persistence",
        "Database persistence",
        department=Department.ENGINEERING,
    )
    await vault.remember(
        MemoryKind.BUG_REPORT, "Database bug", "Database timeout", department=Department.QA
    )
    await vault.remember(
        MemoryKind.RESEARCH_FINDING,
        "Sales",
        "Find customers through community outreach",
        department=Department.GROWTH,
    )
    matches = await vault.search("database persistence")
    assert matches[0].entry.id == best.id
    assert len(matches) == 2
    assert matches[0].score > matches[1].score
    assert matches[0].matched_terms == ("database", "persistence")
    assert len(await vault.search("database", kind=MemoryKind.BUG_REPORT)) == 1
    assert len(await vault.search("database", department=Department.ENGINEERING)) == 1
    assert await vault.search("nooverlapuniqueterm") == ()
    assert await vault.search("the and is") == ()
    assert await vault.search("database", min_score=1) == ()


async def test_cosine_matches_known_vector(vault: MemoryVault) -> None:
    await vault.remember(MemoryKind.RESEARCH_FINDING, "alpha", "alpha beta")
    match = (await vault.search("alpha"))[0]
    alpha_weight = 1 + math.log(3)
    expected = alpha_weight / math.sqrt(alpha_weight**2 + 1)
    assert match.cosine_similarity == pytest.approx(expected)
    assert match.keyword_score == 1
    assert match.score == pytest.approx(0.75 * expected + 0.25)


async def test_unicode_and_case_normalization(vault: MemoryVault) -> None:
    entry = await vault.remember(MemoryKind.RESEARCH_FINDING, "ＤＡＴＡＢＡＳＥ", "Résumé parsing")
    assert (await vault.search("database"))[0].entry.id == entry.id
    assert (await vault.search("RÉSUMÉ"))[0].entry.id == entry.id


async def test_document_chunks_and_canonical_spec(vault: MemoryVault) -> None:
    chunks = await vault.store_document_chunks(
        "https://example.com/document.pdf", ["First page: architecture", "Second page: testing"]
    )
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == [0, 1]
    assert all(chunk.kind == MemoryKind.DOCUMENT_CHUNK for chunk in chunks)
    assert all(chunk.source == "https://example.com/document.pdf" for chunk in chunks)
    first = await vault.save_spec("# SPEC\nUse SQLite persistence")
    updated = await vault.save_spec("# SPEC\nUse async SQLite persistence")
    assert updated.id == first.id
    assert updated.created_at == first.created_at
    assert updated.updated_at >= first.updated_at
    assert await vault.get_spec() == updated
    assert len(await vault.search("persistence")) == 1


async def test_metadata_is_snapshotted_and_delete_persists(vault: MemoryVault) -> None:
    metadata = {"tags": ["sqlite"]}
    entry = await vault.remember(
        MemoryKind.RESEARCH_FINDING, "Storage", "Persistent storage", metadata=metadata
    )
    metadata["tags"].append("changed")
    assert (await vault.get(entry.id)).metadata == {"tags": ["sqlite"]}
    assert await vault.delete(entry.id)
    assert not await vault.delete(entry.id)
    assert await vault.get(entry.id) is None


async def test_capacity_failures_roll_back_batch(tmp_path: Path) -> None:
    limited = MemoryVault(uuid4(), storage_root=tmp_path, max_entries=1)
    with pytest.raises(MemoryCapacityError):
        await limited.store_document_chunks("document.pdf", ["First chunk", "Second chunk"])
    assert await limited.search("chunk") == ()
    entry = await limited.remember(MemoryKind.BUG_REPORT, "Bug", "Renderer crash")
    with pytest.raises(MemoryCapacityError):
        await limited.save_spec("# SPEC\nBuild renderer")
    assert await limited.get(entry.id) == entry
    assert await limited.get_spec() is None


async def test_parallel_writers_observe_transactional_capacity(tmp_path: Path) -> None:
    mission_id = uuid4()
    first = MemoryVault(mission_id, storage_root=tmp_path, max_entries=1)
    second = MemoryVault(mission_id, storage_root=tmp_path, max_entries=1)
    await first.initialize()
    await second.initialize()
    results = await asyncio.gather(
        first.remember(MemoryKind.BUG_REPORT, "Bug", "Renderer first crash"),
        second.remember(MemoryKind.BUG_REPORT, "Bug", "Renderer second crash"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, MemoryCapacityError) for result in results) == 1
    assert len(await first.search("renderer")) == 1


async def test_invalid_data_is_not_partially_persisted(vault: MemoryVault) -> None:
    with pytest.raises(ValidationError):
        await vault.store_document_chunks("document.pdf", ["Valid content", " "])
    assert await vault.search("valid") == ()
    with pytest.raises(ValueError):
        await vault.remember(
            MemoryKind.RESEARCH_FINDING, "Title", "Content", metadata={"price": float("nan")}
        )
    with pytest.raises(ValueError):
        await vault.search("query", min_score=float("nan"))
    with pytest.raises(ValueError):
        await vault.search("query", limit=0)


async def test_future_schema_versions_and_symlink_databases_fail_closed(tmp_path: Path) -> None:
    vault = MemoryVault(uuid4(), storage_root=tmp_path)
    with sqlite3.connect(vault.path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(MemoryVaultError, match="version"):
        await vault.initialize()
    other = MemoryVault(uuid4(), storage_root=tmp_path)
    other.path.symlink_to(vault.path)
    with pytest.raises(MemoryVaultError, match="symlink"):
        await other.initialize()
