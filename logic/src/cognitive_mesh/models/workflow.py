import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowRunStatus(enum.StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    WAITING = "WAITING"  # a timer or an outside event must happen first
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    NEEDS_CHECK = "NEEDS_CHECK"  # at least one step has an unknown outcome
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"  # the approved plan or account changed


class WorkflowStepStatus(enum.StrEnum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    SENDING = "SENDING"  # reserved before the external call
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # definitely did not happen
    NEEDS_CHECK = "NEEDS_CHECK"  # sent, but the outcome is unknown: never repeated
    SKIPPED = "SKIPPED"


class WorkflowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One signed multi-step plan. Kept separate from LangGraph checkpoints."""

    __tablename__ = "workflow_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), index=True
    )
    playbook: Mapped[str] = mapped_column(String(64))
    playbook_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[WorkflowRunStatus] = mapped_column(String(32), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    binding_signature: Mapped[str] = mapped_column(String(64))
    approved_gate: Mapped[int] = mapped_column(Integer, default=-1)  # -1 means none yet
    last_gate: Mapped[int] = mapped_column(Integer)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)


class WorkflowStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One step and its single allowed external attempt."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "key", name="uq_workflow_steps_run_key"),
        UniqueConstraint("run_id", "position", name="uq_workflow_steps_run_position"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    gate: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    run_after_failure: Mapped[bool] = mapped_column(default=False)
    action_type: Mapped[str | None] = mapped_column(String(64))
    toolkit_slug: Mapped[str | None] = mapped_column(String(64))
    tool_slug: Mapped[str | None] = mapped_column(String(128))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="SET NULL")
    )
    target_account_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    payload_hash: Mapped[str] = mapped_column(String(64))
    resolved_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    wait_seconds: Mapped[int | None] = mapped_column(Integer)
    wait_event: Mapped[str | None] = mapped_column(String(64))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[WorkflowStepStatus] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
