"""Additional tables; existing mission/task schemas are left intact."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.mission import Base, UTCDateTime


class MissionRun(Base):
    __tablename__ = "mission_runs"

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True
    )
    planning_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_nodes.id", ondelete="CASCADE"), unique=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class MissionExecutionProfile(Base):
    """Additive table: restart-safe demo/live routing, never credentials."""

    __tablename__ = "mission_execution_profiles"
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True
    )
    configuration_json: Mapped[str] = mapped_column(Text)


class TaskApproval(Base):
    __tablename__ = "task_approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('requested', 'approved', 'rejected')", name="approval_decision"
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), index=True
    )
    approval_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4, unique=True)
    decision: Mapped[Literal["requested", "approved", "rejected"]] = mapped_column(
        String(16), default="requested"
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MissionEventRecord(Base):
    __tablename__ = "mission_events"
    __table_args__ = (
        Index("ix_mission_events_cursor", "mission_id", "sequence"),
        {"sqlite_autoincrement": True},
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    mission_id: Mapped[UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    payload: Mapped[str] = mapped_column(Text)
