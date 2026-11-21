"""Turns one verified approval into at most one external post.

    reserve: verify the approval, consume it, record SENDING (one transaction)
    send:    one gateway call, never retried
    finish:  PUBLISHED, FAILED (definitely not posted) or NEEDS_CHECK (outcome unknown)

The reservation is committed BEFORE sending. If the process dies mid-send, recovery sees
SENDING and marks NEEDS_CHECK instead of sending again, because LinkedIn's create-post call
has no idempotency key that could make a resend safe.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.integrations import linkedin
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

NOT_PUBLISHED = (
    "LinkedIn did not publish this post, so nothing went live. "
    "Press Retry to prepare it for your approval again."
)
NEEDS_CHECK = (
    "I can't confirm whether this post went live because the result was lost after sending. "
    "Check your LinkedIn profile before posting it again. I will not resend it automatically."
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
    if connection is None or not connection.has_scopes(linkedin.REQUIRED_SCOPE):
        raise ApprovalInvalidError("LinkedIn posting permission is missing. Reconnect LinkedIn.")
    if approval.tool_slug != linkedin.POST_TOOL or approval.action_type != linkedin.ACTION_TYPE:
        raise ApprovalInvalidError("This approval is not for a LinkedIn post")
    try:
        linkedin.validate_post_payload(approval.payload)
    except linkedin.InvalidPayloadError as exc:
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
            "visibility": approval.payload["visibility"],
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
    if published:
        message = f"Published on LinkedIn: {external_url}" if external_url else "Published on LinkedIn."
    else:
        message = NOT_PUBLISHED if status == PublicationStatus.FAILED else NEEDS_CHECK
        await session.execute(
            update(Execution)
            .where(Execution.id == publication.execution_id)
            .values(error=(error or status.value)[:2000])
        )
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
