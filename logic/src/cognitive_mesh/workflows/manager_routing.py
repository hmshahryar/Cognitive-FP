"""Turn the Manager agent's decision into a validated route.

The model chooses the specialist. Code stays in charge of what can cause harm:
- Times, guests and email recipients are read from the user's own words.
- A date phrase must be grounded in the message, so an invented date is discarded.
- The model can never supply exact text to publish, and every change still needs approval.
"""

import difflib
import re
from typing import Any

from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.integrations.google_sheets import CELL
from cognitive_mesh.services.active_items import ActiveItems
from cognitive_mesh.workflows.calendar_commands import (
    CalendarCommand,
    finalize_calendar,
    guests_in,
    wants_meet,
)
from cognitive_mesh.workflows.calendar_time import find_date, find_duration, find_times
from cognitive_mesh.workflows.command_router import Route, find_platforms
from cognitive_mesh.workflows.email_commands import email_from_text
from cognitive_mesh.workflows.sheet_commands import (
    DEFAULT_SHEET_NAME,
    SheetCommand,
    finalize_sheet,
)

_WORD = re.compile(r"[a-z]+")
_NUMBER = re.compile(r"\d+")
_MIN_WORD_MATCH = 0.7
_GROUNDING_IGNORED = {"of", "the", "on"}


def grounded_date(phrase: str, message: str) -> str | None:
    """Keep a model-corrected date only if it matches what the user actually wrote."""
    candidate = find_date(phrase or "")
    if not candidate:
        return None
    lowered = message.lower()
    if not set(_NUMBER.findall(candidate)) <= set(_NUMBER.findall(lowered)):
        return None
    written = _WORD.findall(lowered)
    words = [w for w in _WORD.findall(re.sub(r"\d+(?:st|nd|rd|th)?", " ", candidate))]
    for word in words:
        if word in _GROUNDING_IGNORED:
            continue
        if not difflib.get_close_matches(word, written, n=1, cutoff=_MIN_WORD_MATCH):
            return None
    return candidate


def _calendar_route(
    decision: ManagerDecision, message: str, active: ActiveItems
) -> Route | None:
    target_date = find_date(message) or grounded_date(decision.date_phrase, message)
    active_event = active.event if decision.use_active_item else None
    if decision.action in ("list", "free"):
        command = CalendarCommand(decision.action, date_text=target_date or "today")
    elif decision.action == "create":
        start, end = find_times(message)
        title = " ".join(decision.title.split())[:200] or "Calendar event"
        command = finalize_calendar(
            CalendarCommand(
                "create",
                title=title,
                attendees=guests_in(message),
                date_text=target_date,
                start=start,
                end=end,
                duration_minutes=find_duration(message),
                meet=wants_meet(message),
                all_day=decision.all_day and not start,
            )
        )
    elif decision.action in ("reschedule", "cancel"):
        source = grounded_date(decision.source_date_phrase, message)
        query = " ".join(decision.event_query.split()) or None
        if active_event is not None and query is None:
            query, source = active_event.title, source or active_event.day
        if decision.action == "cancel":
            command = finalize_calendar(
                CalendarCommand("cancel", query=query, source_date=source or target_date)
            )
        else:
            parts = re.split(r"\bto\b", message, flags=re.IGNORECASE)
            when = parts[-1] if len(parts) > 1 else ""
            start, end = find_times(when)
            command = finalize_calendar(
                CalendarCommand(
                    "reschedule",
                    query=query,
                    source_date=source,
                    date_text=grounded_date(decision.date_phrase, message),
                    start=start,
                    end=end,
                    duration_minutes=find_duration(when),
                )
            )
    else:
        return None
    return Route("calendar", ("googlecalendar",), calendar=command)


def _in_message(value: str, message: str) -> bool:
    return " ".join(value.split()).casefold() in " ".join(message.split()).casefold()


def _grounded_rows(decision: ManagerDecision, message: str) -> tuple[tuple[str, ...], ...]:
    """One value missing from the user's message discards all of them, and the user is asked."""
    wanted = [row for row in decision.rows if row]
    rows = tuple(
        tuple(cell.strip() for cell in row)
        for row in wanted
        if all(cell.strip() and _in_message(cell, message) for cell in row)
    )
    return rows if len(rows) == len(wanted) else ()


def _sheet_route(decision: ManagerDecision, message: str, active: ActiveItems) -> Route | None:
    """Sheet names and cell values must appear in the user's message, so none can be invented."""
    name = " ".join(decision.sheet_name.split())[:200] or None
    url = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,128})", message)
    target: dict[str, Any] = {"spreadsheet_id": url.group(1)} if url else {"name": name}
    if decision.action == "sheet_list":
        command = SheetCommand("list")
    elif decision.action == "sheet_create":
        columns = tuple(c.strip()[:200] for c in decision.columns if c.strip())
        values = _grounded_rows(decision, message)
        rows = ((columns,) if columns else ()) + values
        command = SheetCommand(
            "create", name=name or (DEFAULT_SHEET_NAME if values else None), rows=rows
        )
    else:
        if name and not url and not _in_message(name, message):
            target = {"name": None}
        if decision.use_active_item and active.sheet is not None and not url:
            target = {"use_last": True}  # code confirmed this chat really has an active sheet
        if decision.action == "sheet_read":
            command = SheetCommand("read", **target)
        elif decision.action == "sheet_append":
            command = SheetCommand("append", rows=_grounded_rows(decision, message), **target)
        elif decision.action == "sheet_update":
            cell = decision.cell.strip().upper()
            valid_cell = bool(CELL.match(cell)) and _in_message(cell, message)
            has_value = bool(decision.value) and _in_message(decision.value, message)
            command = SheetCommand(
                "update",
                cell=cell if valid_cell else None,
                value=decision.value.strip() if has_value else None,
                **target,
            )
        else:
            return None
    return Route("sheets", ("googlesheets",), sheet=finalize_sheet(command))


def route_from_decision(
    decision: ManagerDecision, message: str, active: ActiveItems | None = None
) -> tuple[Route, str | None]:
    """(route, optional question for the user). Invalid combinations are treated as unsupported."""
    items = active or ActiveItems()
    if decision.target == "clarify" and decision.question.strip():
        return Route("unsupported"), " ".join(decision.question.split())
    if decision.target == "sheets":
        route = _sheet_route(decision, message, items)
        if route is not None:
            return route, None
    if decision.target == "calendar":
        route = _calendar_route(decision, message, items)
        if route is not None:
            return route, None
    if decision.target == "gmail":
        kinds = {"write_email": "write", "forward_email": "forward", "trash_email": "trash"}
        if decision.action in kinds:
            email = email_from_text(kinds[decision.action], message)
            return Route("gmail", ("gmail",), email=email), None
    if decision.target == "linkedin":
        actions = {"write_post": "write", "research_post": "research"}
        if decision.action in actions:
            return Route(actions[decision.action], find_platforms(message)), None  # type: ignore[arg-type]
    return Route("unsupported"), None
