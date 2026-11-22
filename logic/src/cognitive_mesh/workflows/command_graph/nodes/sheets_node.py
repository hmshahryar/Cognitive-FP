"""Answer Google Sheets reads or prepare one exact change."""

import uuid

from sqlalchemy import select

from cognitive_mesh.integrations import google_sheets
from cognitive_mesh.models import Chat, ContentDraft, MessageRole, TaskStatus
from cognitive_mesh.services import audit, sheet_actions
from cognitive_mesh.services.active_items import load_active_items
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import SHEETS_NOT_CONNECTED
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.sheet_commands import sheet_from_dict


def build_sheets_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    fail = ctx.fail
    finish_with_reply = ctx.finish_with_reply

    async def sheets_action(state: GraphState) -> GraphState:
        """Answer Sheets reads, or prepare one exact change. Uses no AI and changes nothing."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        if state.get("feedback"):
            return await finish_with_reply(
                state, "This sheet action can't be revised. Send a new command to change it."
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
            connection = await get_active_connection(s, task.workspace_id, google_sheets.TOOLKIT)
            active = (await load_active_items(s, task.workspace_id, task.chat_id)).sheet
        if connection is None:
            return await fail(state, SHEETS_NOT_CONNECTED, "google sheets not connected")

        if saved is None:
            command = sheet_from_dict(state["sheet"])
            try:
                if command.kind in ("list", "read"):
                    answer = await sheet_actions.answer_read(
                        deps.gateway, state["user_id"], command, active
                    )
                    return await finish_with_reply(state, answer)
                prepared = await sheet_actions.prepare_change(
                    deps.gateway, state["user_id"], command, active
                )
            except sheet_actions.SheetStop as stop:
                return await finish_with_reply(state, stop.message)
            except google_sheets.InvalidSheetActionError as exc:
                return await finish_with_reply(state, f"I can't prepare that change: {exc}.")
            if not await is_current(state, TaskStatus.RUNNING):
                return {"outcome": "cancelled"}
            async with db.sessionmaker() as s:
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=first, channel="googlesheets", text=prepared.text,
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model="none", provider="user",
                    prompt_version="none", claims=[], quality={},
                    research={
                        "mode": "sheets",
                        "action_type": prepared.action_type,
                        "payload": prepared.payload,
                    },
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content="This sheet change is ready for your approval. Nothing has been "
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

    return {"sheets_action": sheets_action}
