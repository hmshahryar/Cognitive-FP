"""Stop the same write from being prepared twice while the provider's search can't see it yet."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.models import Approval, ApprovalStatus, Publication, PublicationStatus

RECENT = timedelta(minutes=30)


async def already_requested(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    action_type: str,
    arguments: dict[str, Any],
    window: timedelta = RECENT,
) -> bool:
    """True when identical arguments are pending, or were approved recently and didn't fail."""
    cutoff = datetime.now(UTC) - window
    rows = await session.execute(
        select(Approval.payload, Publication.status)
        .outerjoin(Publication, Publication.approval_id == Approval.id)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.action_type == action_type,
            or_(
                Approval.status == ApprovalStatus.PENDING,
                (Approval.status == ApprovalStatus.APPROVED) & (Approval.created_at >= cutoff),
            ),
        )
    )
    return any(
        payload.get("arguments") == arguments and status != PublicationStatus.FAILED
        for payload, status in rows
    )
