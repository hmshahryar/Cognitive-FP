"""Append-only audit trail. Rows are only ever inserted."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.models import AuditEvent


def record(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event: str,
    actor: str,
    actor_user_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Stage an audit row in the caller's transaction (commits with the change it records)."""
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            event=event,
            actor=actor,
            actor_user_id=actor_user_id,
            task_id=task_id,
            execution_id=execution_id,
            approval_id=approval_id,
            detail=detail or {},
        )
    )
