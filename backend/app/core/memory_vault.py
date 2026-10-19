"""Mission-scoped SQLite memory with deterministic keyword and TF-IDF retrieval."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4, uuid5

import aiosqlite
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from backend.app.models.events import Department, DepartmentName

_STOPWORDS = frozenset(
    "a an and are as at be been by for from has have in is it its of on or that the "
    "their this to was were will with".split()
)
_SCHEMA_VERSION = 1


class MemoryVaultError(RuntimeError):
    """A mission memory operation could not be completed."""


class MemoryCapacityError(MemoryVaultError):
    """An atomic write would exceed this mission's configured storage limits."""


class MemoryKind(StrEnum):
    RESEARCH_FINDING = "research_finding"
    DOCUMENT_CHUNK = "document_chunk"
    ARCHITECTURE_DECISION = "architecture_decision"
    BUG_REPORT = "bug_report"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    id: UUID
    mission_id: UUID
    kind: MemoryKind
    title: Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
    content: Annotated[str, Field(min_length=1, max_length=1_000_000, pattern=r"\S")]
    department: Department | DepartmentName | None = None
    source: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MemoryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    entry: MemoryEntry
    score: float = Field(ge=0, le=1)
    cosine_similarity: float = Field(ge=0, le=1)
    keyword_score: float = Field(ge=0, le=1)
    matched_terms: tuple[str, ...]


