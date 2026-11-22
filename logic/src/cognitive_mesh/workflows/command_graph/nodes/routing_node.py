"""Understand the command and choose the path through the workflow."""

from dataclasses import asdict
from typing import Any

from cognitive_mesh.agents.schemas import CommandIntent, ManagerDecision
from cognitive_mesh.integrations import linkedin
from cognitive_mesh.integrations.gateway import IntegrationUnavailableError, ToolExecutionError
from cognitive_mesh.models import Task, TaskStatus
from cognitive_mesh.services import conversation
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import NO_PROFILE_ID, NOT_CONNECTED
from cognitive_mesh.workflows.command_graph.state import NO_RESEARCH, GraphState
from cognitive_mesh.workflows.command_router import blocking_reply


def profile_from_info(data: Any) -> tuple[str, str | None]:
    """(profile text for the writer, LinkedIn person id) from LINKEDIN_GET_MY_INFO."""
    if not isinstance(data, dict):
        return "unknown", None
    name = " ".join(p for p in (data.get("localizedFirstName"), data.get("localizedLastName")) if p)
    headline = data.get("localizedHeadline")
    parts = [f"Name: {name}" if name else "", f"Headline: {headline}" if headline else ""]
    return "; ".join(p for p in parts if p) or "unknown", data.get("id")


def build_routing_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    load_task = ctx.load_task
    finish_with_reply = ctx.finish_with_reply
    event = ctx.event
    fail = ctx.fail

    async def prepare(state: GraphState) -> GraphState:
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        task = await load_task(state)

        async def decide_route(message: str, context: str) -> ManagerDecision:
            return await deps.build_pipeline(state["user_id"]).decide_route(message, context)

        async with db.sessionmaker() as s:
            current = await s.get(Task, task.id, populate_existing=True)
            assert current is not None
            resolved = await conversation.resolve(s, current, deps.memory, decide_route)
        command, route = resolved.command, resolved.route
        if resolved.reply:
            return await finish_with_reply(state, resolved.reply)
        if route.action == "memory":
            assert route.memory is not None
            return await finish_with_reply(
                state,
                await conversation.run_memory_command(deps.memory, task.workspace_id, route.memory),
            )
        reply = blocking_reply(route)
        if reply:
            return await finish_with_reply(state, reply)
        await event(
            state, "route.selected", "manager",
            {
                "mode": route.action,
                "platform": route.platform,
                "routed_by": resolved.routed_by,
                "specialist": conversation.resolution_label(resolved),
            },
        )
        if route.action == "gmail":
            assert route.email is not None
            return {
                "command": command, "mode": "gmail", "email": asdict(route.email),
                "feedback": [], "next_version": 1, "outcome": "",
            }
        if route.action == "calendar":
            assert route.calendar is not None
            return {
                "command": command, "mode": "calendar", "calendar": asdict(route.calendar),
                "feedback": [], "next_version": 1, "outcome": "",
            }
        if route.action == "sheets":
            assert route.sheet is not None
            return {
                "command": command, "mode": "sheets", "sheet": asdict(route.sheet),
                "feedback": [], "next_version": 1, "outcome": "",
            }
        async with db.sessionmaker() as s:
            connection = await get_active_connection(s, task.workspace_id, linkedin.TOOLKIT)
        if connection is None:
            return await fail(state, NOT_CONNECTED, "linkedin not connected")
        # Social Media employee: read-only profile lookup (niche + author id).
        try:
            info = await deps.gateway.execute_read_only(
                user_id=state["user_id"], toolkit_slug=linkedin.TOOLKIT,
                tool_slug="LINKEDIN_GET_MY_INFO", arguments={},
            )
        except (IntegrationUnavailableError, ToolExecutionError):
            info = None  # handled below: no person id -> clear failure message
        profile, person_id = profile_from_info(info)
        if not person_id:
            return await fail(state, NO_PROFILE_ID, "LINKEDIN_GET_MY_INFO returned no id")
        return {
            "command": command,
            "mode": route.action,
            "provided_text": route.text or "",
            "intent": CommandIntent(action="linkedin_post", topic=command).model_dump(),
            "findings": dict(NO_RESEARCH),
            "sources": [],
            "feedback": [],
            "next_version": 1,
            "profile": profile,
            "author_urn": linkedin.person_urn(person_id),
            "outcome": "",
        }

    return {"prepare": prepare}
