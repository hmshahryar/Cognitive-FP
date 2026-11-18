"""Chats and messages: the persistent conversation, scoped to the caller's workspace."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow
from cognitive_mesh.models import Chat, Message, MessageRole
from cognitive_mesh.services.accounts import Account
from cognitive_mesh.services.errors import NotFoundError

DEFAULT_TITLE = "New chat"


async def create_chat(session: AsyncSession, account: Account, title: str | None) -> Chat:
    chat = Chat(
        workspace_id=account.workspace.id,
        created_by=account.user.id,
        title=(title or "").strip()[:200] or DEFAULT_TITLE,
    )
    session.add(chat)
    await session.commit()
    return chat


async def list_chats(session: AsyncSession, account: Account) -> list[Chat]:
    rows = await session.scalars(
        select(Chat)
        .where(Chat.workspace_id == account.workspace.id)
        .order_by(Chat.updated_at.desc(), Chat.id.desc())
    )
    return list(rows)


async def get_chat(session: AsyncSession, account: Account, chat_id: uuid.UUID) -> Chat:
    chat = await session.scalar(
        select(Chat).where(Chat.id == chat_id, Chat.workspace_id == account.workspace.id)
    )
    if chat is None:
        raise NotFoundError("Chat not found")
    return chat


def add_message(
    session: AsyncSession,
    chat: Chat,
    *,
    role: MessageRole,
    content: str,
    task_id: uuid.UUID | None = None,
) -> Message:
    """Stage a message in the current transaction and bump the chat's activity time."""
    message = Message(
        workspace_id=chat.workspace_id,
        chat_id=chat.id,
        role=role,
        content=content,
        task_id=task_id,
    )
    session.add(message)
    chat.updated_at = utcnow()
    return message


async def post_user_message(
    session: AsyncSession, account: Account, chat_id: uuid.UUID, content: str
) -> Message:
    chat = await get_chat(session, account, chat_id)
    message = add_message(session, chat, role=MessageRole.USER, content=content)
    await session.commit()
    return message


async def list_messages(
    session: AsyncSession, account: Account, chat_id: uuid.UUID
) -> list[Message]:
    await get_chat(session, account, chat_id)  # ownership check
    rows = await session.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.workspace_id == account.workspace.id)
        .order_by(Message.id)
    )
    return list(rows)
