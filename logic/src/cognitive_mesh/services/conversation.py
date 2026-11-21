"""Conversation memory: continue unfinished commands and keep AI context small.

    Active command: exact structured state, resolved with plain code (0 tokens).
    Short-term:     the last 5 exchanges word for word.
    Compaction:     at 10 uncompacted exchanges, the oldest 5 merge into a rolling summary.
    Long-term:      only what the user explicitly asked to remember.

Context can inform wording, but it can never approve an action or choose a recipient.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.agents.llm import MANAGER_TIMEOUT_SECONDS
from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.db import Database
from cognitive_mesh.models import ChatMemory, Message, MessageRole, Task
from cognitive_mesh.services.errors import ConfigurationError
from cognitive_mesh.services.long_term_memory import LongTermMemory
from cognitive_mesh.workflows.calendar_commands import (
    apply_guest_contact,
    calendar_from_dict,
    continue_calendar,
    parse_calendar_mark,
)
from cognitive_mesh.workflows.command_router import (
    Route,
    bare_platform,
    find_platforms,
    needs_platform,
    route_command,
    with_platform,
)
from cognitive_mesh.workflows.email_commands import (
    apply_contact,
    continue_email,
    email_from_dict,
)
from cognitive_mesh.workflows.manager_routing import route_from_decision
from cognitive_mesh.workflows.memory_commands import MemoryCommand, memory_from_dict
from cognitive_mesh.workflows.sheet_commands import continue_sheet, sheet_from_dict

logger = logging.getLogger(__name__)

KEEP_EXCHANGES = 5
COMPACT_AT_EXCHANGES = 10
MAX_CONTEXT_MESSAGE_CHARS = 500
MAX_CONTEXT_CHARS = 4000
MAX_TRANSCRIPT_MESSAGE_CHARS = 800
_CANCEL_WORDS = {"cancel", "stop", "never mind", "nevermind", "forget it"}
_NEW_COMMAND_ACTIONS = {"post_as_is", "write", "research", "gmail", "calendar", "sheets"}
CANCELLED_REPLY = "Okay, I dropped that command. Nothing was sent or changed."
ManagerDecide = Callable[[str], Awaitable[ManagerDecision]]
_CALENDAR_LABELS = {
    "list": "Calendar agent · Read events",
    "free": "Calendar agent · Find free time",
    "create": "Calendar agent · Schedule event",
    "reschedule": "Calendar agent · Reschedule event",
    "cancel": "Calendar agent · Cancel event",
}
_SHEET_LABELS = {
    "list": "Sheets agent · List sheets",
    "read": "Sheets agent · Read sheet",
    "create": "Sheets agent · Create sheet",
    "append": "Sheets agent · Add rows",
    "update": "Sheets agent · Update cell",
}
_EMAIL_LABELS = {
    "send": "Email agent · Send exact email",
    "write": "Email agent · Write email",
    "forward": "Email agent · Forward email",
    "trash": "Email agent · Move to Trash",
}
_POST_LABELS = {
    "post_as_is": "LinkedIn agent · Post your text",
    "write": "Content agent · Write LinkedIn post",
    "research": "Research and Content agents · LinkedIn post",
    "memory": "Memory",
}


@dataclass(frozen=True)
class Resolution:
    command: str
    route: Route
    reply: str | None = None
    routed_by: str = "rules"  # "rules" (no tokens) or "manager" (one small model call)


@dataclass(frozen=True)
class ConversationContext:
    text: str = ""  # reference context for the writer
    trusted_text: str = ""  # only the user's own words and saved facts


def _route_to_dict(
    command: str, route: Route, reply: str | None = None, routed_by: str = "rules"
) -> dict[str, Any]:
    return {
        "routed_by": routed_by,
        "command": command,
        "action": route.action,
        "platforms": list(route.platforms),
        "text": route.text,
        "email": asdict(route.email) if route.email else None,
        "memory": asdict(route.memory) if route.memory else None,
        "calendar": asdict(route.calendar) if route.calendar else None,
        "sheet": asdict(route.sheet) if route.sheet else None,
        "reply": reply,
    }


def _resolution_from_dict(data: dict[str, Any]) -> Resolution:
    route = Route(
        action=data["action"],
        platforms=tuple(data["platforms"]),
        text=data["text"],
        email=email_from_dict(data["email"]) if data["email"] else None,
        memory=memory_from_dict(data["memory"]) if data["memory"] else None,
        calendar=calendar_from_dict(data["calendar"]) if data.get("calendar") else None,
        sheet=sheet_from_dict(data["sheet"]) if data.get("sheet") else None,
    )
    return Resolution(
        data["command"], route, data.get("reply"), data.get("routed_by", "rules")
    )


def route_label(data: dict[str, Any] | None) -> str | None:
    """Which specialist handled a task, for the task card."""
    if not data:
        return None
    action = data.get("action")
    if action == "calendar" and data.get("calendar"):
        return _CALENDAR_LABELS.get(data["calendar"]["kind"])
    if action == "sheets" and data.get("sheet"):
        return _SHEET_LABELS.get(data["sheet"]["kind"])
    if action == "gmail" and data.get("email"):
        return _EMAIL_LABELS.get(data["email"]["kind"])
    return _POST_LABELS.get(str(action))


def resolution_label(resolution: Resolution) -> str | None:
    return route_label(_route_to_dict(resolution.command, resolution.route))


def _pending_for(command: str, route: Route) -> dict[str, Any] | None:
    if route.action == "gmail":
        return _route_to_dict(command, route) if route.email and route.email.missing else None
    if route.action == "calendar":
        waiting = route.calendar and route.calendar.missing
        return _route_to_dict(command, route) if waiting else None
    if route.action == "sheets":
        waiting = route.sheet and route.sheet.missing
        return _route_to_dict(command, route) if waiting else None
    missing_text = route.action == "post_as_is" and not route.text
    return _route_to_dict(command, route) if missing_text or needs_platform(route) else None


def _continue(pending: dict[str, Any], reply: str) -> Resolution:
    earlier = _resolution_from_dict(pending)
    route = earlier.route
    if route.action == "gmail" and route.email:
        return replace(earlier, route=replace(route, email=continue_email(route.email, reply)))
    if route.action == "calendar" and route.calendar:
        updated = continue_calendar(route.calendar, reply)
        return replace(earlier, route=replace(route, calendar=updated))
    if route.action == "sheets" and route.sheet:
        return replace(earlier, route=replace(route, sheet=continue_sheet(route.sheet, reply)))
    if route.action == "post_as_is" and not route.text:
        route = replace(route, text=reply.strip())
    elif not route.platforms:
        platform = bare_platform(reply)
        if platform is None and len(found := find_platforms(reply)) == 1:
            platform = found[0]
        if platform:
            route = with_platform(route, platform)
    return replace(earlier, route=route)


async def _memory_row(session: AsyncSession, task: Task) -> ChatMemory:
    row = await session.scalar(
        select(ChatMemory)
        .where(ChatMemory.chat_id == task.chat_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = ChatMemory(workspace_id=task.workspace_id, chat_id=task.chat_id, pending=None)
        session.add(row)
        await session.flush()
    return row


def _same_command(pending: dict[str, Any], resolution: Resolution) -> bool:
    """True when a reply did not fill the missing detail."""
    current = _route_to_dict(resolution.command, resolution.route)
    return json.loads(json.dumps(current)) == {**pending, "routed_by": current["routed_by"]}


async def _ask_manager(raw: str, decide: ManagerDecide | None) -> Resolution | None:
    """One small model call, only when plain rules can't understand the message."""
    try:
        if decide is None:
            raise ConfigurationError("No manager is available")
        decision = await asyncio.wait_for(decide(raw), MANAGER_TIMEOUT_SECONDS + 2)
        route, question = route_from_decision(decision, raw)
    except Exception:
        logger.warning("Manager routing unavailable; using offline rules", exc_info=True)
        marked = parse_calendar_mark(raw)
        if marked is None:
            return None
        return Resolution(
            raw, Route("calendar", ("googlecalendar",), calendar=marked), routed_by="rules"
        )
    return Resolution(raw, route, question, routed_by="manager")


