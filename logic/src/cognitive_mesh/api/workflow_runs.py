"""Review and decide workflow runs. Plans are created only by server-side playbooks."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.api.approvals import SignerDep
from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.models import WorkflowRun
from cognitive_mesh.workflows.engine import ledger

router = APIRouter(prefix="/workflow-runs", tags=["workflows"])


class WorkflowStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    position: int
    gate: int
    depends_on: list[str]
    action_type: str | None
    toolkit_slug: str | None
    tool_slug: str | None
    target_account_id: str | None
    payload: dict[str, Any] | None
    payload_hash: str
    wait_seconds: int | None
    wait_event: str | None
    status: str
    external_id: str | None
    external_url: str | None
    error: str | None


class WorkflowRunOut(BaseModel):
    """Everything needed for informed consent. No signature is exposed."""

    id: uuid.UUID
    playbook: str
    playbook_version: int
    title: str
    status: str
    plan_hash: str
    approved_gate: int
    last_gate: int
    error: str | None
    created_at: datetime
    steps: list[WorkflowStepOut]


class ApproveGateIn(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    gate: int = Field(ge=0)


class RejectRunIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


async def _out(session: SessionDep, run: WorkflowRun) -> WorkflowRunOut:
    steps = await ledger.load_steps(session, run.id)
    return WorkflowRunOut(
        id=run.id,
        playbook=run.playbook,
        playbook_version=run.playbook_version,
        title=run.title,
        status=run.status,
        plan_hash=run.plan_hash,
        approved_gate=run.approved_gate,
        last_gate=run.last_gate,
        error=run.error,
        created_at=run.created_at,
        steps=[WorkflowStepOut.model_validate(step) for step in steps],
    )


@router.get("", response_model=list[WorkflowRunOut])
async def list_runs(account: CurrentAccountDep, session: SessionDep) -> list[WorkflowRunOut]:
    return [await _out(session, run) for run in await ledger.list_runs(session, account)]


@router.get("/{run_id}", response_model=WorkflowRunOut)
async def get_run(
    run_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> WorkflowRunOut:
    return await _out(session, await ledger.get_run(session, account, run_id))


@router.post("/{run_id}/approve", response_model=WorkflowRunOut)
async def approve(
    run_id: uuid.UUID,
    body: ApproveGateIn,
    request: Request,
    account: CurrentAccountDep,
    session: SessionDep,
    signer: SignerDep,
) -> WorkflowRunOut:
    run = await ledger.approve_gate(
        session, account, signer, run_id, seen_plan_hash=body.plan_hash, seen_gate=body.gate
    )
    request.app.state.runner.wake()
    return await _out(session, run)


@router.post("/{run_id}/reject", response_model=WorkflowRunOut)
async def reject(
    run_id: uuid.UUID, body: RejectRunIn, account: CurrentAccountDep, session: SessionDep
) -> WorkflowRunOut:
    return await _out(session, await ledger.reject_run(session, account, run_id, note=body.note))


@router.post("/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel(
    run_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> WorkflowRunOut:
    return await _out(session, await ledger.cancel_run(session, account, run_id))
