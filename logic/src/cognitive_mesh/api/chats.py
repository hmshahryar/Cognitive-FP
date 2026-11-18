import uuid
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.api.tasks import TaskOut, task_out
from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.services import chats as chat_svc
from cognitive_mesh.services import tasks as task_svc

router = APIRouter(prefix="/chats", tags=["chats"])


class ChatIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    task_id: uuid.UUID | None
    created_at: datetime


class TaskIn(BaseModel):
    request: str = Field(min_length=1, max_length=8000)


class ChatDetailOut(BaseModel):
    chat: ChatOut
    messages: list[MessageOut]
    tasks: list[TaskOut]


@router.post("", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_chat(body: ChatIn, account: CurrentAccountDep, session: SessionDep) -> ChatOut:
    return ChatOut.model_validate(await chat_svc.create_chat(session, account, body.title))


@router.get("", response_model=list[ChatOut])
async def list_chats(account: CurrentAccountDep, session: SessionDep) -> list[ChatOut]:
    return [ChatOut.model_validate(c) for c in await chat_svc.list_chats(session, account)]


@router.get("/{chat_id}", response_model=ChatDetailOut)
async def get_chat(
    chat_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> ChatDetailOut:
    """Conversation history plus the tasks started from it."""
    chat = await chat_svc.get_chat(session, account, chat_id)
    messages = await chat_svc.list_messages(session, account, chat_id)
    tasks = await task_svc.list_tasks(session, account, chat_id)
    return ChatDetailOut(
        chat=ChatOut.model_validate(chat),
        messages=[MessageOut.model_validate(m) for m in messages],
        tasks=[task_out(t) for t in tasks],
    )


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(
    chat_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> list[MessageOut]:
    messages = await chat_svc.list_messages(session, account, chat_id)
    return [MessageOut.model_validate(m) for m in messages]


@router.post(
    "/{chat_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED
)
async def post_message(
    chat_id: uuid.UUID, body: MessageIn, account: CurrentAccountDep, session: SessionDep
) -> MessageOut:
    """Users can only post user-role messages; assistant messages come from the system."""
    message = await chat_svc.post_user_message(session, account, chat_id, body.content)
    return MessageOut.model_validate(message)


@router.post("/{chat_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    chat_id: uuid.UUID, body: TaskIn, account: CurrentAccountDep, session: SessionDep
) -> TaskOut:
    """A command for the AI employees. Stored as a message + task + first execution."""
    return task_out(await task_svc.create_task(session, account, chat_id, body.request))
