"""Prepare one exact Gmail action."""

import uuid

from sqlalchemy import select

from cognitive_mesh.integrations import gmail
from cognitive_mesh.models import Chat, ContentDraft, MessageRole, TaskStatus
from cognitive_mesh.services import audit, conversation, email_actions
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import GMAIL_NOT_CONNECTED
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.content_pipeline import WorkflowStopped
from cognitive_mesh.workflows.email_commands import EmailCommand


def build_email_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    fail = ctx.fail
    finish_with_reply = ctx.finish_with_reply
    ai_context = ctx.ai_context

    async def gmail_action(state: GraphState) -> GraphState:
        """Prepare one exact email action. Nothing is sent or changed here."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
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
            connection = await get_active_connection(s, task.workspace_id, gmail.TOOLKIT)
        if connection is None:
            return await fail(state, GMAIL_NOT_CONNECTED, "gmail not connected")

        if saved is None:
            async def cancelled() -> bool:
                return not await is_current(state, TaskStatus.RUNNING)

            feedback = state.get("feedback") or []
            email_command = EmailCommand(**state["email"])
            needs_ai = email_command.kind == "write" or (
                email_command.kind == "send" and bool(feedback)
            )
            context = (
                await ai_context(state, email_command.topic or email_command.body or "")
                if needs_ai
                else conversation.ConversationContext()
            )
            try:
                prepared = await email_actions.prepare(
                    deps.gateway, deps.build_pipeline,
                    user_id=state["user_id"],
                    command=email_command,
                    model_name=deps.settings.llm_model,
                    feedback=feedback,
                    previous_text=state.get("draft_text") if feedback else None,
                    should_stop=cancelled,
                    context=context.text,
                    trusted_text=context.trusted_text,
                )
            except email_actions.EmailStop as stop:
                return await finish_with_reply(state, stop.message)
            except WorkflowStopped:
                return {"outcome": "cancelled"}
            if await cancelled():
                return {"outcome": "cancelled"}
            async with db.sessionmaker() as s:
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=first, channel="gmail", text=prepared.text,
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model=prepared.model,
                    provider=prepared.provider, prompt_version=prepared.prompt_version,
                    claims=[], quality={},
                    research={
                        "mode": "email",
                        "action_type": prepared.action_type,
                        "payload": prepared.payload,
                    },
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content="This email action is ready for your approval. Nothing has been "
                    "sent or changed.",
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

    return {"gmail_action": gmail_action}
