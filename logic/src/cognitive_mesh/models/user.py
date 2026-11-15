from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cognitive_mesh.models.workspace import Workspace


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A registered person, keyed by their verified Clerk user ID."""

    __tablename__ = "users"

    clerk_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(2048))

    workspace: Mapped["Workspace | None"] = relationship(
        back_populates="owner", uselist=False, lazy="raise"
    )