async def resolve(
    session: AsyncSession,
    task: Task,
    memory: LongTermMemory,
    decide: ManagerDecide | None = None,
) -> Resolution:
    """What this message means in its chat. Stored on the task, so retries stay exact."""
    if task.resolved_route:
        return _resolution_from_dict(task.resolved_route)

    raw = task.request
    fresh = route_command(raw)
    row = await _memory_row(session, task)
    pending = row.pending
    resolution = Resolution(raw, fresh)

    if fresh.action != "memory":
        if pending and raw.strip().lower().rstrip(".!") in _CANCEL_WORDS:
            resolution = Resolution(raw, Route("unsupported"), CANCELLED_REPLY)
            row.pending = None
        else:
            unclear = fresh.action == "unsupported" or (
                fresh.action == "model" and not fresh.platforms
            )
            stuck = False
            if pending and fresh.action not in _NEW_COMMAND_ACTIONS:
                resolution = _continue(pending, raw)
                stuck = _same_command(pending, resolution)
            if stuck or (unclear and not pending):
                await session.commit()  # never hold a database write open during a model call
                routed = await _ask_manager(raw, decide)
                actionable = routed is not None and routed.route.action != "unsupported"
                if routed is not None and (actionable or not pending):
                    resolution = routed
                row = await _memory_row(session, task)
            route = resolution.route
            if route.action == "gmail" and route.email and route.email.missing == "recipient":
                contacts = await memory.contacts(task.workspace_id)
                email = apply_contact(route.email, raw, contacts)
                resolution = replace(resolution, route=replace(route, email=email))
            elif route.action == "calendar" and route.calendar and route.calendar.guest_name:
                contacts = await memory.contacts(task.workspace_id)
                calendar = apply_guest_contact(route.calendar, contacts)
                resolution = replace(resolution, route=replace(route, calendar=calendar))
            row.pending = _pending_for(resolution.command, resolution.route)

    await session.execute(
        update(Task)
        .where(Task.id == task.id)
        .values(
            resolved_route=_route_to_dict(
                resolution.command, resolution.route, resolution.reply, resolution.routed_by
            )
        )
    )
    await session.commit()
    return resolution


