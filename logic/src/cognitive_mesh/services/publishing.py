"""Turns one verified approval into at most one external action (post, email, ...).

    reserve: verify the approval, consume it, record SENDING (one transaction)
    send:    one gateway call, never retried
    finish:  PUBLISHED, FAILED (definitely did not happen) or NEEDS_CHECK (outcome unknown)

The reservation is committed BEFORE sending. If the process dies mid-send, recovery sees
SENDING and marks NEEDS_CHECK instead of sending again, because these provider calls have
no idempotency key that could make a repeat safe.
"""

import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.integrations.actions import action_for_tool, get_action
from cognitive_mesh.models import (
    Approval,
    ApprovalStatus,
    Chat,
    Execution,
    IntegrationConnection,
    MessageRole,
    Publication,
    PublicationStatus,
    Task,
    TaskStatus,
)
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import approvals as approval_svc
from cognitive_mesh.services import audit
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.errors import ApprovalInvalidError, ConflictError
from cognitive_mesh.services.tasks import transition

_MISSING_PERMISSION = re.compile(
    r"insufficient authentication scopes|ACCESS_TOKEN_SCOPE_INSUFFICIENT", re.IGNORECASE
)


async def for_approval(session: AsyncSession, approval_id: uuid.UUID) -> Publication | None:
    return await session.scalar(
        select(Publication)
        .where(Publication.approval_id == approval_id)
        .execution_options(populate_existing=True)
    )


async def reserve(
    session: AsyncSession, signer: ApprovalSigner, approval: Approval
) -> Publication:
    """Final checks, then atomically use the approval. Raises if it can't authorize a post."""
    await approval_svc.verify_for_execution(session, signer, approval)
    connection = await session.get(
        IntegrationConnection, approval.connection_id, populate_existing=True
    )
    spec = get_action(approval.action_type)
    if spec is None or approval.tool_slug != spec.tool_slug or approval.toolkit_slug != spec.toolkit:
        raise ApprovalInvalidError("This approval is not for a supported action")
    granted = set(connection.granted_scopes or []) if connection else set()
    if connection is None or not spec.is_permitted(granted):
        raise ApprovalInvalidError(f"{spec.app} permission is missing. Reconnect {spec.app}.")
    try:
        spec.validate(approval.payload)
    except ValueError as exc:
        raise ApprovalInvalidError(str(exc)) from exc

    task = await session.get(Task, approval.task_id, populate_existing=True)
    assert task is not None
    used = await session.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == ApprovalStatus.APPROVED,
            Approval.consumed_at.is_(None),
        )
        .values(consumed_at=utcnow())
    )
    if used.rowcount != 1:
        await session.rollback()
        raise ConflictError("This approval was already used")
    try:
        await transition(
            session, task, allowed_from=[TaskStatus.APPROVED], to=TaskStatus.EXECUTING
        )
    except ConflictError:  # cancelled at the last moment: the approval stays unused
        await session.rollback()
        raise
    publication = Publication(
        id=uuid7(),
        workspace_id=approval.workspace_id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        toolkit_slug=approval.toolkit_slug,
        tool_slug=approval.tool_slug,
        target_account_id=approval.target_account_id,
        payload_hash=approval.payload_hash,
        status=PublicationStatus.SENDING,
    )
    session.add(publication)
    await session.flush()
    audit.record(
        session,
        workspace_id=approval.workspace_id,
        event="publish.started",
        actor="system",
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        detail={
            "payload_hash": approval.payload_hash,
            "content_version": approval.content_version,
            "action_type": approval.action_type,
            "visibility": approval.payload.get("visibility"),
        },
    )
    await session.commit()
    return publication


async def finish(
    session: AsyncSession,
    publication_id: uuid.UUID,
    status: PublicationStatus,
    *,
    external_id: str | None = None,
    external_url: str | None = None,
    error: str | None = None,
    note: str | None = None,
) -> None:
    """Record the single outcome. A publication leaves SENDING exactly once."""
    moved = await session.execute(
        update(Publication)
        .where(Publication.id == publication_id, Publication.status == PublicationStatus.SENDING)
        .values(
            status=status,
            external_id=external_id,
            external_url=external_url,
            error=error[:2000] if error else None,
            finished_at=utcnow(),
        )
    )
    if moved.rowcount != 1:
        await session.rollback()
        return
    publication = await session.get(Publication, publication_id, populate_existing=True)
    assert publication is not None
    task = await session.get(Task, publication.task_id, populate_existing=True)
    assert task is not None
    published = status == PublicationStatus.PUBLISHED
    await transition(
        session,
        task,
        allowed_from=[TaskStatus.EXECUTING],
        to=TaskStatus.COMPLETED if published else TaskStatus.FAILED,
    )
    spec = action_for_tool(publication.tool_slug)
    assert spec is not None
    if published:
        message = f"{spec.done_message}: {external_url}" if external_url else f"{spec.done_message}."
    else:
        message = spec.failed_message if status == PublicationStatus.FAILED else spec.unknown_message
        if status == PublicationStatus.FAILED and _MISSING_PERMISSION.search(error or ""):
            message = (
                f"{spec.app} refused this because the connection needs more permission, so "
                f"nothing changed. Reconnect {spec.app} on the Connectors page, allow all "
                "requested access, then press Retry."
            )
        await session.execute(
            update(Execution)
            .where(Execution.id == publication.execution_id)
            .values(error=(error or status.value)[:2000])
        )
    if note:
        message = f"{message} {note}"
    chat = await session.get(Chat, task.chat_id)
    add_message(session, chat, role=MessageRole.ASSISTANT, content=message, task_id=task.id)  # type: ignore[arg-type]
    event = {
        PublicationStatus.PUBLISHED: "publish.succeeded",
        PublicationStatus.FAILED: "publish.failed",
        PublicationStatus.NEEDS_CHECK: "publish.needs_check",
    }[status]
    audit.record(
        session,
        workspace_id=publication.workspace_id,
        event=event,
        actor="system",
        task_id=publication.task_id,
        execution_id=publication.execution_id,
        approval_id=publication.approval_id,
        detail={"external_id": external_id, "external_url": external_url},
    )
    await session.commit()
