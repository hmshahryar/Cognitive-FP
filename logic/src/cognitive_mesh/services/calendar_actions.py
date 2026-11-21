"""Read Google Calendar and prepare one exact change for the user to approve.

This path uses plain code only. Event titles and details are untrusted and never reach a
model. Reads answer immediately; nothing here creates, changes or deletes an event.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cognitive_mesh.integrations import google_calendar as gcal
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.workflows.calendar_commands import CalendarCommand
from cognitive_mesh.workflows.calendar_time import (
    DEFAULT_DURATION_MINUTES,
    at_clock,
    resolve_date,
)

READ_FAILED = (
    "I couldn't read your Google Calendar just now, so nothing was changed. "
    "Press Retry, or reconnect Google Calendar on the Connectors page."
)
TOO_MANY_EVENTS = (
    "That period has too many events for me to calculate free time reliably. Try a single day."
)
SEARCH_DAYS = 60
MAX_EVENTS = 25
MAX_FREE_TIME_EVENTS = 250
MAX_FREE_SLOTS = 10
WORK_START = time(9, 0)
WORK_END = time(18, 0)
MIN_FREE_MINUTES = 30


class CalendarStop(Exception):
    """The command can't continue. `message` tells the user what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedCalendarAction:
    action_type: str
    payload: dict[str, Any]
    text: str


async def _read(
    gateway: IntegrationGateway, user_id: str, tool: str, arguments: dict[str, Any]
) -> Any:
    try:
        return await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=gcal.TOOLKIT, tool_slug=tool, arguments=arguments
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise CalendarStop(READ_FAILED) from exc


async def _profile(gateway: IntegrationGateway, user_id: str) -> tuple[str, str, ZoneInfo]:
    # The events list carries the calendar's timezone and needs only the calendar.events scope.
    data = await _read(
        gateway,
        user_id,
        gcal.LIST_TOOL,
        {"calendarId": gcal.PRIMARY, "maxResults": 1, "fields": "timeZone,summary"},
    )
    if not isinstance(data, dict):
        raise CalendarStop(READ_FAILED)
    name = str(data.get("timeZone") or "")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarStop(READ_FAILED) from exc
    return str(data.get("summary") or gcal.PRIMARY), name, zone


def _parse_moment(value: dict[str, Any] | None, zone: ZoneInfo) -> tuple[datetime | None, bool]:
    """(local start of this moment, is_all_day)."""
    if not isinstance(value, dict):
        return None, False
    try:
        if value.get("dateTime"):
            moment = datetime.fromisoformat(str(value["dateTime"]))
            moment = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment
            return moment.astimezone(zone), False
        if value.get("date"):
            day = datetime.fromisoformat(str(value["date"]))
            return day.replace(tzinfo=zone), True
    except ValueError:
        return None, False
    return None, False


def _event(row: dict[str, Any], zone: ZoneInfo) -> dict[str, Any] | None:
    start, all_day = _parse_moment(row.get("start"), zone)
    end, _ = _parse_moment(row.get("end"), zone)
    if not row.get("id") or start is None or end is None or row.get("status") == "cancelled":
        return None
    attendees = [
        a["email"]
        for a in row.get("attendees") or []
        if isinstance(a, dict) and a.get("email") and not a.get("self") and not a.get("resource")
    ]
    organizer = row.get("organizer") if isinstance(row.get("organizer"), dict) else {}
    return {
        "id": str(row["id"]),
        "title": str(row.get("summary") or "(no title)")[:gcal.MAX_TITLE_CHARS],
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "all_day": all_day,
        "attendees": attendees,
        "is_organizer": bool(organizer.get("self")),
        "recurring": bool(row.get("recurringEventId")),
        "busy": row.get("transparency") != "transparent",
    }


async def _events(
    gateway: IntegrationGateway, user_id: str, zone_name: str, zone: ZoneInfo,
    start: datetime, end: datetime, query: str | None = None,
    limit: int = MAX_EVENTS, require_complete: bool = False,
) -> list[dict[str, Any]]:
    arguments: dict[str, Any] = {
        "calendarId": gcal.PRIMARY,
        "timeMin": start.isoformat(timespec="seconds"),
        "timeMax": end.isoformat(timespec="seconds"),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": limit,
        "timeZone": zone_name,
    }
    if query:
        arguments["query"] = query
    data = await _read(gateway, user_id, gcal.LIST_TOOL, arguments)
    if require_complete and isinstance(data, dict) and data.get("nextPageToken"):
        raise CalendarStop(TOO_MANY_EVENTS)  # a partial list could report busy time as free
    rows = data.get("items") if isinstance(data, dict) else None
    events = [_event(row, zone) for row in rows or [] if isinstance(row, dict)]
    return [event for event in events if event is not None]


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0")


