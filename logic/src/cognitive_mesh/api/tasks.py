import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.models import Task
from cognitive_mesh.services import tasks as task_svc
from cognitive_mesh.services.conversation import route_label

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_id: uuid.UUID
    request: str
    status: str
    current_execution_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    route_label: str | None = None  # which specialist handled this task


def task_out(task: Task) -> TaskOut:
    return TaskOut.model_validate(task).model_copy(
        update={"route_label": route_label(task.resolved_route)}
    )


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt: int
    thread_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event: str
    actor: str
    execution_id: uuid.UUID | None
    approval_id: uuid.UUID | None
    detail: dict[str, Any]
    created_at: datetime


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    version: int
    channel: str
    text: str
    hashtags: list[str]
    source_ids: list[str]
    sources: list[dict[str, Any]]
    review: dict[str, Any]
    accepted: bool
    is_final: bool
    model: str
    provider: str
    prompt_version: str
    claims: list[dict[str, Any]]
    quality: dict[str, Any]
    research: dict[str, Any]
    created_at: datetime


@router.get("/{task_id}/drafts", response_model=list[DraftOut])
async def list_drafts(
    task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> list[DraftOut]:
    """Every reviewed draft version the AI employees produced for this task."""
    return [DraftOut.model_validate(d) for d in await task_svc.list_drafts(session, account, task_id)]


class PublicationOut(BaseModel):
    """The publish result. The full provider error stays server-side."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    approval_id: uuid.UUID
    tool_slug: str
    status: str
    external_id: str | None
    external_url: str | None
    created_at: datetime
    finished_at: datetime | None


@router.get("/{task_id}/publications", response_model=list[PublicationOut])
async def list_publications(
    task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> list[PublicationOut]:
    rows = await task_svc.list_publications(session, account, task_id)
    return [PublicationOut.model_validate(p) for p in rows]


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    account: CurrentAccountDep, session: SessionDep, chat_id: uuid.UUID | None = None
) -> list[TaskOut]:
    return [task_out(t) for t in await task_svc.list_tasks(session, account, chat_id)]


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep) -> TaskOut:
    return task_out(await task_svc.get_task(session, account, task_id))


@router.get("/{task_id}/executions", response_model=list[ExecutionOut])
async def list_executions(
    task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> list[ExecutionOut]:
    executions = await task_svc.list_executions(session, account, task_id)
    return [ExecutionOut.model_validate(e) for e in executions]


@router.get("/{task_id}/events", response_model=list[AuditEventOut])
async def list_events(
    task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> list[AuditEventOut]:
    """The task's audit trail."""
    events = await task_svc.list_events(session, account, task_id)
    return [AuditEventOut.model_validate(e) for e in events]


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task_id: uuid.UUID, request: Request, account: CurrentAccountDep, session: SessionDep
) -> TaskOut:
    task = await task_svc.cancel_task(session, account, task_id)
    if task.current_execution_id is not None:
        # Let a paused workflow observe the cancellation and finish cleanly.
        request.app.state.runner.request_resume(task.current_execution_id)
    return task_out(task)


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(
    task_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> TaskOut:
    return task_out(await task_svc.retry_task(session, account, task_id))
