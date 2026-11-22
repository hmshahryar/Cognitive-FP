"""What this chat is currently working on, read from completed, approved actions.

Follow-ups such as "add it there" resolve here. The execution records are the source of truth,
so an item appears only after its action really succeeded, and only inside its own workspace.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.integrations import gmail, google_calendar, google_sheets
from cognitive_mesh.models import Approval, Publication, PublicationStatus, Task

_SHEET_TOOLS = {google_sheets.CREATE_TOOL, google_sheets.APPEND_TOOL, google_sheets.UPDATE_TOOL}
_EVENT_TOOLS = {
    google_calendar.CREATE_TOOL, google_calendar.PATCH_TOOL, google_calendar.DELETE_TOOL,
}
_EMAIL_TOOLS = {gmail.SEND_TOOL}
_RECENT_ACTIONS = 50


@dataclass(frozen=True)
class ActiveSheet:
    id: str
    name: str


@dataclass(frozen=True)
class ActiveEvent:
    title: str
    day: str | None


@dataclass(frozen=True)
class ActiveItems:
    sheet: ActiveSheet | None = None
    event: ActiveEvent | None = None
    email_subject: str | None = None

    def describe(self) -> str:
        lines = []
        if self.sheet:
            lines.append(f"- Active sheet: {self.sheet.name}")
        if self.event:
            day = f" on {self.event.day}" if self.event.day else ""
            lines.append(f"- Active calendar event: {self.event.title}{day}")
        if self.email_subject:
            lines.append(f"- Last sent email subject: {self.email_subject}")
        return "\n".join(lines)


def _sheet(external_id: str | None, payload: dict[str, Any]) -> ActiveSheet | None:
    target = payload.get("spreadsheet")
    name = target.get("name") if isinstance(target, dict) else None
    if not external_id or not isinstance(name, str) or not name.strip():
        return None
    return ActiveSheet(external_id, name)


def _event(payload: dict[str, Any]) -> ActiveEvent | None:
    event = payload.get("event")
    title = event.get("title") if isinstance(event, dict) else None
    if not isinstance(title, str) or not title.strip():
        return None
    start = event.get("start")
    return ActiveEvent(title, start[:10] if isinstance(start, str) else None)


async def load_active_items(
    session: AsyncSession, workspace_id: uuid.UUID, chat_id: uuid.UUID
) -> ActiveItems:
    rows = await session.execute(
        select(Publication.tool_slug, Publication.external_id, Approval.payload)
        .join(Task, Task.id == Publication.task_id)
        .join(Approval, Approval.id == Publication.approval_id)
        .where(
            Publication.workspace_id == workspace_id,
            Task.workspace_id == workspace_id,
            Task.chat_id == chat_id,
            Publication.status == PublicationStatus.PUBLISHED,
        )
        .order_by(Publication.created_at.desc(), Publication.id.desc())
        .limit(_RECENT_ACTIONS)
    )
    sheet: ActiveSheet | None = None
    event: ActiveEvent | None = None
    subject: str | None = None
    seen: set[str] = set()
    for tool, external_id, payload in rows:
        if tool in _SHEET_TOOLS and "sheet" not in seen:
            seen.add("sheet")
            sheet = _sheet(external_id, payload)
        elif tool in _EVENT_TOOLS and "event" not in seen:
            seen.add("event")  # the newest event action wins, so a cancelled event is not active
            event = None if tool == google_calendar.DELETE_TOOL else _event(payload)
        elif tool in _EMAIL_TOOLS and "email" not in seen:
            seen.add("email")
            value = (payload.get("arguments") or {}).get("subject")
            subject = value if isinstance(value, str) and value.strip() else None
    return ActiveItems(sheet=sheet, event=event, email_subject=subject)