def _day(moment: datetime) -> str:
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b')}"


def _line(event: dict[str, Any]) -> str:
    start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
    if event["all_day"]:
        return f"- {_day(start)}, all day: {event['title']}"
    return f"- {_day(start)}, {_clock(start)} to {_clock(end)}: {event['title']}"


def _window(text: str, now: datetime) -> tuple[datetime, datetime, str]:
    value = text.lower()
    midnight = datetime.combine(now.date(), time.min, now.tzinfo)
    if value == "this week":
        return now, midnight + timedelta(days=7 - now.weekday()), "this week"
    if value == "next week":
        first = midnight + timedelta(days=7 - now.weekday())
        return first, first + timedelta(days=7), "next week"
    day = resolve_date(value, now.date())
    if day is None:
        raise CalendarStop("I couldn't understand that date. Try tomorrow, Friday or 25 November.")
    start = datetime.combine(day, time.min, now.tzinfo)
    return (max(start, now) if day == now.date() else start), start + timedelta(days=1), value


async def read_events(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand,
    now: datetime | None = None,
) -> str:
    _calendar, zone_name, zone = await _profile(gateway, user_id)
    current = (now or datetime.now(zone)).astimezone(zone)
    start, end, label = _window(command.date_text or "today", current)
    events = await _events(gateway, user_id, zone_name, zone, start, end)
    if not events:
        return f"You have nothing on your calendar for {label}."
    more = "\nThere may be more events." if len(events) >= MAX_EVENTS else ""
    return f"Your calendar for {label} ({zone_name}):\n" + "\n".join(map(_line, events)) + more


