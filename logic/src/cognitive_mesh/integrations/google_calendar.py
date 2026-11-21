"""The exact Google Calendar actions that can be requested, and their payload rules.

Tool slugs, inputs and response shapes were verified against Composio's toolkit. The payload
is what the user approves and what gets signed:

    {"calendar": id, "timezone": tz, "arguments": <exact tool input>,
     "event": <what the user sees>, "conflicts": [<overlapping events>]}

Guest notifications and Google Meet are always set explicitly, because the tool's defaults
would email every guest and add a Meet link.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cognitive_mesh.integrations.gmail import ADDRESS

TOOLKIT = "googlecalendar"
CREATE_ACTION = "calendar.create_event"
RESCHEDULE_ACTION = "calendar.reschedule_event"
CANCEL_ACTION = "calendar.cancel_event"
CREATE_TOOL = "GOOGLECALENDAR_CREATE_EVENT"
PATCH_TOOL = "GOOGLECALENDAR_PATCH_EVENT"
DELETE_TOOL = "GOOGLECALENDAR_DELETE_EVENT"
LIST_TOOL = "GOOGLECALENDAR_EVENTS_LIST"  # also provides the calendar's timezone

PRIMARY = "primary"
WRITE_SCOPES = (
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.owned",
)
MAX_ATTENDEES = 50
MAX_TITLE_CHARS = 200
MAX_LOCATION_CHARS = 300

_ADDRESS = re.compile(rf"^{ADDRESS}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
_CREATE_KEYS = {
    "calendar_id", "summary", "start_datetime", "end_datetime", "timezone", "attendees",
    "send_updates", "create_meeting_room",
}
_ALL_DAY_KEYS = {
    "calendar_id", "summary", "start_date", "end_date", "timezone", "attendees", "send_updates",
    "create_meeting_room", "transparency",
}
_PATCH_KEYS = {"calendar_id", "event_id", "start_time", "end_time", "timezone", "send_updates"}
_DELETE_KEYS = {"calendar_id", "event_id", "send_updates"}
_ENVELOPE = {"calendar", "timezone", "arguments", "event", "conflicts"}
_RESULT_URLS = ("https://www.google.com/calendar/", "https://calendar.google.com/")


class InvalidCalendarActionError(ValueError):
    pass


def _check_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != _ENVELOPE:
        raise InvalidCalendarActionError(f"Payload must have exactly {sorted(_ENVELOPE)}")
    try:
        ZoneInfo(str(payload["timezone"]))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidCalendarActionError("A valid timezone is required") from exc
    arguments, event = payload["arguments"], payload["event"]
    if not isinstance(arguments, dict) or not isinstance(event, dict):
        raise InvalidCalendarActionError("Arguments and event details are required")
    if arguments.get("calendar_id") != PRIMARY:
        raise InvalidCalendarActionError("Only the primary calendar is supported")
    if not isinstance(payload["conflicts"], list):
        raise InvalidCalendarActionError("Conflicts must be a list")
    return arguments


def _check_attendee_rule(attendees: list[str], send_updates: Any) -> None:
    if len(attendees) > MAX_ATTENDEES:
        raise InvalidCalendarActionError(f"At most {MAX_ATTENDEES} guests are allowed")
    if any(not isinstance(a, str) or not _ADDRESS.match(a) for a in attendees):
        raise InvalidCalendarActionError("Every guest must be a valid email address")
    if send_updates != ("all" if attendees else "none"):
        raise InvalidCalendarActionError("Guest notifications must match the guest list")


def _parse(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidCalendarActionError("Invalid event time") from exc


def _validate_all_day(arguments: dict[str, Any]) -> None:
    if set(arguments) != _ALL_DAY_KEYS:
        raise InvalidCalendarActionError("Unexpected all-day event fields")
    title = arguments["summary"]
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
        raise InvalidCalendarActionError("An event title is required")
    try:
        start, end = date.fromisoformat(arguments["start_date"]), date.fromisoformat(arguments["end_date"])
    except (TypeError, ValueError) as exc:
        raise InvalidCalendarActionError("Invalid all-day date") from exc
    if end <= start:  # Google's all-day end date is exclusive
        raise InvalidCalendarActionError("The all-day end date must be after its start")
    if arguments["create_meeting_room"] is not False or arguments["transparency"] != "transparent":
        raise InvalidCalendarActionError("All-day markers must not add Meet or block time")
    _check_attendee_rule(arguments["attendees"], arguments["send_updates"])


def validate_create(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload)
    if "start_date" in arguments:
        _validate_all_day(arguments)
        return
    if not _CREATE_KEYS <= set(arguments) <= {*_CREATE_KEYS, "location"}:
        raise InvalidCalendarActionError("Unexpected event fields")
    title = arguments["summary"]
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
        raise InvalidCalendarActionError("An event title is required")
    if len(str(arguments.get("location") or "")) > MAX_LOCATION_CHARS:
        raise InvalidCalendarActionError("The location is too long")
    start, end = _parse(arguments["start_datetime"]), _parse(arguments["end_datetime"])
    if start.tzinfo or end.tzinfo or end <= start:
        raise InvalidCalendarActionError("The event needs a local start before its end")
    if not isinstance(arguments["create_meeting_room"], bool):
        raise InvalidCalendarActionError("The Meet setting must be explicit")
    _check_attendee_rule(arguments["attendees"], arguments["send_updates"])


def _check_target(payload: dict[str, Any], arguments: dict[str, Any]) -> None:
    event_id = arguments.get("event_id")
    if not isinstance(event_id, str) or not _EVENT_ID.match(event_id):
        raise InvalidCalendarActionError("A valid event id is required")
    if payload["event"].get("id") != event_id:
        raise InvalidCalendarActionError("The target event does not match the action")
    if arguments["send_updates"] != ("all" if payload["event"].get("attendees") else "none"):
        raise InvalidCalendarActionError("Guest notifications must match the guest list")


def validate_reschedule(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload)
    if set(arguments) != _PATCH_KEYS:
        raise InvalidCalendarActionError("Unexpected reschedule fields")
    _check_target(payload, arguments)
    start, end = _parse(arguments["start_time"]), _parse(arguments["end_time"])
    if not start.tzinfo or not end.tzinfo or end <= start:
        raise InvalidCalendarActionError("The new time needs an offset and a start before its end")


def validate_cancel(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload)
    if set(arguments) != _DELETE_KEYS:
        raise InvalidCalendarActionError("Unexpected cancel fields")
    _check_target(payload, arguments)


def _envelope(
    calendar: str, timezone: str, arguments: dict[str, Any], event: dict[str, Any],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "calendar": calendar,
        "timezone": timezone,
        "arguments": arguments,
        "event": event,
        "conflicts": conflicts,
    }


def build_create_payload(
    *, calendar: str, timezone: str, title: str, start: datetime, end: datetime,
    attendees: list[str], meet: bool, location: str | None, conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "calendar_id": PRIMARY,
        "summary": title,
        "start_datetime": start.replace(tzinfo=None).isoformat(timespec="seconds"),
        "end_datetime": end.replace(tzinfo=None).isoformat(timespec="seconds"),
        "timezone": timezone,
        "attendees": list(attendees),
        "send_updates": "all" if attendees else "none",
        "create_meeting_room": meet,
    }
    if location:
        arguments["location"] = location
    event = {
        "title": title,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "attendees": list(attendees),
        "meet": meet,
        "location": location,
    }
    payload = _envelope(calendar, timezone, arguments, event, conflicts)
    validate_create(payload)
    return payload


def build_all_day_payload(
    *, calendar: str, timezone: str, title: str, day: date, attendees: list[str]
) -> dict[str, Any]:
    """A whole-day marker. It does not block the user's time or add a Meet link."""
    end = day + timedelta(days=1)
    arguments = {
        "calendar_id": PRIMARY,
        "summary": title,
        "start_date": day.isoformat(),
        "end_date": end.isoformat(),
        # Sent explicitly so the tool doesn't need extra permission to look it up.
        "timezone": timezone,
        "attendees": list(attendees),
        "send_updates": "all" if attendees else "none",
        "create_meeting_room": False,
        "transparency": "transparent",
    }
    event = {
        "title": title,
        "start": day.isoformat(),
        "end": end.isoformat(),
        "attendees": list(attendees),
        "meet": False,
        "location": None,
        "all_day": True,
    }
    payload = _envelope(calendar, timezone, arguments, event, [])
    validate_create(payload)
    return payload


