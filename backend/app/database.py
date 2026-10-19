"""Application-owned async SQLite lifecycle and transactional session scopes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from anyio import CancelScope
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.config import Settings
from backend.app.models import orchestration as _orchestration  # noqa: F401
from backend.app.models.mission import Base


class Database:
    """A session factory per application, with no import-time engine or filesystem writes.

    ``initialize`` creates a new schema. Existing deployments must use explicit schema
    migrations when upgrading; create_all deliberately does not alter existing tables.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        url = (
            make_url(settings.database_url)
            if settings.database_url
            else URL.create("sqlite+aiosqlite", database=str(settings.data_dir / "openfoundry.db"))
        )
        self.engine: AsyncEngine = create_async_engine(
            url,
            connect_args={"timeout": settings.sqlite_busy_timeout_ms / 1000},
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        timeout_ms = settings.sqlite_busy_timeout_ms

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={timeout_ms:d}")
                cursor.execute("PRAGMA synchronous=FULL")
            finally:
                cursor.close()

    async def initialize(self) -> None:
        self.settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.settings.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        database_file = self.engine.url.database
        if database_file and database_file != ":memory:" and not database_file.startswith("file:"):
            Path(database_file).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        async with self.engine.connect() as connection:
            # WAL lets readers proceed while the single SQLite writer commits a reservation.
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.commit()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        """Commit on success; roll back on errors and cancellation; always close."""
        session = self.session_factory()
        try:
            await session.begin()
            try:
                if write:
                    # Acquire the single SQLite writer before reading mutable state,
                    # avoiding read-snapshot upgrades when another worker commits.
                    await session.execute(text("BEGIN IMMEDIATE"))
                yield session
            except BaseException:
                with CancelScope(shield=True):
                    await session.rollback()
                raise
            else:
                with CancelScope(shield=True):
                    try:
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        raise
        finally:
            # ASGI/AnyIO cancellation is level-triggered. Cleanup must survive a
            # disconnected socket repeatedly cancelling the enclosing request scope.
            with CancelScope(shield=True):
                await session.close()

    async def close(self) -> None:
        await self.engine.dispose()

    async def __aenter__(self) -> Database:
        try:
            await self.initialize()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
