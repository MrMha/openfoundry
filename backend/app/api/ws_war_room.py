"""Durable event replay with bounded, independently paced WebSocket subscribers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import authorize_websocket
from backend.app.database import Database
from backend.app.models.events import WarRoomEvent
from backend.app.models.mission import Mission
from backend.app.models.orchestration import MissionEventRecord

router = APIRouter()


class ConnectionManager:
    """Persist before notifying; sockets fetch committed events in sequence order.

    Each consumer owns one wakeup bit rather than an unbounded message queue. Slow clients
    never block mission execution. Event history is retained in SQLite across restarts.
    Deploy one coordinator process per database; this is not a cross-host message broker.
    """

    def __init__(self, database: Database, *, max_connections: int = 64) -> None:
        self.database = database
        self.max_connections = max_connections
        self._subscribers: dict[UUID, set[asyncio.Event]] = defaultdict(set)
        self._closed = False

    async def append(self, session: AsyncSession, event: WarRoomEvent) -> WarRoomEvent:
        if event.sequence is not None:
            raise ValueError("Event sequence numbers are assigned by the event store")
        record = MissionEventRecord(
            mission_id=event.mission_id, event_id=event.event_id, payload=""
        )
        session.add(record)
        await session.flush()
        sequenced = event.model_copy(update={"sequence": record.sequence})
        record.payload = sequenced.model_dump_json()
        if len(record.payload.encode()) > 8 * 1024 * 1024:
            raise ValueError("Event exceeds the eight MiB transport limit")
        return sequenced

    def notify(self, mission_id: UUID) -> None:
        for event in self._subscribers.get(mission_id, ()):
            event.set()

    async def publish(self, event: WarRoomEvent) -> None:
        async with self.database.session(write=True) as session:
            await self.append(session, event)
        self.notify(event.mission_id)

    @asynccontextmanager
    async def subscribe(self, mission_id: UUID) -> AsyncIterator[asyncio.Event]:
        if self._closed or sum(map(len, self._subscribers.values())) >= self.max_connections:
            raise RuntimeError("WebSocket subscriber capacity exceeded")
        wakeup = asyncio.Event()
        self._subscribers[mission_id].add(wakeup)
        try:
            yield wakeup
        finally:
            self._subscribers[mission_id].discard(wakeup)
            if not self._subscribers[mission_id]:
                del self._subscribers[mission_id]

    async def history(
        self, mission_id: UUID, after: int, *, limit: int = 100
    ) -> list[MissionEventRecord]:
        async with self.database.session() as session:
            return list(
                (
                    await session.scalars(
                        select(MissionEventRecord)
                        .where(
                            MissionEventRecord.mission_id == mission_id,
                            MissionEventRecord.sequence > after,
                        )
                        .order_by(MissionEventRecord.sequence)
                        .limit(limit)
                    )
                ).all()
            )

    async def close(self) -> None:
        self._closed = True
        for subscribers in self._subscribers.values():
            for event in subscribers:
                event.set()


@router.websocket("/ws/missions/{mission_id}")
async def war_room(
    websocket: WebSocket, mission_id: UUID, after: int = Query(default=0, ge=0, le=2**63 - 1)
) -> None:
    await authorize_websocket(websocket)
    manager = cast(ConnectionManager, websocket.app.state.connection_manager)
    async with manager.database.session() as session:
        if await session.get(Mission, mission_id) is None:
            await websocket.close(code=1008, reason="Mission does not exist")
            return
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    await websocket.accept(
        subprotocol="openfoundry" if "openfoundry" in {part.strip() for part in protocols} else None
    )
    try:
        async with manager.subscribe(mission_id) as wakeup:

            async def send_events() -> None:
                cursor = after
                while not manager._closed:
                    wakeup.clear()
                    records = await manager.history(mission_id, cursor, limit=1)
                    for record in records:
                        async with asyncio.timeout(5):
                            await websocket.send_text(record.payload)
                        cursor = record.sequence
                    if records:
                        continue
                    try:
                        async with asyncio.timeout(15):
                            await wakeup.wait()
                    except TimeoutError:
                        async with asyncio.timeout(5):
                            await websocket.send_json(
                                {
                                    "type": "heartbeat",
                                    "mission_id": str(mission_id),
                                    "cursor": cursor,
                                }
                            )

            async def receive_control() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    content = message.get("text")
                    if not isinstance(content, str) or len(content) > 4096 or content != "ping":
                        await websocket.close(
                            code=1008, reason="Only ping is accepted; decisions use REST"
                        )
                        return

            tasks = [asyncio.create_task(send_events()), asyncio.create_task(receive_control())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                with CancelScope(shield=True):
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    except (WebSocketDisconnect, TimeoutError, RuntimeError, asyncio.CancelledError):
        return
    finally:
        with CancelScope(shield=True):
            try:
                await websocket.close()
            except (WebSocketDisconnect, RuntimeError):
                pass
