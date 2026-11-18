"""Human approval of consequential actions.

Lifecycle:  request_approval (system/agent) -> PENDING
            -> approve | reject | request_changes (workspace owner only)
            -> verify_for_execution + consume (executor, exactly once)

Guarantees:
- Each approval is HMAC-bound to its exact action (see security/approval_signing.py).
- Approving requires the payload hash and content version the user was shown.
- A new content version supersedes the old approval; a changed/removed target
  account invalidates it. Neither can authorize the new action.
- Status changes are compare-and-set, so double clicks can't double-approve, and
  `consume` sets consumed_at once, so an approval can't execute twice.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.integrations import linkedin
from cognitive_mesh.models import (
    Approval,
    ApprovalStatus,
    ConnectionStatus,
    Execution,
    IntegrationConnection,
    Task,
    TaskStatus,
)
from cognitive_mesh.security.approval_signing import (
    ApprovalBinding,
    ApprovalSigner,
    payload_hash,
)
from cognitive_mesh.services import audit
from cognitive_mesh.services.accounts import Account
from cognitive_mesh.services.errors import (
    ApprovalInvalidError,
    ApprovalMismatchError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from cognitive_mesh.services.tasks import transition

OPEN_STATUSES = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)


def binding_of(approval: Approval) -> ApprovalBinding:
    return ApprovalBinding(
        approval_id=approval.id,
        workspace_id=approval.workspace_id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        requested_by=approval.requested_by,
        action_type=approval.action_type,
        toolkit_slug=approval.toolkit_slug,
        tool_slug=approval.tool_slug,
        connection_id=approval.connection_id,
        target_account_id=approval.target_account_id,
        payload_hash=approval.payload_hash,
        content_version=approval.content_version,
    )


# ---------- creation (called by the workflow in later steps) ----------
async def request_approval(
    session: AsyncSession,
    signer: ApprovalSigner,
    *,
    task: Task,
    execution: Execution,
    requested_by: uuid.UUID,
    action_type: str,
    toolkit_slug: str,
    tool_slug: str,
    connection: IntegrationConnection,
    payload: dict[str, Any],
    content_draft_id: uuid.UUID | None = None,
) -> Approval:
    """Create a signed approval for one exact action; supersedes older pending versions."""
    if connection.workspace_id != task.workspace_id or execution.task_id != task.id:
        raise ForbiddenError("Connection or execution does not belong to this task's workspace")
    if connection.status != ConnectionStatus.ACTIVE or connection.toolkit_slug != toolkit_slug:
        raise ConflictError(f"{toolkit_slug} is not connected")
    if tool_slug == linkedin.POST_TOOL:
        try:
            linkedin.validate_post_payload(payload)
        except linkedin.InvalidPayloadError as exc:
            raise ConflictError(str(exc)) from exc

    await session.execute(
        update(Approval)
        .where(Approval.execution_id == execution.id, Approval.status == ApprovalStatus.PENDING)
        .values(status=ApprovalStatus.SUPERSEDED, resolved_at=utcnow())
    )
    last_version = await session.scalar(
        select(func.max(Approval.content_version)).where(Approval.execution_id == execution.id)
    )
    approval = Approval(
        id=uuid7(),
        workspace_id=task.workspace_id,
        task_id=task.id,
        execution_id=execution.id,
        requested_by=requested_by,
        action_type=action_type,
        toolkit_slug=toolkit_slug,
        tool_slug=tool_slug,
        connection_id=connection.id,
        target_account_id=connection.composio_account_id,
        content_draft_id=content_draft_id,
        payload=payload,
        payload_hash=payload_hash(payload),
        content_version=(last_version or 0) + 1,
        status=ApprovalStatus.PENDING,
    )
    binding = binding_of(approval)
    approval.binding_signature = signer.sign(binding)
    approval.idempotency_key = binding.idempotency_key()
    session.add(approval)
    await session.flush()  # the audit row below references this approval

    await transition(
        session,
        task,
        allowed_from=[TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL],
        to=TaskStatus.AWAITING_APPROVAL,
    )
    audit.record(
        session,
        workspace_id=task.workspace_id,
        event="approval.requested",
        actor="system",
        task_id=task.id,
        execution_id=execution.id,
        approval_id=approval.id,
        detail={
            "action_type": action_type,
            "tool_slug": tool_slug,
            "content_version": approval.content_version,
            "payload_hash": approval.payload_hash,
        },
    )
    await session.commit()
    return approval


async def change_visibility(
    session: AsyncSession,
    account: Account,
    signer: ApprovalSigner,
    approval_id: uuid.UUID,
    *,
    visibility: str,
) -> Approval:
    """Changing who can see the post is a change to the action: it needs NEW consent.

    The pending approval is superseded and a new signed version is created through
    `request_approval`, so every binding rule applies to the new version too.
    """
    approval = await get_approval(session, account, approval_id)
    _require_owner(account)
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError("Only a pending approval can be changed")
    if approval.tool_slug != linkedin.POST_TOOL:
        raise ConflictError("This action has no visibility setting")
    if visibility not in linkedin.VISIBILITIES:
        raise ConflictError(f"Visibility must be one of {linkedin.VISIBILITIES}")
    if approval.payload.get("visibility") == visibility:
        return approval  # nothing changes, no new consent needed

    task = await session.get(Task, approval.task_id, populate_existing=True)
    execution = await session.get(Execution, approval.execution_id)
    connection = await session.get(IntegrationConnection, approval.connection_id)
    if task is None or execution is None or connection is None:
        raise NotFoundError("Approval context not found")
    if connection.composio_account_id != approval.target_account_id:
        await _invalidate(session, approval, "Target account changed", account.user.id)
        raise ApprovalInvalidError("Target account changed or is no longer connected")
    new = await request_approval(
        session,
        signer,
        task=task,
        execution=execution,
        requested_by=account.user.id,
        action_type=approval.action_type,
        toolkit_slug=approval.toolkit_slug,
        tool_slug=approval.tool_slug,
        connection=connection,
        payload={**approval.payload, "visibility": visibility},
        content_draft_id=approval.content_draft_id,
    )
    return await get_approval(session, account, new.id)


async def latest_for_execution(session: AsyncSession, execution_id: uuid.UUID) -> Approval | None:
    """The newest content version for an execution (the only one that can be decided)."""
    return await session.scalar(
        select(Approval)
        .where(Approval.execution_id == execution_id)
        .order_by(Approval.content_version.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


# ---------- reads ----------
async def get_approval(
    session: AsyncSession, account: Account, approval_id: uuid.UUID
) -> Approval:
    approval = await session.scalar(
        select(Approval)
        .where(Approval.id == approval_id, Approval.workspace_id == account.workspace.id)
        .execution_options(populate_existing=True)
    )
    if approval is None:
        raise NotFoundError("Approval not found")
    return approval


async def list_approvals(
    session: AsyncSession,
    account: Account,
    *,
    status: ApprovalStatus | None = None,
    task_id: uuid.UUID | None = None,
) -> list[Approval]:
    query = select(Approval).where(Approval.workspace_id == account.workspace.id)
    if status is not None:
        query = query.where(Approval.status == status)
    if task_id is not None:
        query = query.where(Approval.task_id == task_id)
    rows = await session.scalars(
        query.order_by(Approval.id.desc()).execution_options(populate_existing=True)
    )
    return list(rows)


# ---------- binding verification ----------
async def _binding_problem(
    session: AsyncSession, signer: ApprovalSigner, approval: Approval
) -> str | None:
    """None if the approval still authorizes exactly its action; else the reason."""
    if payload_hash(approval.payload) != approval.payload_hash:
        return "Payload changed after approval was requested"
    if not signer.verify(binding_of(approval), approval.binding_signature):
        return "Approval signature does not match its action"
    connection = await session.get(IntegrationConnection, approval.connection_id, populate_existing=True)
    if (
        connection is None
        or connection.workspace_id != approval.workspace_id
        or connection.status != ConnectionStatus.ACTIVE
        or connection.composio_account_id != approval.target_account_id
    ):
        return "Target account changed or is no longer connected"
    return None


async def _invalidate(
    session: AsyncSession, approval: Approval, reason: str, actor_user_id: uuid.UUID | None
) -> None:
    await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status.in_(OPEN_STATUSES))
        .values(status=ApprovalStatus.INVALIDATED, decision_note=reason, resolved_at=utcnow())
    )
    audit.record(
        session,
        workspace_id=approval.workspace_id,
        event="approval.invalidated",
        actor="system",
        actor_user_id=actor_user_id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        detail={"reason": reason},
    )
    await session.commit()


# ---------- user decisions ----------
def _require_owner(account: Account) -> None:
    # Single-user workspaces: only the owner may consent. Teams later add roles here.
    if account.workspace.owner_id != account.user.id:
        raise ForbiddenError("Only the workspace owner can decide on approvals")


async def _decide(
    session: AsyncSession,
    account: Account,
    approval: Approval,
    *,
    to: ApprovalStatus,
    note: str | None,
) -> None:
    result = await session.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status == ApprovalStatus.PENDING)
        .values(
            status=to,
            resolved_by=account.user.id,
            resolved_at=utcnow(),
            decision_note=note,
        )
    )
    if result.rowcount != 1:
        raise ConflictError("This approval has already been decided")


async def _task_of(session: AsyncSession, approval: Approval) -> Task:
    task = await session.get(Task, approval.task_id, populate_existing=True)
    assert task is not None
    return task


async def approve(
    session: AsyncSession,
    account: Account,
    signer: ApprovalSigner,
    approval_id: uuid.UUID,
    *,
    seen_payload_hash: str,
    seen_content_version: int,
    note: str | None = None,
) -> Approval:
    approval = await get_approval(session, account, approval_id)
    _require_owner(account)
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError("This approval has already been decided")
    # The user must approve exactly what is stored, not an earlier draft.
    if (
        seen_payload_hash != approval.payload_hash
        or seen_content_version != approval.content_version
    ):
        raise ApprovalMismatchError()
    problem = await _binding_problem(session, signer, approval)
    if problem:
        await _invalidate(session, approval, problem, account.user.id)
        raise ApprovalInvalidError(problem)

    await _decide(session, account, approval, to=ApprovalStatus.APPROVED, note=note)
    task = await _task_of(session, approval)
    await transition(
        session, task, allowed_from=[TaskStatus.AWAITING_APPROVAL], to=TaskStatus.APPROVED
    )
    audit.record(
        session,
        workspace_id=approval.workspace_id,
        event="approval.approved",
        actor="user",
        actor_user_id=account.user.id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        detail={
            "content_version": approval.content_version,
            "payload_hash": approval.payload_hash,
            "target_account_id": approval.target_account_id,
        },
    )
    await session.commit()
    return await get_approval(session, account, approval_id)


async def reject(
    session: AsyncSession, account: Account, approval_id: uuid.UUID, *, note: str | None
) -> Approval:
    approval = await get_approval(session, account, approval_id)
    _require_owner(account)
    await _decide(session, account, approval, to=ApprovalStatus.REJECTED, note=note)
    task = await _task_of(session, approval)
    await transition(
        session, task, allowed_from=[TaskStatus.AWAITING_APPROVAL], to=TaskStatus.REJECTED
    )
    audit.record(
        session,
        workspace_id=approval.workspace_id,
        event="approval.rejected",
        actor="user",
        actor_user_id=account.user.id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        detail={"note": note},
    )
    await session.commit()
    return await get_approval(session, account, approval_id)


async def request_changes(
    session: AsyncSession, account: Account, approval_id: uuid.UUID, *, note: str
) -> Approval:
    """User wants edits: this version is closed; the agent must propose a new one."""
    approval = await get_approval(session, account, approval_id)
    _require_owner(account)
    await _decide(session, account, approval, to=ApprovalStatus.CHANGES_REQUESTED, note=note)
    task = await _task_of(session, approval)
    await transition(
        session, task, allowed_from=[TaskStatus.AWAITING_APPROVAL], to=TaskStatus.RUNNING
    )
    audit.record(
        session,
        workspace_id=approval.workspace_id,
        event="approval.changes_requested",
        actor="user",
        actor_user_id=account.user.id,
        task_id=approval.task_id,
        execution_id=approval.execution_id,
        approval_id=approval.id,
        detail={"note": note},
    )
    await session.commit()
    return await get_approval(session, account, approval_id)


# ---------- execution side (used by the executor in step 5) ----------
async def verify_for_execution(
    session: AsyncSession, signer: ApprovalSigner, approval: Approval
) -> None:
    """Re-check everything right before acting. Raises if the approval can't be used."""
    if approval.status != ApprovalStatus.APPROVED or approval.consumed_at is not None:
        raise ApprovalInvalidError("Approval is not approved or was already used")
    problem = await _binding_problem(session, signer, approval)
    if problem:
        await _invalidate(session, approval, problem, None)
        raise ApprovalInvalidError(problem)


async def consume(session: AsyncSession, approval: Approval) -> bool:
    """Mark the approval used. Returns False if someone else already used it."""
    result = await session.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == ApprovalStatus.APPROVED,
            Approval.consumed_at.is_(None),
        )
        .values(consumed_at=utcnow())
    )
    await session.commit()
    return result.rowcount == 1


async def invalidate_for_connection_change(
    session: AsyncSession, connection: IntegrationConnection, reason: str
) -> int:
    """Called when an account is disconnected or replaced: its open approvals die.

    An approval survives only if the connection is still ACTIVE on the same account.
    """
    query = select(Approval).where(
        Approval.connection_id == connection.id,
        Approval.status.in_(OPEN_STATUSES),
        Approval.consumed_at.is_(None),
    )
    if connection.status == ConnectionStatus.ACTIVE:
        query = query.where(Approval.target_account_id != connection.composio_account_id)
    stale = list(await session.scalars(query))
    for approval in stale:
        await _invalidate(session, approval, reason, None)
    return len(stale)