async def run_memory_command(
    memory: LongTermMemory, workspace_id: uuid.UUID, command: MemoryCommand
) -> str:
    if command.error:
        return command.error
    if command.op == "remember_contact":
        await memory.remember_contact(workspace_id, command.name or "", command.email or "")
        return f"Saved {command.name} as {command.email}. You can review it on the Memory page."
    if command.op == "remember_fact":
        await memory.remember_fact(workspace_id, command.text or "")
        return "Saved to your memory. You can review or delete it on the Memory page."
    if command.op == "forget":
        deleted, matched = await memory.forget(workspace_id, command.text or "")
        if deleted:
            return "Done, I removed that from your memory."
        if matched:
            return "More than one memory matches. Delete the right one on the Memory page."
        return "I couldn't find that in your memory."
    items = await memory.list_items(workspace_id)
    if not items:
        return "I don't have anything saved yet. Say: remember that ..."
    return "Here's what I remember:\n" + "\n".join(f"- {i.text}" for i in items[:20])


async def _uncompacted(session: AsyncSession, chat_id: uuid.UUID, offset: int) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at, Message.id)
        .offset(offset)
    )
    return list(rows)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


async def context_for(
    session: AsyncSession, task: Task, memory: LongTermMemory, query: str
) -> ConversationContext:
    """A size-limited context for AI work. Rule-based paths never call this."""
    row = await session.scalar(select(ChatMemory).where(ChatMemory.chat_id == task.chat_id))
    summary = row.summary if row else ""
    messages = await _uncompacted(session, task.chat_id, row.compacted_count if row else 0)
    earlier = [m for m in messages if m.task_id != task.id]
    user_positions = [i for i, m in enumerate(earlier) if m.role == MessageRole.USER]
    if len(user_positions) > KEEP_EXCHANGES:
        earlier = earlier[user_positions[-KEEP_EXCHANGES] :]
    facts = await memory.relevant_facts(task.workspace_id, query)

    sections = []
    if facts:
        sections.append("Saved user preferences and facts:\n" + "\n".join(f"- {f}" for f in facts))
    if summary:
        sections.append(f"Earlier conversation summary:\n{summary}")
    if earlier:
        turns = "\n".join(
            f"{m.role}: {_clip(m.content, MAX_CONTEXT_MESSAGE_CHARS)}" for m in earlier
        )
        sections.append(f"Recent conversation:\n{turns}")
    user_words = [m.content for m in earlier if m.role == MessageRole.USER]
    return ConversationContext(
        text=_clip("\n\n".join(sections), MAX_CONTEXT_CHARS),
        trusted_text=" ".join([*facts, *user_words]),
    )


async def compact_if_due(
    db: Database, chat_id: uuid.UUID, summarize: Callable[[str, str], Awaitable[str]]
) -> bool:
    """One model call per 5 exchanges. Original messages are never changed."""
    async with db.sessionmaker() as session:
        row = await session.scalar(select(ChatMemory).where(ChatMemory.chat_id == chat_id))
        if row is None:
            return False
        offset, previous = row.compacted_count, row.summary
        messages = await _uncompacted(session, chat_id, offset)
    user_positions = [i for i, m in enumerate(messages) if m.role == MessageRole.USER]
    if len(user_positions) < COMPACT_AT_EXCHANGES:
        return False
    cut = user_positions[KEEP_EXCHANGES]
    transcript = "\n".join(
        f"{m.role}: {_clip(m.content, MAX_TRANSCRIPT_MESSAGE_CHARS)}" for m in messages[:cut]
    )
    summary = await summarize(previous, transcript)
    async with db.sessionmaker() as session:
        result = await session.execute(
            update(ChatMemory)
            .where(ChatMemory.chat_id == chat_id, ChatMemory.compacted_count == offset)
            .values(summary=summary, compacted_count=offset + cut)
        )
        await session.commit()
    return result.rowcount == 1
