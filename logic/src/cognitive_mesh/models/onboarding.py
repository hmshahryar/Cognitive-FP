import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What the owner told us during onboarding. One row per workspace.

    Only a fixed set of choices is stored (validated in the API), never free text other than
    the workspace name, which lives on `workspaces.name`.
    """

    __tablename__ = "workspace_profiles"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    team_size: Mapped[str | None] = mapped_column(String(16))
    goals: Mapped[list[str]] = mapped_column(JSON, default=list)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
