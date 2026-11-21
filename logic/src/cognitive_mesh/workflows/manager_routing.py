"""Turn the Manager agent's decision into a validated route.

The model chooses the specialist. Code stays in charge of what can cause harm:
- Times, guests and email recipients are read from the user's own words.
- A date phrase must be grounded in the message, so an invented date is discarded.
- The model can never supply exact text to publish, and every change still needs approval.
"""

import difflib
import re

from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.integrations.google_sheets import CELL
from cognitive_mesh.workflows.calendar_commands import (
    CalendarCommand,
    finalize_calendar,
    guests_in,
    wants_meet,
)
from cognitive_mesh.workflows.calendar_time import find_date, find_duration, find_times
from cognitive_mesh.workflows.command_router import Route, find_platforms
from cognitive_mesh.workflows.email_commands import email_from_text
from cognitive_mesh.workflows.sheet_commands import SheetCommand, finalize_sheet

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


def _calendar_route(decision: ManagerDecision, message: str) -> Route | None:
    target_date = find_date(message) or grounded_date(decision.date_phrase, message)
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


def _sheet_route(decision: ManagerDecision, message: str) -> Route | None:
    """Sheet names and cell values must appear in the user's message, so none can be invented."""
    name = " ".join(decision.sheet_name.split())[:200] or None
    url = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,128})", message)
    target = {"spreadsheet_id": url.group(1)} if url else {"name": name}
    if decision.action == "sheet_list":
        command = SheetCommand("list")
    elif decision.action == "sheet_create":
        columns = tuple(c.strip()[:200] for c in decision.columns if c.strip())
        command = SheetCommand("create", name=name, rows=(columns,) if columns else ())
    else:
        if name and not url and not _in_message(name, message):
            target = {"name": None}
        if decision.action == "sheet_read":
            command = SheetCommand("read", **target)
        elif decision.action == "sheet_append":
            rows = tuple(
                tuple(cell.strip() for cell in row)
                for row in decision.rows
                if row and all(_in_message(cell, message) for cell in row)
            )
            if len(rows) != len([row for row in decision.rows if row]):
                rows = ()  # one ungrounded value discards all of them, and the user is asked
            command = SheetCommand("append", rows=rows, **target)
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


def route_from_decision(decision: ManagerDecision, message: str) -> tuple[Route, str | None]:
    """(route, optional question for the user). Invalid combinations are treated as unsupported."""
    if decision.target == "clarify" and decision.question.strip():
        return Route("unsupported"), " ".join(decision.question.split())
    if decision.target == "sheets":
        route = _sheet_route(decision, message)
        if route is not None:
            return route, None
    if decision.target == "calendar":
        route = _calendar_route(decision, message)
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
