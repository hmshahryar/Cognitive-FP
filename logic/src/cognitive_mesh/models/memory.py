import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from cognitive_mesh.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChatMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Short-term memory for one chat. Separate from LangGraph checkpoints.

    - `pending`: the unfinished command waiting for one missing detail.
    - `summary`: rolling summary of the first `compacted_count` messages.
    The full message history always stays in the `messages` table.
    """

    __tablename__ = "chat_memories"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), unique=True
    )
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    compacted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
