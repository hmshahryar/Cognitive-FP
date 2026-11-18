"""Human approvals and the append-only audit log."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, UUIDPrimaryKeyMixin, utcnow


class ApprovalStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SUPERSEDED = "SUPERSEDED"  # a newer content version replaced it
    INVALIDATED = "INVALIDATED"  # target/binding no longer valid, or task cancelled


class Approval(UUIDPrimaryKeyMixin, Base):
    """Consent for ONE exact action. Immutable once signed; changes need a new row.

    `binding_signature` = HMAC-SHA256 (server key) over every field that defines the
    action, so any change to content, version, target account or action breaks it.
    """

    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("execution_id", "content_version"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # --- the bound action ---
    action_type: Mapped[str] = mapped_column(String(64))  # e.g. "linkedin.publish_post"
    toolkit_slug: Mapped[str] = mapped_column(String(64))
    tool_slug: Mapped[str] = mapped_column(String(128))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    target_account_id: Mapped[str] = mapped_column(String(64))  # Composio connected account
    # The exact reviewed draft this approval was requested for (audit + idempotency).
    content_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_drafts.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of canonical payload
    content_version: Mapped[int] = mapped_column(Integer)
    binding_signature: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)

    # --- decision ---
    status: Mapped[ApprovalStatus] = mapped_column(String(32), default=ApprovalStatus.PENDING)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set exactly once when the executor uses this approval (prevents double execution).
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only record of everything that happened to a task. Never updated."""

    __tablename__ = "audit_events"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id", ondelete="CASCADE"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(64))  # "user", "system", or an agent name
    event: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
