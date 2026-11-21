"""Application chat & task records. Separate from LangGraph checkpoint state."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class TaskStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"  # approved, waiting for the executor
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED}
)
RETRYABLE_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED})
CANCELLABLE_STATUSES = frozenset(
    {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED}
)


class Chat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chats"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user command. A chat can hold many tasks."""

    __tablename__ = "tasks"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(String(32), default=TaskStatus.QUEUED)
    # Latest execution attempt (no FK: avoids a tasks<->executions cycle).
    current_execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What this message resolved to, including earlier context. Keeps retries and recovery exact.
    resolved_route: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))


class Execution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt at a task. Each attempt has its own LangGraph thread_id."""

    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("task_id", "attempt"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[TaskStatus] = mapped_column(String(32), default=TaskStatus.QUEUED)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
