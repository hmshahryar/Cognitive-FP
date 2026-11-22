"""Create, read and decide signed workflow runs.

The step ledger is the source of truth. A user approves one gate of one exact plan, and every
status change is compare-and-set, so double clicks and retries cannot authorize anything twice.
"""

import hashlib
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.integrations.actions import get_action
from cognitive_mesh.models import (
    ConnectionStatus,
    IntegrationConnection,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from cognitive_mesh.security.approval_signing import ApprovalSigner, PlanBinding, payload_hash
from cognitive_mesh.services import audit
from cognitive_mesh.services.accounts import Account
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.services.errors import (
    ApprovalInvalidError,
    ApprovalMismatchError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from cognitive_mesh.workflows.engine.plan import PlanInvalidError, WorkflowPlan

ACTIVE_RUN_STATUSES = (
    WorkflowRunStatus.AWAITING_APPROVAL,
    WorkflowRunStatus.EXECUTING,
    WorkflowRunStatus.WAITING,
)
_OPEN_STEPS = (WorkflowStepStatus.PENDING, WorkflowStepStatus.WAITING)


def _step_hash(payload: dict[str, Any] | None, step: Any) -> str:
    return payload_hash(
        {
            "payload": payload,
            "wait_seconds": step.wait_seconds,
            "wait_event": step.wait_event,
            "run_after_failure": step.run_after_failure,
        }
    )


def _binding_row(step: WorkflowStep) -> tuple[str, ...]:
    return (
        step.key,
        str(step.position),
        str(step.gate),
        step.action_type or "",
        step.toolkit_slug or "",
        step.tool_slug or "",
        str(step.connection_id or ""),
        step.target_account_id or "",
        step.payload_hash,
        ",".join(step.depends_on),
    )


def _plan_hash(steps: list[WorkflowStep]) -> str:
    return payload_hash({"steps": [_binding_row(step) for step in steps]})


def _binding(run: WorkflowRun, steps: list[WorkflowStep]) -> PlanBinding:
    return PlanBinding(
        run_id=run.id,
        workspace_id=run.workspace_id,
        requested_by=run.created_by,
        playbook=run.playbook,
        playbook_version=run.playbook_version,
        title=run.title,
        last_gate=run.last_gate,
        plan_hash=run.plan_hash,
        steps=tuple(_binding_row(step) for step in steps),
    )


async def create_run(
    session: AsyncSession,
    signer: ApprovalSigner,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    plan: WorkflowPlan,
    chat_id: uuid.UUID | None = None,
) -> WorkflowRun:
    """Server-side only. No API accepts a plan from the browser or from a model."""
    run_id = uuid7()
    steps: list[WorkflowStep] = []
    for position, spec in enumerate(plan.steps):
        action = get_action(spec.action_type) if spec.action_type else None
        connection: IntegrationConnection | None = None
        if action is not None:
            connection = await get_active_connection(session, workspace_id, action.toolkit)
            if connection is None:
                raise ConflictError(f"{action.app} is not connected")
        digest = _step_hash(spec.payload, spec)
        key_source = f"workflow-step:{run_id}:{spec.key}:{digest}".encode()
        steps.append(
            WorkflowStep(
                id=uuid7(),
                workspace_id=workspace_id,
                run_id=run_id,
                key=spec.key,
                position=position,
                gate=spec.gate,
                depends_on=list(spec.depends_on),
                run_after_failure=spec.run_after_failure,
                action_type=spec.action_type,
                toolkit_slug=action.toolkit if action else None,
                tool_slug=action.tool_slug if action else None,
                connection_id=connection.id if connection else None,
                target_account_id=connection.composio_account_id if connection else None,
                payload=spec.payload,
                payload_hash=digest,
                wait_seconds=spec.wait_seconds,
                wait_event=spec.wait_event,
                idempotency_key=hashlib.sha256(key_source).hexdigest(),
                status=WorkflowStepStatus.PENDING,
            )
        )
    run = WorkflowRun(
        id=run_id,
        workspace_id=workspace_id,
        created_by=created_by,
        chat_id=chat_id,
        playbook=plan.playbook,
        playbook_version=plan.version,
        title=plan.title,
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        plan_hash=_plan_hash(steps),
        binding_signature="",
        approved_gate=-1,
        last_gate=plan.last_gate,
    )
    run.binding_signature = signer.sign(_binding(run, steps))
    session.add(run)
    await session.flush()
    session.add_all(steps)
    audit.record(
        session,
        workspace_id=workspace_id,
        event="workflow.requested",
        actor="system",
        detail={"run_id": str(run_id), "playbook": plan.playbook, "plan_hash": run.plan_hash},
    )
    await session.commit()
    return run


def build_plan(**fields: Any) -> WorkflowPlan:
    """A validated plan, with one error type for callers."""
    try:
        return WorkflowPlan(**fields)
    except (ValidationError, PlanInvalidError) as exc:
        raise PlanInvalidError(str(exc)) from exc


async def load_steps(session: AsyncSession, run_id: uuid.UUID) -> list[WorkflowStep]:
    rows = await session.scalars(
        select(WorkflowStep)
        .where(WorkflowStep.run_id == run_id)
        .order_by(WorkflowStep.position)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def get_run(session: AsyncSession, account: Account, run_id: uuid.UUID) -> WorkflowRun:
    run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == account.workspace.id)
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFoundError("Workflow run not found")
    return run


async def list_runs(session: AsyncSession, account: Account) -> list[WorkflowRun]:
    rows = await session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.workspace_id == account.workspace.id)
        .order_by(WorkflowRun.id.desc())
        .limit(100)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def plan_problem(
    session: AsyncSession, signer: ApprovalSigner, run: WorkflowRun, steps: list[WorkflowStep]
) -> str | None:
    """None if the stored plan is still exactly what was signed and its accounts still match."""
    for step in steps:
        if step.workspace_id != run.workspace_id or _step_hash(step.payload, step) != step.payload_hash:
            return "A step changed after the plan was created"
    if _plan_hash(steps) != run.plan_hash:
        return "The plan changed after it was created"
    if not signer.verify(_binding(run, steps), run.binding_signature):
        return "The plan signature does not match"
    for step in steps:
        if step.action_type is None or step.status not in _OPEN_STEPS:
            continue
        connection = (
            await session.get(IntegrationConnection, step.connection_id, populate_existing=True)
            if step.connection_id
            else None
        )
        if (
            connection is None
            or connection.workspace_id != run.workspace_id
            or connection.status != ConnectionStatus.ACTIVE
            or connection.composio_account_id != step.target_account_id
        ):
            return "A target account changed or is no longer connected"
    return None


async def close_run(
    session: AsyncSession,
    run: WorkflowRun,
    status: WorkflowRunStatus,
    *,
    allowed_from: tuple[WorkflowRunStatus, ...],
    actor: str,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> bool:
    """Stop a run and skip work that has not started. A step already sending is left alone."""
    moved = await session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status.in_(allowed_from))
        .values(status=status, error=reason[:2000] if reason else None, decided_by=actor_user_id)
    )
    if moved.rowcount != 1:
        await session.rollback()
        return False
    await session.execute(
        update(WorkflowStep)
        .where(WorkflowStep.run_id == run.id, WorkflowStep.status.in_(_OPEN_STEPS))
        .values(status=WorkflowStepStatus.SKIPPED, error=reason, finished_at=utcnow())
    )
    audit.record(
        session,
        workspace_id=run.workspace_id,
        event=f"workflow.{status.value.lower()}",
        actor=actor,
        actor_user_id=actor_user_id,
        detail={"run_id": str(run.id), "reason": reason},
    )
    await session.commit()
    return True


