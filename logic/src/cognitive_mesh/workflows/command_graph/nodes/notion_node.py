"""Notion agent: answer reads or prepare one exact page change."""

import uuid

from sqlalchemy import select

from cognitive_mesh.integrations import notion
from cognitive_mesh.models import Chat, ContentDraft, MessageRole, TaskStatus
from cognitive_mesh.services import audit, notion_actions
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.services.duplicate_guard import already_requested
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import NOTION_NOT_CONNECTED
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.notion_commands import notion_from_dict

_ALREADY_REQUESTED = (
    "This exact Notion change is already waiting for your approval or was just made, so I didn't "
    "repeat it."
)


def build_notion_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    fail = ctx.fail
    finish_with_reply = ctx.finish_with_reply

    async def notion_action(state: GraphState) -> GraphState:
        """Answer Notion reads, or prepare one exact change. Uses no AI and changes nothing."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        if state.get("feedback"):
            return await finish_with_reply(
                state, "This Notion change can't be revised. Send a new command to change it."
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
            connection = await get_active_connection(s, task.workspace_id, notion.TOOLKIT)

        if saved is None:
            if connection is None:
                return await fail(state, NOTION_NOT_CONNECTED, "notion not connected")
            command = notion_from_dict(state["notion"])
            try:
                if command.kind == "find":
                    answer = await notion_actions.answer_find(
                        deps.gateway, state["user_id"], command
                    )
                    return await finish_with_reply(state, answer)
                if command.kind == "read":
                    answer = await notion_actions.answer_read(
                        deps.gateway, state["user_id"], command
                    )
                    return await finish_with_reply(state, answer)
                prepared = await notion_actions.prepare_change(
                    deps.gateway, state["user_id"], command
                )
            except notion_actions.NotionStop as stop:
                return await finish_with_reply(state, stop.message)
            except notion.InvalidNotionActionError as exc:
                return await finish_with_reply(state, f"I can't prepare that change: {exc}.")
            if not await is_current(state, TaskStatus.RUNNING):
                return {"outcome": "cancelled"}
            async with db.sessionmaker() as s:
                if await already_requested(
                    s, task.workspace_id, prepared.action_type, prepared.payload["arguments"]
                ):
                    return await finish_with_reply(state, _ALREADY_REQUESTED)
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=first, channel=notion.TOOLKIT, text=prepared.text,
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model="none", provider="user",
                    prompt_version="none", claims=[], quality={},
                    research={
                        "mode": "notion",
                        "action_type": prepared.action_type,
                        "payload": prepared.payload,
                    },
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content="This Notion change is ready for your approval. Nothing has been "
                    "changed yet.",
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

    return {"notion_action": notion_action}
