"""Pause for human approval and read the decision from the database."""

import uuid

from langgraph.types import interrupt

from cognitive_mesh.integrations import (
    facebook,
    gmail,
    google_calendar,
    google_sheets,
    hubspot,
    instagram,
    linkedin,
    notion,
    salesforce,
    zoho,
)
from cognitive_mesh.integrations.actions import get_action
from cognitive_mesh.models import ApprovalStatus, Execution, Task, TaskStatus
from cognitive_mesh.services import approvals as approval_svc
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.services.errors import ConfigurationError
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import (
    CALENDAR_NOT_CONNECTED,
    FACEBOOK_NOT_CONNECTED,
    GMAIL_NOT_CONNECTED,
    HUBSPOT_NOT_CONNECTED,
    INSTAGRAM_NOT_CONNECTED,
    NOT_CONNECTED,
    NOTION_NOT_CONNECTED,
    SALESFORCE_NOT_CONNECTED,
    SHEETS_NOT_CONNECTED,
    ZOHO_NOT_CONNECTED,
)
from cognitive_mesh.workflows.command_graph.state import GraphState


def build_approval_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    load_task = ctx.load_task
    still_mine = ctx.still_mine
    fail = ctx.fail
    event = ctx.event

    async def request_approval(state: GraphState) -> GraphState:
        if deps.signer is None:
            raise ConfigurationError("APPROVAL_SIGNING_KEY is not configured")
        task = await load_task(state)
        if task.status == TaskStatus.CANCELLED or not still_mine(task, state):
            return {"outcome": "cancelled"}
        execution_id = uuid.UUID(state["execution_id"])

        async with db.sessionmaker() as s:
            latest = await approval_svc.latest_for_execution(s, execution_id)
            draft_id = uuid.UUID(state["draft_id"])
            # Same draft -> this node is re-running (resume/crash): reuse its approval
            # (or the user's visibility re-version of it). A new draft -> new approval.
            reusable = latest is not None and latest.content_draft_id == draft_id
            if reusable:
                approval = latest  # node re-run (resume/crash): never create a duplicate
            else:
                action = get_action(state.get("action_type") or linkedin.ACTION_TYPE)
                assert action is not None
                connection = await get_active_connection(s, task.workspace_id, action.toolkit)
                if connection is None:
                    message = {
                        gmail.TOOLKIT: GMAIL_NOT_CONNECTED,
                        google_calendar.TOOLKIT: CALENDAR_NOT_CONNECTED,
                        google_sheets.TOOLKIT: SHEETS_NOT_CONNECTED,
                        hubspot.TOOLKIT: HUBSPOT_NOT_CONNECTED,
                        salesforce.TOOLKIT: SALESFORCE_NOT_CONNECTED,
                        zoho.TOOLKIT: ZOHO_NOT_CONNECTED,
                        notion.TOOLKIT: NOTION_NOT_CONNECTED,
                        facebook.TOOLKIT: FACEBOOK_NOT_CONNECTED,
                        instagram.TOOLKIT: INSTAGRAM_NOT_CONNECTED,
                    }.get(action.toolkit, NOT_CONNECTED)
                    return await fail(state, message, f"{action.toolkit} disconnected")
                if action.action_type == linkedin.ACTION_TYPE:
                    visibility = (
                        latest.payload.get("visibility", linkedin.DEFAULT_VISIBILITY)
                        if latest is not None
                        else linkedin.DEFAULT_VISIBILITY
                    )  # keep the user's earlier visibility choice across revisions
                    payload = linkedin.build_post_payload(
                        author_urn=state["author_urn"],
                        commentary=state["draft_text"],
                        visibility=visibility,
                    )
                else:
                    payload = state["action_payload"]
                approval = await approval_svc.request_approval(
                    s, deps.signer,
                    task=await s.get(Task, task.id, populate_existing=True),  # type: ignore[arg-type]
                    execution=await s.get(Execution, execution_id),  # type: ignore[arg-type]
                    requested_by=task.created_by,
                    action_type=action.action_type,
                    toolkit_slug=action.toolkit,
                    tool_slug=action.tool_slug,
                    connection=connection,
                    payload=payload,
                    content_draft_id=draft_id,
                )
            approval_id = str(approval.id)
            version = approval.content_version

        # Pause here. Resumed by the runner after the user decides (or on recovery).
        interrupt({"approval_id": approval_id, "content_version": version})
        return {"approval_id": approval_id, "outcome": "awaiting"}

    async def decide(state: GraphState) -> GraphState:
        task = await load_task(state)
        if task.status == TaskStatus.CANCELLED or not still_mine(task, state):
            return {"outcome": "cancelled"}
        async with db.sessionmaker() as s:
            latest = await approval_svc.latest_for_execution(s, uuid.UUID(state["execution_id"]))
        status = latest.status if latest else None
        await event(state, "graph.resumed", "manager", {"approval_status": status})
        if status == ApprovalStatus.APPROVED:
            return {"outcome": "approved"}
        if status == ApprovalStatus.REJECTED:
            return {"outcome": "rejected"}
        if status == ApprovalStatus.CHANGES_REQUESTED:
            return {"outcome": "changes", "feedback": [latest.decision_note or "Revise the post."]}  # type: ignore[union-attr]
        if status == ApprovalStatus.INVALIDATED:
            return await fail(
                state,
                "The approval was invalidated (the connected account changed or disconnected). "
                "Reconnect if needed, then press Retry for a fresh draft.",
                f"approval invalidated: {latest.decision_note if latest else ''}",
            )
        return {"outcome": "awaiting"}  # still pending: pause again

    return {"request_approval": request_approval, "decide": decide}
