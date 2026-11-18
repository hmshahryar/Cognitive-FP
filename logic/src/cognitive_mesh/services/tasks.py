"""Tasks and their execution attempts.

Status changes use compare-and-set UPDATEs (`... WHERE status IN (allowed)`), so two
concurrent requests can't both cancel/retry/approve the same task.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.models import (
    Approval,
    ApprovalStatus,
    AuditEvent,
    ContentDraft,
    Execution,
    MessageRole,
    Task,
    TaskStatus,
)
from cognitive_mesh.models.work import (
    CANCELLABLE_STATUSES,
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
)
from cognitive_mesh.services import audit
from cognitive_mesh.services.accounts import Account
from cognitive_mesh.services.chats import add_message, get_chat
from cognitive_mesh.services.errors import ConflictError, NotFoundError


def new_thread_id() -> str:
    """LangGraph thread id: one per execution attempt, never reused."""
    return f"exec-{uuid.uuid4().hex}"


async def get_task(session: AsyncSession, account: Account, task_id: uuid.UUID) -> Task:
    task = await session.scalar(
        select(Task)
        .where(Task.id == task_id, Task.workspace_id == account.workspace.id)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise NotFoundError("Task not found")
    return task


async def list_tasks(
    session: AsyncSession, account: Account, chat_id: uuid.UUID | None = None
) -> list[Task]:
    query = select(Task).where(Task.workspace_id == account.workspace.id)
    if chat_id is not None:
        await get_chat(session, account, chat_id)
        query = query.where(Task.chat_id == chat_id)
    return list(await session.scalars(query.order_by(Task.id.desc())))


async def list_executions(
    session: AsyncSession, account: Account, task_id: uuid.UUID
) -> list[Execution]:
    await get_task(session, account, task_id)
    rows = await session.scalars(
        select(Execution)
        .where(Execution.task_id == task_id, Execution.workspace_id == account.workspace.id)
        .order_by(Execution.attempt)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def list_events(
    session: AsyncSession, account: Account, task_id: uuid.UUID
) -> list[AuditEvent]:
    await get_task(session, account, task_id)
    rows = await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.task_id == task_id, AuditEvent.workspace_id == account.workspace.id)
        .order_by(AuditEvent.id)
    )
    return list(rows)


async def list_drafts(
    session: AsyncSession, account: Account, task_id: uuid.UUID
) -> list[ContentDraft]:
    await get_task(session, account, task_id)
    rows = await session.scalars(
        select(ContentDraft)
        .where(ContentDraft.task_id == task_id, ContentDraft.workspace_id == account.workspace.id)
        .order_by(ContentDraft.id)
    )
    return list(rows)


def _new_execution(task: Task, attempt: int) -> Execution:
    return Execution(
        id=uuid7(),
        workspace_id=task.workspace_id,
        task_id=task.id,
        attempt=attempt,
        thread_id=new_thread_id(),
        status=TaskStatus.QUEUED,
    )


async def create_task(
    session: AsyncSession, account: Account, chat_id: uuid.UUID, request: str
) -> Task:
    """A user command: stores the message, the task and its first execution together."""
    chat = await get_chat(session, account, chat_id)
    task = Task(
        id=uuid7(),
        workspace_id=account.workspace.id,
        chat_id=chat.id,
        created_by=account.user.id,
        request=request,
        status=TaskStatus.QUEUED,
    )
    execution = _new_execution(task, attempt=1)
    task.current_execution_id = execution.id
    session.add(task)
    await session.flush()  # parents first: execution/message/audit rows reference the task
    session.add(execution)
    await session.flush()
    add_message(session, chat, role=MessageRole.USER, content=request, task_id=task.id)
    audit.record(
        session,
        workspace_id=task.workspace_id,
        event="task.created",
        actor="user",
        actor_user_id=account.user.id,
        task_id=task.id,
        execution_id=execution.id,
    )
    await session.commit()
    return task


async def transition(
    session: AsyncSession,
    task: Task,
    *,
    allowed_from: Iterable[TaskStatus],
    to: TaskStatus,
    update_execution: bool = True,
) -> None:
    """Atomically move a task (and its current execution) to `to`, or raise Conflict.

    `update_execution=False` leaves the current execution's final status untouched
    (used by retry, where a new execution is about to be created).
    """
    allowed = list(allowed_from)
    now = utcnow()
    result = await session.execute(
        update(Task)
        .where(Task.id == task.id, Task.status.in_(allowed))
        .values(
            status=to,
            updated_at=now,
            completed_at=now if to in TERMINAL_STATUSES else None,
        )
    )
    if result.rowcount != 1:
        raise ConflictError(f"Task cannot move to {to.value} from its current state")
    if update_execution and task.current_execution_id is not None:
        values: dict = {"status": to, "updated_at": now}
        if to == TaskStatus.RUNNING:
            values["started_at"] = now
        if to in TERMINAL_STATUSES:
            values["finished_at"] = now
        await session.execute(
            update(Execution).where(Execution.id == task.current_execution_id).values(**values)
        )


async def _invalidate_open_approvals(
    session: AsyncSession, task_id: uuid.UUID, reason: str
) -> None:
    await session.execute(
        update(Approval)
        .where(
            Approval.task_id == task_id,
            Approval.consumed_at.is_(None),
            Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
        )
        .values(status=ApprovalStatus.INVALIDATED, decision_note=reason, resolved_at=utcnow())
    )


async def cancel_task(session: AsyncSession, account: Account, task_id: uuid.UUID) -> Task:
    task = await get_task(session, account, task_id)
    await transition(session, task, allowed_from=CANCELLABLE_STATUSES, to=TaskStatus.CANCELLED)
    await _invalidate_open_approvals(session, task.id, "Task cancelled")
    audit.record(
        session,
        workspace_id=task.workspace_id,
        event="task.cancelled",
        actor="user",
        actor_user_id=account.user.id,
        task_id=task.id,
        execution_id=task.current_execution_id,
    )
    await session.commit()
    return await get_task(session, account, task_id)


async def retry_task(session: AsyncSession, account: Account, task_id: uuid.UUID) -> Task:
    """New execution attempt (new LangGraph thread). Concurrent retries: one wins."""
    task = await get_task(session, account, task_id)
    await transition(
        session,
        task,
        allowed_from=RETRYABLE_STATUSES,
        to=TaskStatus.QUEUED,
        update_execution=False,  # the failed attempt keeps its final status
    )
    last = await session.scalar(
        select(func.max(Execution.attempt)).where(Execution.task_id == task.id)
    )
    execution = _new_execution(task, attempt=(last or 0) + 1)
    session.add(execution)
    await session.flush()
    await session.execute(
        update(Task)
        .where(Task.id == task.id)
        .values(current_execution_id=execution.id, completed_at=None)
    )
    audit.record(
        session,
        workspace_id=task.workspace_id,
        event="task.retried",
        actor="user",
        actor_user_id=account.user.id,
        task_id=task.id,
        execution_id=execution.id,
        detail={"attempt": execution.attempt},
    )
    await session.commit()
    return await get_task(session, account, task_id)