class MemoryVault:
    """A persistent, shared brain for all departments working on one mission.

    Each mission has its own database under a trusted, operator-owned ``storage_root``.
    Keep that root outside writable project directories. Connections are short lived;
    independent objects and processes share committed writes through SQLite WAL.
    No model, remote embedding service, or extra numerical dependency is required.
    """

    def __init__(
        self,
        mission_id: UUID | str,
        *,
        storage_root: Path | str = Path(".openfoundry/memory"),
        max_entries: int = 10_000,
        max_total_characters: int = 10_000_000,
    ) -> None:
        if not 1 <= max_entries <= 100_000:
            raise ValueError("max_entries must be between one and 100000")
        if not 1 <= max_total_characters <= 100_000_000:
            raise ValueError("max_total_characters must be between one and 100000000")
        self.mission_id = UUID(str(mission_id))
        self.path = Path(storage_root).expanduser().absolute() / f"{self.mission_id}.sqlite3"
        self.max_entries = max_entries
        self.max_total_characters = max_total_characters
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        # The storage root is a trusted service directory, never an agent-controlled path.
        if self.path.is_symlink():
            raise MemoryVaultError("Memory database must not be a symlink")
        async with aiosqlite.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("PRAGMA busy_timeout=30000")
            await connection.execute("PRAGMA synchronous=FULL")
            yield connection

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self.path.parent.mkdir, mode=0o700, parents=True, exist_ok=True)
            async with self._connection() as connection:
                async with connection.execute("PRAGMA user_version") as cursor:
                    version = await cursor.fetchone()
                if version is None or version[0] not in {0, _SCHEMA_VERSION}:
                    raise MemoryVaultError("Memory schema version is not supported")
                await connection.execute("PRAGMA journal_mode=WAL")
                await connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS entries (
                        id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        kind TEXT NOT NULL CHECK (kind IN (
                            'research_finding', 'document_chunk',
                            'architecture_decision', 'bug_report'
                        )),
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        department TEXT,
                        source TEXT,
                        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_entries_kind ON entries(kind);
                    CREATE INDEX IF NOT EXISTS ix_entries_department ON entries(department);
                    PRAGMA user_version=1;
                    """
                )
                await connection.commit()
            self._initialized = True

    @staticmethod
    def _decode(row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry.model_validate(
            {
                "id": row["id"],
                "mission_id": row["mission_id"],
                "kind": row["kind"],
                "title": row["title"],
                "content": row["content"],
                "department": row["department"],
                "source": row["source"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def _entry(
        self,
        kind: MemoryKind,
        title: str,
        content: str,
        department: Department | str | None,
        source: str | None,
        metadata: dict[str, JsonValue] | None,
        entry_id: UUID | None = None,
    ) -> MemoryEntry:
        now = datetime.now(UTC)
        # Serialize now to reject NaN and snapshot mutable caller metadata before awaiting.
        encoded_metadata = json.dumps(metadata or {}, allow_nan=False, ensure_ascii=False)
        if len(encoded_metadata.encode("utf-8")) > 16_384:
            raise ValueError("Memory metadata exceeds 16 KiB")
        return MemoryEntry(
            id=entry_id or uuid4(),
            mission_id=self.mission_id,
            kind=kind,
            title=title,
            content=content,
            department=department,
            source=source,
            metadata=json.loads(encoded_metadata),
            created_at=now,
            updated_at=now,
        )

    async def _store(self, entries: Sequence[MemoryEntry]) -> tuple[MemoryEntry, ...]:
        await self.initialize()
        saved: list[MemoryEntry] = []
        async with self._connection() as connection:
            try:
                # Limits and writes share one write transaction across all writers.
                await connection.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    await connection.execute(
                        """
                        INSERT INTO entries
                            (id, mission_id, kind, title, content, department, source,
                             metadata_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            kind=excluded.kind, title=excluded.title, content=excluded.content,
                            department=excluded.department, source=excluded.source,
                            metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
                        WHERE entries.mission_id=excluded.mission_id
                        """,
                        (
                            str(entry.id),
                            str(entry.mission_id),
                            entry.kind.value,
                            entry.title,
                            entry.content,
                            str(entry.department) if entry.department else None,
                            entry.source,
                            json.dumps(entry.metadata, allow_nan=False, ensure_ascii=False),
                            entry.created_at.isoformat(),
                            entry.updated_at.isoformat(),
                        ),
                    )
                async with connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(length(content) + length(title)), 0) "
                    "FROM entries"
                ) as cursor:
                    totals = await cursor.fetchone()
                if (
                    totals is None
                    or totals[0] > self.max_entries
                    or totals[1] > self.max_total_characters
                ):
                    raise MemoryCapacityError("Mission memory capacity would be exceeded")
                for entry in entries:
                    async with connection.execute(
                        "SELECT * FROM entries WHERE id=? AND mission_id=?",
                        (str(entry.id), str(self.mission_id)),
                    ) as cursor:
                        row = await cursor.fetchone()
                    if row is None:
                        raise MemoryVaultError("Memory entry could not be persisted")
                    saved.append(self._decode(row))
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return tuple(saved)

    async def remember(
        self,
        kind: MemoryKind,
        title: str,
        content: str,
        *,
        department: Department | str | None = None,
        source: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> MemoryEntry:
        """Append a finding, document chunk, decision, or bug report."""
        entry = self._entry(kind, title, content, department, source, metadata)
        return (await self._store((entry,)))[0]

    async def store_document_chunks(
        self,
        source: str,
        chunks: Sequence[str],
        *,
        department: Department | str = Department.RESEARCH,
    ) -> tuple[MemoryEntry, ...]:
        """Store already-parsed chunks atomically, preserving source and chunk order."""
        if isinstance(chunks, (str, bytes)) or not 1 <= len(chunks) <= 1000:
            raise ValueError("Provide between one and 1000 parsed document chunks")
        if not source.strip() or len(source) > 4096:
            raise ValueError("A nonempty document source of at most 4096 characters is required")
        entries = [
            self._entry(
                MemoryKind.DOCUMENT_CHUNK,
                f"Document chunk {index + 1}",
                content,
                department,
                source,
                {"chunk_index": index, "chunk_count": len(chunks)},
            )
            for index, content in enumerate(chunks)
        ]
        return await self._store(entries)

    async def save_spec(
        self, content: str, *, department: Department = Department.CHIEF_OF_STAFF
    ) -> MemoryEntry:
        """Upsert the mission's canonical SPEC.md memory, preserving its creation time.

        This records the specification in shared memory. Writing the project file is
        explicitly a WorkspaceSandbox operation, so memory cannot bypass file permissions.
        """
        entry = self._entry(
            MemoryKind.ARCHITECTURE_DECISION,
            "SPEC.md",
            content,
            department,
            "SPEC.md",
            {"canonical": True},
            uuid5(self.mission_id, "SPEC.md"),
        )
        return (await self._store((entry,)))[0]

    async def get(self, entry_id: UUID | str) -> MemoryEntry | None:
        await self.initialize()
        async with self._connection() as connection:
            async with connection.execute(
                "SELECT * FROM entries WHERE id=? AND mission_id=?",
                (str(UUID(str(entry_id))), str(self.mission_id)),
            ) as cursor:
                row = await cursor.fetchone()
        return self._decode(row) if row is not None else None

    async def get_spec(self) -> MemoryEntry | None:
        return await self.get(uuid5(self.mission_id, "SPEC.md"))

    async def delete(self, entry_id: UUID | str) -> bool:
        await self.initialize()
        async with self._connection() as connection:
            async with connection.execute(
                "DELETE FROM entries WHERE id=? AND mission_id=?",
                (str(UUID(str(entry_id))), str(self.mission_id)),
            ) as cursor:
                deleted = cursor.rowcount > 0
            await connection.commit()
            return deleted

    @staticmethod
    def _tokens(text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return [term for term in re.findall(r"[^\W_]+", normalized) if term not in _STOPWORDS]

    @classmethod
    def _rank(
        cls,
        entries: Sequence[MemoryEntry],
        query: str,
        limit: int,
        kind: MemoryKind | None,
        department: Department | str | None,
        min_score: float,
    ) -> tuple[MemoryMatch, ...]:
        query_counts = Counter(cls._tokens(query))
        if not query_counts or not entries:
            return ()
        counts = [
            Counter(cls._tokens(f"{entry.title} {entry.title} {entry.content}"))
            for entry in entries
        ]
        document_frequency: Counter[str] = Counter()
        for document in counts:
            document_frequency.update(document.keys())

        def weight(term: str, count: int) -> float:
            inverse_frequency = 1 + math.log((1 + len(entries)) / (1 + document_frequency[term]))
            return (1 + math.log(count)) * inverse_frequency

        query_vector = {term: weight(term, count) for term, count in query_counts.items()}
        query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
        matches: list[MemoryMatch] = []
        for entry, document in zip(entries, counts, strict=True):
            if (kind is not None and entry.kind != kind) or (
                department is not None and entry.department != department
            ):
                continue
            overlap = tuple(sorted(query_counts.keys() & document.keys()))
            if not overlap:
                continue
            vector = {term: weight(term, count) for term, count in document.items()}
            document_norm = math.sqrt(sum(value * value for value in vector.values()))
            dot_product = sum(query_vector[term] * vector[term] for term in overlap)
            cosine = min(1.0, max(0.0, dot_product / (query_norm * document_norm)))
            keyword = len(overlap) / len(query_counts)
            score = min(1.0, 0.75 * cosine + 0.25 * keyword)
            if score >= min_score:
                matches.append(
                    MemoryMatch(
                        entry=entry,
                        score=score,
                        cosine_similarity=cosine,
                        keyword_score=keyword,
                        matched_terms=overlap,
                    )
                )
        matches.sort(key=lambda match: (-match.score, str(match.entry.id)))
        return tuple(matches[:limit])

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        kind: MemoryKind | None = None,
        department: Department | str | None = None,
        min_score: float = 0,
    ) -> tuple[MemoryMatch, ...]:
        """Blend cosine similarity (75%) with distinct keyword coverage (25%).

        Title terms appear twice. IDF is computed from a fresh committed mission snapshot,
        even when filtering by author or kind; another department's writes are visible
        without rebuilding an external index. No-overlap documents are never returned.
        CPU-heavy ranking runs in a worker thread rather than blocking the event loop.
        """
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between one and 100")
        if not math.isfinite(min_score) or not 0 <= min_score <= 1:
            raise ValueError("min_score must be finite and between zero and one")
        if not query.strip() or len(query) > 4096:
            raise ValueError("Search query must contain between one and 4096 characters")
        await self.initialize()
        async with self._connection() as connection:
            async with connection.execute(
                "SELECT * FROM entries WHERE mission_id=? ORDER BY id", (str(self.mission_id),)
            ) as cursor:
                rows = await cursor.fetchall()
        entries = [self._decode(row) for row in rows]
        return await asyncio.to_thread(
            self._rank, entries, query, limit, kind, department, min_score
        )