def _require_owner(account: Account) -> None:
    if account.workspace.owner_id != account.user.id:
        raise ForbiddenError("Only the workspace owner can decide on workflows")


async def approve_gate(
    session: AsyncSession,
    account: Account,
    signer: ApprovalSigner,
    run_id: uuid.UUID,
    *,
    seen_plan_hash: str,
    seen_gate: int,
) -> WorkflowRun:
    """Approve the next gate of the exact plan the user saw."""
    run = await get_run(session, account, run_id)
    _require_owner(account)
    if run.status != WorkflowRunStatus.AWAITING_APPROVAL:
        raise ConflictError("This workflow is not waiting for approval")
    if seen_plan_hash != run.plan_hash or seen_gate != run.approved_gate + 1:
        raise ApprovalMismatchError()
    problem = await plan_problem(session, signer, run, await load_steps(session, run.id))
    if problem:
        await close_run(
            session, run, WorkflowRunStatus.INVALIDATED,
            allowed_from=(WorkflowRunStatus.AWAITING_APPROVAL,), actor="system", reason=problem,
        )
        raise ApprovalInvalidError(problem)
    moved = await session.execute(
        update(WorkflowRun)
        .where(
            WorkflowRun.id == run.id,
            WorkflowRun.status == WorkflowRunStatus.AWAITING_APPROVAL,
            WorkflowRun.approved_gate == seen_gate - 1,
        )
        .values(
            status=WorkflowRunStatus.EXECUTING, approved_gate=seen_gate, decided_by=account.user.id
        )
    )
    if moved.rowcount != 1:
        await session.rollback()
        raise ConflictError("This workflow has already been decided")
    audit.record(
        session,
        workspace_id=run.workspace_id,
        event="workflow.gate_approved",
        actor="user",
        actor_user_id=account.user.id,
        detail={"run_id": str(run.id), "gate": seen_gate, "plan_hash": run.plan_hash},
    )
    await session.commit()
    return await get_run(session, account, run_id)


async def reject_run(
    session: AsyncSession, account: Account, run_id: uuid.UUID, *, note: str | None
) -> WorkflowRun:
    run = await get_run(session, account, run_id)
    _require_owner(account)
    closed = await close_run(
        session, run, WorkflowRunStatus.REJECTED,
        allowed_from=(WorkflowRunStatus.AWAITING_APPROVAL,), actor="user",
        actor_user_id=account.user.id, reason=note or "Rejected by the user",
    )
    if not closed:
        raise ConflictError("This workflow is not waiting for approval")
    return await get_run(session, account, run_id)


async def cancel_run(session: AsyncSession, account: Account, run_id: uuid.UUID) -> WorkflowRun:
    run = await get_run(session, account, run_id)
    _require_owner(account)
    closed = await close_run(
        session, run, WorkflowRunStatus.CANCELLED, allowed_from=ACTIVE_RUN_STATUSES, actor="user",
        actor_user_id=account.user.id, reason="Cancelled by the user",
    )
    if not closed:
        raise ConflictError("This workflow has already finished")
    return await get_run(session, account, run_id)


async def signal_event(
    session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID, event: str
) -> bool:
    """Complete a step waiting for this event. Server-side only, and safe to repeat."""
    moved = await session.execute(
        update(WorkflowStep)
        .where(
            WorkflowStep.run_id == run_id,
            WorkflowStep.workspace_id == workspace_id,
            WorkflowStep.wait_event == event,
            WorkflowStep.status == WorkflowStepStatus.WAITING,
        )
        .values(status=WorkflowStepStatus.SUCCEEDED, finished_at=utcnow())
    )
    if moved.rowcount == 0:
        await session.rollback()
        return False
    await session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id, WorkflowRun.status == WorkflowRunStatus.WAITING)
        .values(status=WorkflowRunStatus.EXECUTING)
    )
    await session.commit()
    return True
