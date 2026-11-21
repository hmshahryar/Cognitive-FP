"""Understand calendar commands with plain code.

Guests come only from addresses in the user's instruction or contacts they saved. Dates and
times are never guessed: a missing detail becomes one question, and the reply continues the
same command.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from cognitive_mesh.integrations.gmail import ADDRESS
from cognitive_mesh.workflows.calendar_time import (
    find_date,
    find_duration,
    find_times,
    strip_when,
)

Kind = Literal["list", "free", "create", "reschedule", "cancel"]
Missing = Literal["date", "time", "event", "when"]

_NOUN = r"(?:meeting|event|appointment|call|interview|session|catch[- ]?up)"
_ADDRESS = re.compile(ADDRESS)
_ADDRESS_LIST = rf"{ADDRESS}(?:\s*(?:,|;|&|\band\b)\s*{ADDRESS})*"
_FREE = re.compile(
    r"^\s*(?:please\s+)?(?:when\s+am\s+i\s+free|am\s+i\s+free|find\s+(?:me\s+)?(?:some\s+|a\s+)?"
    r"free\s+(?:time|slots?)|(?:show|list)\s+(?:my\s+)?free\s+(?:time|slots?)|free\s+slots?)\b",
    re.IGNORECASE,
)
_LIST = re.compile(
    r"^\s*(?:please\s+)?(?:what(?:'s|’s|\s+is|\s+do\s+i\s+have)|show|list|see|check|read|"
    r"do\s+i\s+have|any)\b.{0,60}\b(?:calend[ae]r|schedule|agenda|meetings?|events?|appointments?)\b",
    re.IGNORECASE | re.DOTALL,
)
_CREATE = re.compile(
    r"^\s*(?:please\s+)?(?:schedule|book|create|add|set\s+up|arrange|organi[sz]e)\s+"
    rf"(?:(?:an?|my|the|new)\s+)*(?P<label>(?:[\w'-]+\s+){{0,3}}?)(?P<noun>{_NOUN})\b",
    re.IGNORECASE,
)
_ADD_TO = re.compile(
    r"^\s*(?:please\s+)?(?:add|put)\s+(?P<title>.+?)\s+(?:to|on|in)\s+(?:my\s+)?(?:google\s+)?"
    r"calend[ae]r\b",
    re.IGNORECASE,
)
_RESCHEDULE = re.compile(
    r"^\s*(?:please\s+)?(?P<verb>reschedule|move|shift|change)\s+(?P<rest>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_CANCEL = re.compile(
    rf"^\s*(?:please\s+)?(?:cancel|delete|remove)\s+(?P<what>.*\b{_NOUN}\b.*)$",
    re.IGNORECASE | re.DOTALL,
)
_CANCEL_FROM_CALENDAR = re.compile(
    r"^\s*(?:please\s+)?(?:cancel|delete|remove)\s+(?P<what>.+?)\s+from\s+(?:my\s+)?calend[ae]r\b",
    re.IGNORECASE | re.DOTALL,
)
_NOT_A_LABEL = {"post", "posts", "email", "emails", "article", "caption", "about", "for", "on", "to"}
_TO_WORD = re.compile(r"\bto\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“]([^\"”]{1,200})[\"”]")
_STOP = (
    r"(?=\s+(?:on|at|from|for\s+\d|for\s+an?\b|for\s+half|today|tonight|tomorrow|next|this|with|"
    r"invite|location:|using|via)\b|[.,;]|$)"
)
_CALLED = re.compile(rf"\b(?:called|titled|named)\s+(.+?){_STOP}", re.IGNORECASE)
_ABOUT = re.compile(rf"\b(?:about|regarding|to\s+discuss)\s+(.+?){_STOP}", re.IGNORECASE)
_GUESTS = re.compile(
    rf"\b(?:with|invite|inviting|include|including)\s+({_ADDRESS_LIST})", re.IGNORECASE
)
_GUEST_NAME = re.compile(
    r"\bwith\s+(?:my\s+|our\s+)?([A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2}?)"
    r"(?=\s+(?:on|at|from|for|today|tonight|tomorrow|next|this|about|regarding|to|using|via)\b"
    r"|[.,;]|$)",
    re.IGNORECASE,
)
_LOCATION = re.compile(r"\blocation:\s*(.+?)(?=\s+\w+:|$)", re.IGNORECASE | re.DOTALL)
_MEET = re.compile(r"\b(?:google\s+meet|meet\s+link|video\s+(?:call|meeting)|online)\b", re.IGNORECASE)
_FILLER = re.compile(
    rf"\b(?:my|the|this|that|our|an?|with|on|at|from|in|for|{_NOUN}|calend[ae]r)\b", re.IGNORECASE
)

_CALENDAR_WORD = re.compile(r"\b(?:calend[ae]r|schedule)\b", re.IGNORECASE)
_MARK_WORD = re.compile(r"\b(?:mark|block|note|save|put|add|set)\b", re.IGNORECASE)
_MARK_AS = re.compile(r"\bas\s+(?:the\s+|a\s+|an\s+|my\s+)?(.+?)\s*$", re.IGNORECASE | re.DOTALL)

_QUESTIONS: dict[str, str] = {
    "date": "What day should I schedule it? For example: tomorrow, Friday or 25 November.",
    "time": "What time should it start? Include AM or PM, for example 3pm or 15:00.",
    "event": "Which event do you mean? Reply with its title, or the day it is on.",
    "when": "What new day or time should it move to? For example: Friday at 3pm.",
}


@dataclass(frozen=True)
class CalendarCommand:
    kind: Kind
    title: str | None = None
    attendees: tuple[str, ...] = ()
    guest_name: str | None = None  # a name to look up in saved contacts
    date_text: str | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: int | None = None
    query: str | None = None
    source_date: str | None = None
    meet: bool = False
    location: str | None = None
    all_day: bool = False
    error: str | None = None
    missing: Missing | None = None


def calendar_from_dict(data: dict[str, Any]) -> CalendarCommand:
    return CalendarCommand(**{**data, "attendees": tuple(data["attendees"])})


def _with_status(command: CalendarCommand) -> CalendarCommand:
    missing: Missing | None = None
    if command.kind == "create":
        if not command.date_text:
            missing = "date"
        elif not command.start and not command.all_day:
            missing = "time"
    elif command.kind in ("reschedule", "cancel"):
        if not (command.query or command.source_date):
            missing = "event"
        elif command.kind == "reschedule" and not (command.date_text or command.start):
            missing = "when"
    return replace(command, missing=missing, error=_QUESTIONS[missing] if missing else None)


def _event_query(text: str) -> str | None:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).strip()
    words = _FILLER.sub(" ", strip_when(text))
    return " ".join(words.split()).strip(" .,:;") or None


def _title(text: str, label: str, noun: str, guest: str | None) -> str:
    for pattern in (_QUOTED, _CALLED, _ABOUT):
        match = pattern.search(text)
        if match and match.group(1).strip():
            value = match.group(1).strip()
            return value[0].upper() + value[1:]
    base = " ".join(f"{label} {noun}".split())
    base = base[0].upper() + base[1:]
    return f"{base} with {guest}" if guest else base


def _create(text: str, label: str, noun: str) -> CalendarCommand:
    guests = tuple(
        dict.fromkeys(a for m in _GUESTS.finditer(text) for a in _ADDRESS.findall(m.group(1)))
    )
    name_match = None if guests else _GUEST_NAME.search(text)
    guest_name = " ".join(name_match.group(1).lower().split()) if name_match else None
    location = _LOCATION.search(text)
    when_text = _LOCATION.sub(" ", text)
    start, end = find_times(when_text)
    return _with_status(
        CalendarCommand(
            "create",
            title=_title(text, label, noun, guest_name)[:200],
            attendees=guests,
            guest_name=guest_name,
            date_text=find_date(when_text),
            start=start,
            end=end,
            duration_minutes=find_duration(when_text),
            meet=bool(_MEET.search(text)),
            location=location.group(1).strip() if location else None,
        )
    )


def _reschedule(rest: str, verb: str) -> CalendarCommand | None:
    for match in _TO_WORD.finditer(rest):
        target, when = rest[: match.start()], rest[match.end() :]
        date_text, (start, end) = find_date(when), find_times(when)
        if date_text or start:
            if verb != "reschedule" and not (re.search(_NOUN, target, re.IGNORECASE) or _QUOTED.search(target)):
                return None
            return _with_status(
                CalendarCommand(
                    "reschedule",
                    query=_event_query(target),
                    source_date=find_date(target),
                    date_text=date_text,
                    start=start,
                    end=end,
                    duration_minutes=find_duration(when),
                )
            )
    if verb != "reschedule":
        return None
    return _with_status(
        CalendarCommand("reschedule", query=_event_query(rest), source_date=find_date(rest))
    )


def parse_calendar_command(command: str) -> CalendarCommand | None:
    """A CalendarCommand if this is a calendar instruction, otherwise None."""
    if _FREE.match(command):
        return CalendarCommand("free", date_text=find_date(command) or "today")
    if _LIST.match(command):
        return CalendarCommand("list", date_text=find_date(command) or "today")
    created = _CREATE.match(command)
    if created:
        label = created["label"].strip()
        if not {w.lower() for w in label.split()} & _NOT_A_LABEL:
            return _create(command, label, created["noun"].lower())
    added = _ADD_TO.match(command)
    if added:
        title = strip_when(added["title"]).strip(" .,:;") or "Event"
        return _create(command.replace(added["title"], f'"{title}" {added["title"]}', 1), "", "event")
    moved = _RESCHEDULE.match(command)
    if moved:
        result = _reschedule(moved["rest"], moved["verb"].lower())
        if result is not None:
            return result
    cancelled = _CANCEL_FROM_CALENDAR.match(command) or _CANCEL.match(command)
    if cancelled and not re.search(r"\be-?mails?\b", cancelled["what"], re.IGNORECASE):
        what = cancelled["what"]
        return _with_status(
            CalendarCommand("cancel", query=_event_query(what), source_date=find_date(what))
        )
    return None


def finalize_calendar(command: CalendarCommand) -> CalendarCommand:
    """Set the next missing detail on a command built by the Manager path."""
    return _with_status(command)


def guests_in(text: str) -> tuple[str, ...]:
    """Guest addresses, read by code from the user's own words."""
    return tuple(
        dict.fromkeys(a for m in _GUESTS.finditer(text) for a in _ADDRESS.findall(m.group(1)))
    )