def build_reschedule_payload(
    *, calendar: str, timezone: str, target: dict[str, Any], start: datetime, end: datetime,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    arguments = {
        "calendar_id": PRIMARY,
        "event_id": target["id"],
        "start_time": start.isoformat(timespec="seconds"),
        "end_time": end.isoformat(timespec="seconds"),
        "timezone": timezone,
        "send_updates": "all" if target["attendees"] else "none",
    }
    event = {**target, "new_start": arguments["start_time"], "new_end": arguments["end_time"]}
    payload = _envelope(calendar, timezone, arguments, event, conflicts)
    validate_reschedule(payload)
    return payload


def build_cancel_payload(*, calendar: str, timezone: str, target: dict[str, Any]) -> dict[str, Any]:
    arguments = {
        "calendar_id": PRIMARY,
        "event_id": target["id"],
        "send_updates": "all" if target["attendees"] else "none",
    }
    payload = _envelope(calendar, timezone, arguments, dict(target), [])
    validate_cancel(payload)
    return payload


def _event_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    nested = data.get("response_data")
    return nested if isinstance(nested, dict) else data


def result_id(data: Any) -> str | None:
    value = _event_data(data).get("id")
    return value if isinstance(value, str) and value else None


def result_url(data: Any, _result_id: str | None) -> str | None:
    url = _event_data(data).get("htmlLink")
    return url if isinstance(url, str) and url.startswith(_RESULT_URLS) else None


def deleted_id(data: Any) -> str | None:
    """A completed delete has no event body. The tool reports success with a status."""
    return "deleted" if isinstance(data, dict) else None


def no_url(_data: Any, _result_id: str | None) -> str | None:
    return None
