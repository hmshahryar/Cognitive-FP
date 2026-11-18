import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectionStatus(enum.StrEnum):
    """Mirrors Composio's connected-account statuses, plus our own DISCONNECTED."""

    INITIALIZING = "INITIALIZING"
    INITIATED = "INITIATED"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    INACTIVE = "INACTIVE"
    REVOKED = "REVOKED"
    DISCONNECTED = "DISCONNECTED"  # the user disconnected it from the Hub


class AttemptStatus(enum.StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IntegrationConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A workspace's connection to one app. Tokens live in Composio, never here."""

    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("workspace_id", "toolkit_slug"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # The user who authorized it; also the Composio user_id (str(user.id)).
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    toolkit_slug: Mapped[str] = mapped_column(String(64))
    composio_account_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[ConnectionStatus] = mapped_column(String(20))
    display_name: Mapped[str | None] = mapped_column(String(255))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Consent record: scopes the user granted on the provider's consent screen,
    # and when we verified that grant with Composio.
    granted_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def has_scopes(self, *required: str) -> bool:
        return set(required) <= set(self.granted_scopes or [])


class ConnectionAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One click on "Connect". Its id is the state we verify on the OAuth callback."""

    __tablename__ = "connection_attempts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    toolkit_slug: Mapped[str] = mapped_column(String(64))
    composio_account_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[AttemptStatus] = mapped_column(String(20), default=AttemptStatus.PENDING)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