def wants_meet(text: str) -> bool:
    return bool(_MEET.search(text))


def parse_calendar_mark(text: str) -> CalendarCommand | None:
    """Offline fallback for "mark 24 November as X on my calendar" if the Manager is down."""
    if not (_CALENDAR_WORD.search(text) and _MARK_WORD.search(text)):
        return None
    date_text = find_date(text)
    if not date_text:
        return None
    label = _MARK_AS.search(text)
    title = strip_when(label.group(1)).strip(" .,:;") if label else ""
    start, end = find_times(text)
    return _with_status(
        CalendarCommand(
            "create",
            title=(title[:1].upper() + title[1:])[:200] or "Calendar reminder",
            attendees=guests_in(text),
            date_text=date_text,
            start=start,
            end=end,
            duration_minutes=find_duration(text),
            all_day=not start,
        )
    )


def continue_calendar(command: CalendarCommand, reply: str) -> CalendarCommand:
    """Fill the missing detail from the user's reply. Unusable replies change nothing."""
    text = reply.strip()
    if command.missing == "event":
        return _with_status(
            replace(command, query=_event_query(text), source_date=find_date(text))
        )
    date_text = find_date(text)
    start, end = find_times(text)
    return _with_status(
        replace(
            command,
            date_text=date_text or command.date_text,
            start=start or command.start,
            end=end or command.end,
            duration_minutes=find_duration(text) or command.duration_minutes,
        )
    )


def apply_guest_contact(command: CalendarCommand, contacts: dict[str, str]) -> CalendarCommand:
    """Invite a named guest only if the user explicitly saved that contact."""
    if command.kind != "create" or command.attendees or not command.guest_name:
        return command
    email = contacts.get(command.guest_name)
    return replace(command, attendees=(email,)) if email else command
