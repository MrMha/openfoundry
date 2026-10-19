"""Relational domain models and the durable LLM usage ledger."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from backend.app.config import USD_QUANTUM


class USD(TypeDecorator[Decimal]):
    """Store USD as integer nanodollars, avoiding SQLite floating point arithmetic."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("USD values must be finite Decimal instances")
        units = value / USD_QUANTUM
        if units != units.to_integral_value() or abs(units) > 2**63 - 1:
            raise ValueError("USD value exceeds storage precision or range")
        return int(units)

    def process_result_value(self, value: int | None, dialect: Dialect) -> Decimal | None:
        return Decimal(value) * USD_QUANTUM if value is not None else None


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores naive UTC; the public Python value is always timezone aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    """Shared SQLAlchemy metadata."""


class MissionStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class AttemptStatus(enum.StrEnum):
    RESERVED = "reserved"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


def persisted_enum(enum_class: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint("total_cost_usd >= 0", name="mission_cost_nonnegative"),
        CheckConstraint("reserved_cost_usd >= 0", name="mission_reserved_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[MissionStatus] = mapped_column(
        persisted_enum(MissionStatus, "mission_status"), default=MissionStatus.PENDING, index=True
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(USD(), default=Decimal(0))
    reserved_cost_usd: Mapped[Decimal] = mapped_column(USD(), default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))
    tasks: Mapped[list[TaskNode]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    llm_attempts: Mapped[list[LLMAttempt]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class TaskNode(Base):
    __tablename__ = "task_nodes"
    __table_args__ = (
        CheckConstraint(
            "json_valid(dependencies_json) AND json_type(dependencies_json) = 'array'",
            name="task_dependencies_array",
        ),
        Index("ix_task_mission_status", "mission_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), index=True
    )
    department: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    dependencies_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[TaskStatus] = mapped_column(
        persisted_enum(TaskStatus, "task_status"), default=TaskStatus.PENDING
    )
    output_summary: Mapped[str | None] = mapped_column(Text)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    mission: Mapped[Mission] = relationship(back_populates="tasks", lazy="raise")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(100))
    content_preview: Mapped[str | None] = mapped_column(Text)
    mission: Mapped[Mission] = relationship(back_populates="artifacts", lazy="raise")


class LLMAttempt(Base):
    """One provider request, including failed requests with uncertain billing.

    A reserved or uncertain attempt continues to consume its reservation after process
    failure. Never release it automatically on a timeout: the provider may have billed it.
    No prompts, response text, API keys, or raw provider errors are stored here.
    """

    __tablename__ = "llm_attempts"
    __table_args__ = (
        CheckConstraint("reserved_cost_usd >= 0", name="attempt_reserved_nonnegative"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="attempt_cost_nonnegative"),
        CheckConstraint("prompt_tokens >= 0", name="attempt_prompt_nonnegative"),
        CheckConstraint("completion_tokens >= 0", name="attempt_completion_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[AttemptStatus] = mapped_column(
        persisted_enum(AttemptStatus, "attempt_status"), default=AttemptStatus.RESERVED
    )
    reserved_cost_usd: Mapped[Decimal] = mapped_column(USD())
    cost_usd: Mapped[Decimal | None] = mapped_column(USD())
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))
    mission: Mapped[Mission] = relationship(back_populates="llm_attempts", lazy="raise")
