import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, UUIDPrimaryKeyMixin, utcnow


class PublicationStatus(enum.StrEnum):
    SENDING = "SENDING"  # reserved before the external call
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"  # the provider definitely did not publish it
    NEEDS_CHECK = "NEEDS_CHECK"  # sent, but the outcome is unknown: never resend automatically


class Publication(UUIDPrimaryKeyMixin, Base):
    """The one external publish attempt allowed for an approval."""

    __tablename__ = "publications"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approvals.id", ondelete="CASCADE"), unique=True
    )
    toolkit_slug: Mapped[str] = mapped_column(String(64))
    tool_slug: Mapped[str] = mapped_column(String(128))
    target_account_id: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[PublicationStatus] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