async def read_free_time(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand,
    now: datetime | None = None,
) -> str:
    _calendar, zone_name, zone = await _profile(gateway, user_id)
    current = (now or datetime.now(zone)).astimezone(zone)
    start, end, label = _window(command.date_text or "today", current)
    events = await _events(
        gateway, user_id, zone_name, zone, start, end,
        limit=MAX_FREE_TIME_EVENTS, require_complete=True,
    )
    busy = sorted(
        (datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
        for e in events
        if e["busy"] and not e["all_day"]
    )

    slots: list[str] = []
    minimum = timedelta(minutes=MIN_FREE_MINUTES)
    day = start.date()
    while day < end.date() or (day == end.date() and end.time() > time.min):
        cursor = max(start, datetime.combine(day, WORK_START, zone))
        day_end = min(end, datetime.combine(day, WORK_END, zone))
        for busy_start, busy_end in busy:
            if busy_end <= cursor or busy_start >= day_end:
                continue
            if busy_start - cursor >= minimum:
                slots.append(f"- {_day(cursor)}, {_clock(cursor)} to {_clock(busy_start)}")
            cursor = max(cursor, busy_end)
        if day_end - cursor >= minimum:
            slots.append(f"- {_day(cursor)}, {_clock(cursor)} to {_clock(day_end)}")
        day += timedelta(days=1)
    slots = slots[:MAX_FREE_SLOTS]
    if not slots:
        return f"You have no free time of at least 30 minutes between 9 AM and 6 PM for {label}."
    return f"You're free for {label} ({zone_name}, 9 AM to 6 PM):\n" + "\n".join(slots)


async def _conflicts(
    gateway: IntegrationGateway, user_id: str, zone_name: str, zone: ZoneInfo,
    start: datetime, end: datetime, ignore_id: str | None = None,
) -> list[dict[str, Any]]:
    events = await _events(gateway, user_id, zone_name, zone, start, end)
    return [
        {"title": e["title"], "start": e["start"], "end": e["end"]}
        for e in events
        if e["id"] != ignore_id
        and e["busy"]
        and not e["all_day"]
        and datetime.fromisoformat(e["start"]) < end
        and datetime.fromisoformat(e["end"]) > start
    ]


def _span(
    command: CalendarCommand, day: Any, zone: ZoneInfo, default_minutes: int,
    start_clock: str,
) -> tuple[datetime, datetime]:
    start = at_clock(day, start_clock, zone)
    if command.end:
        end = at_clock(day, command.end, zone)
        if end <= start:  # e.g. 11pm to 1am
            end += timedelta(days=1)
    else:
        end = start + timedelta(minutes=command.duration_minutes or default_minutes)
    return start, end


async def _prepare_create(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand, now: datetime | None
) -> PreparedCalendarAction:
    calendar, zone_name, zone = await _profile(gateway, user_id)
    current = (now or datetime.now(zone)).astimezone(zone)
    day = resolve_date(command.date_text or "", current.date())
    if day is None:
        raise CalendarStop("I couldn't understand that date. Try tomorrow, Friday or 25 November.")
    if command.all_day:
        if day < current.date():
            raise CalendarStop("That date has already passed. Send the command with a future date.")
        payload = gcal.build_all_day_payload(
            calendar=calendar, timezone=zone_name, title=command.title or "Calendar event",
            day=day, attendees=list(command.attendees),
        )
        return PreparedCalendarAction(
            gcal.CREATE_ACTION, payload,
            f"Create all-day: {payload['event']['title']} on {day.isoformat()}",
        )
    start, end = _span(command, day, zone, DEFAULT_DURATION_MINUTES, command.start or "")
    if start <= current:
        raise CalendarStop("That time has already passed. Send the command with a future time.")
    conflicts = await _conflicts(gateway, user_id, zone_name, zone, start, end)
    payload = gcal.build_create_payload(
        calendar=calendar, timezone=zone_name, title=command.title or "Meeting", start=start,
        end=end, attendees=list(command.attendees), meet=command.meet, location=command.location,
        conflicts=conflicts,
    )
    return PreparedCalendarAction(
        gcal.CREATE_ACTION, payload, f"Create: {payload['event']['title']} at {payload['event']['start']}"
    )


async def _find_target(
    gateway: IntegrationGateway, user_id: str, zone_name: str, zone: ZoneInfo,
    command: CalendarCommand, current: datetime,
) -> dict[str, Any]:
    if command.source_date:
        day = resolve_date(command.source_date, current.date())
        if day is None:
            raise CalendarStop("I couldn't understand that date.")
        start = datetime.combine(day, time.min, zone)
        end = start + timedelta(days=1)
    else:
        start, end = current, current + timedelta(days=SEARCH_DAYS)
    events = await _events(gateway, user_id, zone_name, zone, start, end, command.query)
    matches = [e for e in events if not e["all_day"]]
    if not matches:
        raise CalendarStop("I couldn't find a matching upcoming event. Nothing was changed.")
    if len(matches) > 1:
        options = "\n".join(_line(e) for e in matches[:5])
        raise CalendarStop(
            "More than one event matches. Send the command again with its exact title or day:\n"
            + options
        )
    target = matches[0]
    if not target["is_organizer"]:
        raise CalendarStop(
            "You were invited to this event, so only its organizer can change or cancel it."
        )
    return target


async def _prepare_reschedule(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand, now: datetime | None
) -> PreparedCalendarAction:
    calendar, zone_name, zone = await _profile(gateway, user_id)
    current = (now or datetime.now(zone)).astimezone(zone)
    target = await _find_target(gateway, user_id, zone_name, zone, command, current)
    old_start = datetime.fromisoformat(target["start"])
    old_end = datetime.fromisoformat(target["end"])
    day = resolve_date(command.date_text, current.date()) if command.date_text else old_start.date()
    if day is None:
        raise CalendarStop("I couldn't understand that date. Try tomorrow, Friday or 25 November.")
    minutes = max(1, int((old_end - old_start).total_seconds() // 60))
    clock = command.start or old_start.strftime("%H:%M")
    start, end = _span(command, day, zone, minutes, clock)
    if start <= current:
        raise CalendarStop("That time has already passed. Send the command with a future time.")
    conflicts = await _conflicts(gateway, user_id, zone_name, zone, start, end, target["id"])
    payload = gcal.build_reschedule_payload(
        calendar=calendar, timezone=zone_name, target=target, start=start, end=end,
        conflicts=conflicts,
    )
    return PreparedCalendarAction(
        gcal.RESCHEDULE_ACTION, payload, f"Reschedule: {target['title']} to {payload['event']['new_start']}"
    )


async def _prepare_cancel(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand, now: datetime | None
) -> PreparedCalendarAction:
    calendar, zone_name, zone = await _profile(gateway, user_id)
    current = (now or datetime.now(zone)).astimezone(zone)
    target = await _find_target(gateway, user_id, zone_name, zone, command, current)
    payload = gcal.build_cancel_payload(calendar=calendar, timezone=zone_name, target=target)
    return PreparedCalendarAction(
        gcal.CANCEL_ACTION, payload, f"Cancel: {target['title']} at {target['start']}"
    )


async def prepare_change(
    gateway: IntegrationGateway, user_id: str, command: CalendarCommand,
    now: datetime | None = None,
) -> PreparedCalendarAction:
    if command.kind == "create":
        return await _prepare_create(gateway, user_id, command, now)
    if command.kind == "reschedule":
        return await _prepare_reschedule(gateway, user_id, command, now)
    return await _prepare_cancel(gateway, user_id, command, now)
