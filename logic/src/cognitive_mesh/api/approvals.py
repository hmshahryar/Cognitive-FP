import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.integrations.linkedin import Visibility
from cognitive_mesh.models import ApprovalStatus
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import approvals as svc
from cognitive_mesh.services.errors import ConfigurationError

router = APIRouter(prefix="/approvals", tags=["approvals"])


def get_signer(request: Request) -> ApprovalSigner:
    signer = request.app.state.approval_signer
    if signer is None:
        raise ConfigurationError("APPROVAL_SIGNING_KEY is not configured")
    return signer


SignerDep = Annotated[ApprovalSigner, Depends(get_signer)]


class ApprovalOut(BaseModel):
    """Everything the user needs to give informed consent. No signature is exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    execution_id: uuid.UUID
    action_type: str
    toolkit_slug: str
    tool_slug: str
    target_account_id: str
    payload: dict[str, Any]
    payload_hash: str
    content_version: int
    status: str
    decision_note: str | None
    created_at: datetime
    resolved_at: datetime | None


class ApproveIn(BaseModel):
    # Echo back exactly what was reviewed; a newer version makes this fail.
    payload_hash: str = Field(min_length=64, max_length=64)
    content_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class VisibilityIn(BaseModel):
    visibility: Visibility


def resume_graph(request: Request, execution_id: uuid.UUID) -> None:
    """After a decision is committed, wake the paused workflow (it re-reads the DB)."""
    request.app.state.runner.request_resume(execution_id)


class RequestChangesIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    account: CurrentAccountDep,
    session: SessionDep,
    status: ApprovalStatus | None = None,
    task_id: uuid.UUID | None = None,
) -> list[ApprovalOut]:
    rows = await svc.list_approvals(session, account, status=status, task_id=task_id)
    return [ApprovalOut.model_validate(a) for a in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID, account: CurrentAccountDep, session: SessionDep
) -> ApprovalOut:
    return ApprovalOut.model_validate(await svc.get_approval(session, account, approval_id))


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: uuid.UUID,
    body: ApproveIn,
    request: Request,
    account: CurrentAccountDep,
    session: SessionDep,
    signer: SignerDep,
) -> ApprovalOut:
    approval = await svc.approve(
        session,
        account,
        signer,
        approval_id,
        seen_payload_hash=body.payload_hash,
        seen_content_version=body.content_version,
        note=body.note,
    )
    resume_graph(request, approval.execution_id)
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: uuid.UUID,
    body: RejectIn,
    request: Request,
    account: CurrentAccountDep,
    session: SessionDep,
) -> ApprovalOut:
    approval = await svc.reject(session, account, approval_id, note=body.note)
    resume_graph(request, approval.execution_id)
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/request-changes", response_model=ApprovalOut)
async def request_changes(
    approval_id: uuid.UUID,
    body: RequestChangesIn,
    request: Request,
    account: CurrentAccountDep,
    session: SessionDep,
) -> ApprovalOut:
    approval = await svc.request_changes(session, account, approval_id, note=body.note)
    resume_graph(request, approval.execution_id)
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/visibility", response_model=ApprovalOut)
async def change_visibility(
    approval_id: uuid.UUID,
    body: VisibilityIn,
    account: CurrentAccountDep,
    session: SessionDep,
    signer: SignerDep,
) -> ApprovalOut:
    """Changing visibility changes the action: returns a NEW approval needing consent."""
    approval = await svc.change_visibility(
        session, account, signer, approval_id, visibility=body.visibility
    )
    return ApprovalOut.model_validate(approval)
