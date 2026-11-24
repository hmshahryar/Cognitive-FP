"""Social agent: prepare one exact Facebook Page or Instagram post for approval."""

import uuid

from sqlalchemy import select

from cognitive_mesh.models import Chat, ContentDraft, MessageRole, TaskStatus
from cognitive_mesh.services import audit, social_actions
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.services.duplicate_guard import already_requested
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import (
    CANVA_NOT_CONNECTED,
    FACEBOOK_NOT_CONNECTED,
    INSTAGRAM_NOT_CONNECTED,
)
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.social_commands import social_from_dict

_NAMES = {"facebook": "Facebook", "instagram": "Instagram"}
_NOT_CONNECTED = {"facebook": FACEBOOK_NOT_CONNECTED, "instagram": INSTAGRAM_NOT_CONNECTED}
_ALREADY_REQUESTED = (
    "This exact post is already waiting for your approval or was just published, so I didn't "
    "repeat it."
)


def build_social_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    fail = ctx.fail
    finish_with_reply = ctx.finish_with_reply

    async def social_action(state: GraphState) -> GraphState:
        """Prepare one exact post. Uses no AI and publishes nothing."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        if state.get("feedback"):
            return await finish_with_reply(
                state, "This post can't be revised here. Send a new command with the new text."
            )
        task = await load_task(state)
        execution_id = uuid.UUID(state["execution_id"])
        first = state.get("next_version", 1)
        command = social_from_dict(state["social"])
        async with db.sessionmaker() as s:
            saved = await s.scalar(
                select(ContentDraft).where(
                    ContentDraft.execution_id == execution_id,
                    ContentDraft.version >= first,
                    ContentDraft.is_final.is_(True),
                )
            )
            connection = await get_active_connection(s, task.workspace_id, command.platform)
            canva_ready = command.kind != "banner" or (
                await get_active_connection(s, task.workspace_id, "canva") is not None
            )

        if saved is None:
            if connection is None:
                return await fail(
                    state, _NOT_CONNECTED[command.platform], f"{command.platform} not connected"
                )
            if not canva_ready:
                return await fail(state, CANVA_NOT_CONNECTED, "canva not connected")
            try:
                prepared = await social_actions.prepare_post(
                    deps.gateway, state["user_id"], command
                )
            except social_actions.SocialStop as stop:
                return await finish_with_reply(state, stop.message)
            if not await is_current(state, TaskStatus.RUNNING):
                return {"outcome": "cancelled"}
            async with db.sessionmaker() as s:
                if await already_requested(
                    s, task.workspace_id, prepared.action_type, prepared.payload["arguments"]
                ):
                    return await finish_with_reply(state, _ALREADY_REQUESTED)
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=first, channel=command.platform, text=prepared.text,
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model="none", provider="user",
                    prompt_version="none", claims=[], quality={},
                    research={
                        "mode": "social",
                        "action_type": prepared.action_type,
                        "payload": prepared.payload,
                    },
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content=(
                        f"Your banner is in your Canva, and this {_NAMES[command.platform]} post "
                        "is ready for your approval. Nothing has been posted yet."
                        if command.kind == "banner"
                        else f"This {_NAMES[command.platform]} post is ready for your approval. "
                        "Nothing has been posted yet."
                    ),
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

    return {"social_action": social_action}
