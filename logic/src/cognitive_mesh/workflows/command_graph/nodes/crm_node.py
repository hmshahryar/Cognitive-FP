"""CRM agent: answer CRM reads or prepare one exact contact change."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.integrations import hubspot
from cognitive_mesh.integrations.crm_providers import PROVIDERS, CrmProvider
from cognitive_mesh.models import (
    Approval,
    ApprovalStatus,
    Chat,
    ContentDraft,
    MessageRole,
    Publication,
    PublicationStatus,
    TaskStatus,
)
from cognitive_mesh.services import audit, crm_actions
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import (
    CRM_NOT_CONNECTED,
    HUBSPOT_NOT_CONNECTED,
    SALESFORCE_NOT_CONNECTED,
    ZOHO_NOT_CONNECTED,
)
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.crm_commands import MULTIPLE_CRMS, CrmCommand, crm_from_dict

_NOT_CONNECTED = {
    "hubspot": HUBSPOT_NOT_CONNECTED,
    "salesforce": SALESFORCE_NOT_CONNECTED,
    "zoho": ZOHO_NOT_CONNECTED,
}
_SEARCH_LAG = timedelta(minutes=30)


async def _recent_create_emails(
    session: AsyncSession, workspace_id: uuid.UUID, provider: CrmProvider
) -> frozenset[str]:
    """Emails this workspace has pending or recently sent, for CRMs whose search can lag."""
    cutoff = datetime.now(UTC) - _SEARCH_LAG
    rows = await session.execute(
        select(Approval.payload, Publication.status)
        .outerjoin(Publication, Publication.approval_id == Approval.id)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.action_type == provider.create_action,
            or_(
                Approval.status == ApprovalStatus.PENDING,
                Approval.status == ApprovalStatus.APPROVED,
            ),
            or_(Approval.status == ApprovalStatus.PENDING, Approval.created_at >= cutoff),
        )
    )
    return frozenset(
        str(payload["contact"]["email"]).casefold()
        for payload, status in rows
        if status != PublicationStatus.FAILED
    )


def _choose_provider(
    command: CrmCommand, connected: list[CrmProvider]
) -> tuple[CrmProvider | None, str | None, bool]:
    """(provider, message, is_failure). A CRM is never guessed when more than one could apply."""
    named = command.provider
    if named == MULTIPLE_CRMS:
        return None, "I can change one CRM per command. Tell me which one to use.", False
    if named is not None:
        provider = PROVIDERS[named]
        if provider not in connected:
            return None, _NOT_CONNECTED[named], True
        return provider, None, False
    if not connected:
        return None, CRM_NOT_CONNECTED, True
    if len(connected) > 1:
        names = " or ".join(p.name for p in connected)
        return None, f"Which CRM should I use, {names}? Send the command again and name it.", False
    return connected[0], None, False


def build_crm_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    fail = ctx.fail
    finish_with_reply = ctx.finish_with_reply

    async def crm_action(state: GraphState) -> GraphState:
        """Answer CRM reads, or prepare one exact change. Uses no AI and changes nothing."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        if state.get("feedback"):
            return await finish_with_reply(
                state, "This contact change can't be revised. Send a new command to change it."
            )
        task = await load_task(state)
        execution_id = uuid.UUID(state["execution_id"])
        first = state.get("next_version", 1)
        async with db.sessionmaker() as s:
            saved = await s.scalar(
                select(ContentDraft).where(
                    ContentDraft.execution_id == execution_id,
                    ContentDraft.version >= first,
                    ContentDraft.is_final.is_(True),
                )
            )
            connected = [
                provider
                for provider in PROVIDERS.values()
                if await get_active_connection(s, task.workspace_id, provider.toolkit) is not None
            ]

        if saved is None:
            command = crm_from_dict(state["crm"])
            provider, message, failed = _choose_provider(command, connected)
            if provider is None:
                assert message is not None
                if failed:
                    return await fail(state, message, "crm not connected")
                return await finish_with_reply(state, message)
            try:
                if command.kind == "find":
                    answer = await crm_actions.answer_find(
                        deps.gateway, state["user_id"], provider, command
                    )
                    return await finish_with_reply(state, answer)
                recent: frozenset[str] = frozenset()
                if provider.search_can_lag and command.kind == "create":
                    async with db.sessionmaker() as s:
                        recent = await _recent_create_emails(s, task.workspace_id, provider)
                prepared = await crm_actions.prepare_change(
                    deps.gateway, state["user_id"], provider, command, recent_emails=recent
                )
            except crm_actions.CrmStop as stop:
                return await finish_with_reply(state, stop.message)
            except hubspot.InvalidCrmActionError as exc:
                return await finish_with_reply(state, f"I can't prepare that change: {exc}.")
            if not await is_current(state, TaskStatus.RUNNING):
                return {"outcome": "cancelled"}
            async with db.sessionmaker() as s:
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=first, channel=provider.toolkit, text=prepared.text,
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model="none", provider="user",
                    prompt_version="none", claims=[], quality={},
                    research={
                        "mode": "crm",
                        "action_type": prepared.action_type,
                        "payload": prepared.payload,
                    },
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content=f"This {provider.name} change is ready for your approval. Nothing "
                    "has been changed yet.",
                )
                audit.record(
                    s, workspace_id=task.workspace_id, event="draft.ready", actor="manager",
                    task_id=task.id, execution_id=execution_id,
                    detail={"version": first, "action_type": prepared.action_type},
                )
                await s.commit()
        return {
            "action_type": saved.research["action_type"],
            "action_payload": saved.research["payload"],
            "draft_text": saved.text,
            "draft_id": str(saved.id),
            "next_version": saved.version + 1,
            "feedback": [],
            "outcome": "",
        }

    return {"crm_action": crm_action}
