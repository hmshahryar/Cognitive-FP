import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cognitive_mesh.models.user import User

DEFAULT_WORKSPACE_NAME = "Personal workspace"


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's private workspace. Exactly one per user (unique owner_id)."""

    __tablename__ = "workspaces"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), default=DEFAULT_WORKSPACE_NAME)

    owner: Mapped["User"] = relationship(back_populates="workspace", lazy="raise")
