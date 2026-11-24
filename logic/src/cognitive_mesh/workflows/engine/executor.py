"""Run approved plan steps, each at most once.

    reserve: re-check the plan, then commit PENDING -> SENDING
    send:    one gateway call, never retried
    finish:  SUCCEEDED, FAILED (definitely did not happen) or NEEDS_CHECK (outcome unknown)

The reservation is committed before sending. After a crash, recovery marks SENDING steps as
NEEDS_CHECK and skips what depends on them. This follows LangGraph's guidance that resumed work
can run again, so an external side effect must be protected by its own durable record.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db import Database
from cognitive_mesh.db.base import utcnow
from cognitive_mesh.integrations.actions import get_action
from cognitive_mesh.integrations.gateway import ActionRejectedError, IntegrationGateway
from cognitive_mesh.models import (
    IntegrationConnection,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import audit
from cognitive_mesh.workflows.engine.ledger import close_run, load_steps, plan_problem
from cognitive_mesh.workflows.engine.plan import (
    PlanReferenceError,
    is_safe_output,
    resolve_references,
)

logger = logging.getLogger(__name__)
_DRIVABLE = (WorkflowRunStatus.EXECUTING, WorkflowRunStatus.WAITING)
_UNFINISHED = (
    WorkflowStepStatus.PENDING, WorkflowStepStatus.WAITING, WorkflowStepStatus.SENDING,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)  # SQLite returns naive values


async def _finish_step(
    session: AsyncSession,
    step: WorkflowStep,
    status: WorkflowStepStatus,
    *,
    allowed_from: WorkflowStepStatus,
    external_id: str | None = None,
    external_url: str | None = None,
    error: str | None = None,
) -> bool:
    moved = await session.execute(
        update(WorkflowStep)
        .where(WorkflowStep.id == step.id, WorkflowStep.status == allowed_from)
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
        return False
    audit.record(
        session,
        workspace_id=step.workspace_id,
        event=f"workflow.step_{status.value.lower()}",
        actor="system",
        detail={"run_id": str(step.run_id), "step": step.key, "external_id": external_id},
    )
    await session.commit()
    return True


def _dependency_state(step: WorkflowStep, by_key: dict[str, WorkflowStep]) -> str:
    dependencies = [by_key[key] for key in step.depends_on]
    if any(d.status in _UNFINISHED for d in dependencies):
        return "blocked"
    if all(d.status == WorkflowStepStatus.SUCCEEDED for d in dependencies):
        return "ready"
    return "ready" if step.run_after_failure else "skip"


async def _settle(
    session: AsyncSession, run: WorkflowRun, steps: list[WorkflowStep]
) -> tuple[WorkflowStep | None, bool]:
    """Apply skips and waits. Returns (next action step, whether anything changed)."""
    by_key = {step.key: step for step in steps}
    changed = False
    now = utcnow()
    for step in steps:
        if step.gate > run.approved_gate:
            continue
        if step.status == WorkflowStepStatus.WAITING and step.not_before is not None:
            if _aware(step.not_before) <= now:
                timed_out = step.wait_event is not None
                await _finish_step(
                    session, step,
                    WorkflowStepStatus.FAILED if timed_out else WorkflowStepStatus.SUCCEEDED,
                    allowed_from=WorkflowStepStatus.WAITING,
                    error="Timed out waiting for the event" if timed_out else None,
                )
                await session.refresh(step)
                changed = True
            continue
        if step.status != WorkflowStepStatus.PENDING:
            continue
        state = _dependency_state(step, by_key)
        if state == "blocked":
            continue
        if state == "skip":
            await _finish_step(
                session, step, WorkflowStepStatus.SKIPPED,
                allowed_from=WorkflowStepStatus.PENDING,
                error="An earlier step did not complete",
            )
            await session.refresh(step)
            changed = True
        elif step.action_type is None:
            due = now + timedelta(seconds=step.wait_seconds) if step.wait_seconds else None
            await session.execute(
                update(WorkflowStep)
                .where(WorkflowStep.id == step.id, WorkflowStep.status == WorkflowStepStatus.PENDING)
                .values(status=WorkflowStepStatus.WAITING, not_before=due, started_at=now)
            )
            await session.commit()
            await session.refresh(step)
            changed = True
        else:
            return step, changed
    return None, changed


def _final_status(steps: list[WorkflowStep]) -> WorkflowRunStatus:
    statuses = {step.status for step in steps}
    if WorkflowStepStatus.NEEDS_CHECK in statuses:
        return WorkflowRunStatus.NEEDS_CHECK
    if statuses == {WorkflowStepStatus.SUCCEEDED}:
        return WorkflowRunStatus.COMPLETED
    if WorkflowStepStatus.SUCCEEDED in statuses:
        return WorkflowRunStatus.PARTIALLY_COMPLETED
    return WorkflowRunStatus.FAILED


async def _after_gate(session: AsyncSession, run: WorkflowRun, steps: list[WorkflowStep]) -> bool:
    """No action is ready: wait, ask for the next gate, or finish."""
    current = [step for step in steps if step.gate <= run.approved_gate]
    if any(step.status == WorkflowStepStatus.WAITING for step in current):
        if run.status == WorkflowRunStatus.WAITING:
            return False
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run.id, WorkflowRun.status == WorkflowRunStatus.EXECUTING)
            .values(status=WorkflowRunStatus.WAITING)
        )
        await session.commit()
        return True
    if any(step.status in _UNFINISHED for step in current):
        return False
    clean = all(step.status == WorkflowStepStatus.SUCCEEDED for step in current)
    if clean and run.approved_gate < run.last_gate:
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run.id, WorkflowRun.status.in_(_DRIVABLE))
            .values(status=WorkflowRunStatus.AWAITING_APPROVAL)
        )
        audit.record(
            session,
            workspace_id=run.workspace_id,
            event="workflow.gate_requested",
            actor="system",
            detail={"run_id": str(run.id), "gate": run.approved_gate + 1},
        )
        await session.commit()
        return True
    if not clean:  # never ask to continue after a failed or uncertain gate
        await session.execute(
            update(WorkflowStep)
            .where(WorkflowStep.run_id == run.id, WorkflowStep.status == WorkflowStepStatus.PENDING)
            .values(
                status=WorkflowStepStatus.SKIPPED,
                error="An earlier approval gate did not complete",
                finished_at=utcnow(),
            )
        )
        await session.commit()
        steps = await load_steps(session, run.id)
    status = _final_status(steps)
    moved = await session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id, WorkflowRun.status.in_(_DRIVABLE))
        .values(status=status)
    )
    if moved.rowcount == 1:
        audit.record(
            session,
            workspace_id=run.workspace_id,
            event=f"workflow.{status.value.lower()}",
            actor="system",
            detail={"run_id": str(run.id)},
        )
    await session.commit()
    return moved.rowcount == 1


async def _reserve(
    session: AsyncSession, run: WorkflowRun, step: WorkflowStep, steps: list[WorkflowStep]
) -> dict | None:
    """The exact resolved payload after reservation, or None if the step ended without sending."""
    action = get_action(step.action_type or "")
    connection = (
        await session.get(IntegrationConnection, step.connection_id, populate_existing=True)
        if step.connection_id
        else None
    )
    problem: str | None = None
    resolved: dict | None = None
    if action is None or action.tool_slug != step.tool_slug or action.toolkit != step.toolkit_slug:
        problem = "This step is not for a supported action"
    elif connection is None or not action.is_permitted(set(connection.granted_scopes or [])):
        problem = f"{action.app} permission is missing. Reconnect {action.app}."
    else:
        outputs = {
            s.key: s.external_id
            for s in steps
            if s.status == WorkflowStepStatus.SUCCEEDED and s.external_id
        }
        try:
            resolved = resolve_references(step.payload, outputs)
            action.validate(resolved)
        except (PlanReferenceError, ValueError) as exc:
            problem = str(exc)
    if problem:
        await _finish_step(
            session, step, WorkflowStepStatus.FAILED,
            allowed_from=WorkflowStepStatus.PENDING, error=problem,
        )
        return None
    reserved = await session.execute(
        update(WorkflowStep)
        .where(
            WorkflowStep.id == step.id,
            WorkflowStep.status == WorkflowStepStatus.PENDING,
            exists(
                select(WorkflowRun.id).where(
                    WorkflowRun.id == run.id,
                    WorkflowRun.status == WorkflowRunStatus.EXECUTING,
                    WorkflowRun.approved_gate >= step.gate,
                )
            ),
        )
        .values(
            status=WorkflowStepStatus.SENDING, resolved_payload=resolved, started_at=utcnow()
        )
    )
    if reserved.rowcount != 1:
        await session.rollback()
        return None
    audit.record(
        session,
        workspace_id=step.workspace_id,
        event="workflow.step_started",
        actor="system",
        detail={"run_id": str(run.id), "step": step.key, "tool": step.tool_slug},
    )
    await session.commit()
    return resolved


async def _send(
    db: Database, gateway: IntegrationGateway, run: WorkflowRun, step: WorkflowStep, resolved: dict
) -> None:
    action = get_action(step.action_type or "")
    assert action is not None
    status, external_id, url, error = WorkflowStepStatus.SUCCEEDED, None, None, None
    try:
        data = await gateway.execute_approved_action(
            user_id=str(run.created_by),
            toolkit_slug=step.toolkit_slug or "",
            tool_slug=step.tool_slug or "",
            connected_account_id=step.target_account_id or "",
            arguments=action.arguments(resolved),
        )
        external_id = action.result_id(data)
        url = action.result_url(data, external_id)
        if external_id is None or not is_safe_output(external_id):
            status, error = WorkflowStepStatus.NEEDS_CHECK, "success without a usable result id"
        elif action.follow_up is not None:
            try:
                extra = action.follow_up(resolved, data)
                if extra is not None:
                    await gateway.execute_approved_action(
                        user_id=str(run.created_by),
                        toolkit_slug=step.toolkit_slug or "",
                        tool_slug=extra.tool_slug,
                        connected_account_id=step.target_account_id or "",
                        arguments=extra.arguments,
                    )
            except Exception as exc:  # noqa: BLE001 - the first call already succeeded
                status, error = WorkflowStepStatus.NEEDS_CHECK, repr(exc)
    except ActionRejectedError as exc:
        status, error = WorkflowStepStatus.FAILED, str(exc)
    except Exception as exc:  # noqa: BLE001 - after sending, any error means outcome unknown
        status, error = WorkflowStepStatus.NEEDS_CHECK, repr(exc)
    async with db.sessionmaker() as session:
        await _finish_step(
            session, step, status, allowed_from=WorkflowStepStatus.SENDING,
            external_id=external_id, external_url=url, error=error,
        )


async def advance_run(
    db: Database, gateway: IntegrationGateway, signer: ApprovalSigner, run_id: uuid.UUID
) -> bool:
    """Drive one run as far as it can go. Returns True if anything changed."""
    progressed = False
    while True:
        async with db.sessionmaker() as session:
            run = await session.get(WorkflowRun, run_id, populate_existing=True)
            if run is None or run.status not in _DRIVABLE:
                return progressed
            steps = await load_steps(session, run.id)
            problem = await plan_problem(session, signer, run, steps)
            if problem:
                await close_run(
                    session, run, WorkflowRunStatus.INVALIDATED, allowed_from=_DRIVABLE,
                    actor="system", reason=problem,
                )
                return True
            step, changed = await _settle(session, run, steps)
            progressed = progressed or changed
            if step is None:
                steps = await load_steps(session, run.id)
                run = await session.get(WorkflowRun, run_id, populate_existing=True)
                assert run is not None
                moved = await _after_gate(session, run, steps)
                if moved:
                    progressed = True
                    continue
                if changed:
                    continue
                return progressed
            if run.status != WorkflowRunStatus.EXECUTING:
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == run.id, WorkflowRun.status == WorkflowRunStatus.WAITING)
                    .values(status=WorkflowRunStatus.EXECUTING)
                )
                await session.commit()
                run = await session.get(WorkflowRun, run_id, populate_existing=True)
                assert run is not None
            resolved = await _reserve(session, run, step, steps)
            progressed = True
        if resolved is not None:
            await _send(db, gateway, run, step, resolved)


async def advance_due(db: Database, gateway: IntegrationGateway, signer: ApprovalSigner) -> bool:
    """Called by the worker. Returns True if any run moved."""
    async with db.sessionmaker() as session:
        run_ids = list(
            await session.scalars(
                select(WorkflowRun.id)
                .where(WorkflowRun.status.in_(_DRIVABLE))
                .order_by(WorkflowRun.id)
            )
        )
    moved = False
    for run_id in run_ids:
        try:
            moved = await advance_run(db, gateway, signer, run_id) or moved
        except Exception:  # one broken run must not block every other user's work
            logger.exception("Workflow run %s could not advance", run_id)
    return moved


async def recover_interrupted(db: Database) -> int:
    """After a restart, an in-flight send has an unknown outcome and is never sent again."""
    async with db.sessionmaker() as session:
        interrupted = list(
            await session.scalars(
                select(WorkflowStep).where(WorkflowStep.status == WorkflowStepStatus.SENDING)
            )
        )
        count = 0
        for step in interrupted:
            if await _finish_step(
                session, step, WorkflowStepStatus.NEEDS_CHECK,
                allowed_from=WorkflowStepStatus.SENDING, error="interrupted while sending",
            ):
                count += 1
    return count
