"""Google Calendar: reads are immediate, every change needs one exact, verified approval."""

import uuid
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.integrations import google_calendar as gcal
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    ActionOutcomeUnknownError,
    ActionRejectedError,
)
from cognitive_mesh.models import Approval, ConnectionStatus, IntegrationConnection, Publication
from cognitive_mesh.services.calendar_actions import read_free_time
from cognitive_mesh.workflows.calendar_commands import continue_calendar, parse_calendar_command
from cognitive_mesh.workflows.calendar_time import (
    find_date,
    find_duration,
    find_times,
    resolve_date,
)
from cognitive_mesh.workflows.command_router import route_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve, fresh_runner
from tests.test_publishing import send, stub_gateway
from tests.test_runner import status

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
DAY = "2099-01-05"
SCHEDULE = f"Schedule a budget meeting with ann@example.com on {DAY} at 3pm for 45 minutes"
CREATED = {"response_data": {"id": "evt1", "htmlLink": "https://www.google.com/calendar/event?eid=abc"}}


def event(event_id="evt1", title="Budget review", start="15:00", end="15:30", **extra):  # type: ignore[no-untyped-def]
    return {
        "id": event_id,
        "summary": title,
        "status": "confirmed",
        "start": {"dateTime": f"{DAY}T{start}:00+05:00"},
        "end": {"dateTime": f"{DAY}T{end}:00+05:00"},
        "organizer": {"self": True},
        "attendees": [{"email": "jane@example.com", "self": True}, {"email": "ann@example.com"}],
        **extra,
    }


async def connect_calendar(client, app, headers=ALICE, scopes=None) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=headers)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug="googlecalendar",
                composio_account_id=f"ca_cal_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes or ["https://www.googleapis.com/auth/calendar"],
            )
        )
        await s.commit()


async def new_chat(client: httpx.AsyncClient) -> str:
    return (await client.post("/api/chats", headers=ALICE, json={})).json()["id"]


async def say(client: httpx.AsyncClient, app: FastAPI, chat: str, text: str) -> dict:
    task = (await client.post(f"/api/chats/{chat}/tasks", headers=ALICE, json={"request": text})).json()
    await app.state.runner.run_once()
    return task


async def last_reply(client: httpx.AsyncClient, chat: str) -> str:
    return (await client.get(f"/api/chats/{chat}", headers=ALICE)).json()["messages"][-1]["content"]


async def paused(client: httpx.AsyncClient, app: FastAPI, text: str) -> tuple[dict, dict]:
    await connect_calendar(client, app)
    task = await say(client, app, await new_chat(client), text)
    assert await status(client, task) == "AWAITING_APPROVAL"
    return task, (await approvals_for(client, task))[0]


# ---------- date and time parsing ----------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3pm", ("15:00", None)),
        ("12am", ("00:00", None)),
        ("12:30 pm", ("12:30", None)),
        ("15:45", ("15:45", None)),
        ("from 3 to 4pm", ("15:00", "16:00")),
        ("11 to 1pm", ("11:00", "13:00")),
        ("noon", ("12:00", None)),
        ("at 3", (None, None)),
    ],
)
def test_times(text: str, expected: tuple[str | None, str | None]) -> None:
    assert find_times(text) == expected


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("for 45 minutes", 45), ("for 1.5 hours", 90), ("for an hour", 60), ("for half an hour", 30)],
)
def test_durations(text: str, minutes: int) -> None:
    assert find_duration(text) == minutes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", date(2026, 11, 21)),  # a Saturday
        ("tomorrow", date(2026, 11, 22)),
        ("monday", date(2026, 11, 23)),
        ("saturday", date(2026, 11, 28)),
        ("this saturday", date(2026, 11, 21)),
        ("next monday", date(2026, 11, 23)),
        ("25 november", date(2026, 11, 25)),
        ("nov 3rd", date(2027, 11, 3)),
        ("2099-01-05", date(2099, 1, 5)),
        ("31 february", None),
    ],
)
def test_dates(text: str, expected: date | None) -> None:
    assert resolve_date(text, date(2026, 11, 21)) == expected


def test_date_is_found_inside_a_sentence() -> None:
    assert find_date("meet on 25th November at 3pm") == "25th november"


# ---------- command parsing ----------
def test_schedule_command() -> None:
    command = parse_calendar_command(SCHEDULE)
    assert command is not None and command.kind == "create" and command.missing is None
    assert command.title == "Budget meeting" and command.attendees == ("ann@example.com",)
    assert (command.date_text, command.start, command.duration_minutes) == (DAY, "15:00", 45)
    assert command.meet is False


def test_address_in_title_text_is_not_invited() -> None:
    command = parse_calendar_command(
        f'Schedule a meeting called "Reply to evil@example.com" on {DAY} at 3pm'
    )
    assert command is not None and command.attendees == ()


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("What's on my calendar tomorrow", "list"),
        ("show my schedule next week", "list"),
        ("do I have meetings today", "list"),
        ("when am I free on Friday", "free"),
        ("add dentist appointment to my calendar tomorrow at 3pm", "create"),
        ("reschedule my budget meeting to Friday at 4pm", "reschedule"),
        ("move the standup meeting to 5pm", "reschedule"),
        ("cancel my budget meeting tomorrow", "cancel"),
        ("delete the dentist appointment", "cancel"),
    ],
)
def test_calendar_commands(text: str, kind: str) -> None:
    command = parse_calendar_command(text)
    assert command is not None and command.kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "Write a LinkedIn post about how to schedule a meeting",
        "create a post about the meeting",
        "Schedule a LinkedIn post",
        "Delete the latest email from boss@example.com",
        "Post this on LinkedIn: cancel my meeting",
        "Send an email to ann@example.com about the meeting",
        "Move fast and break things",
    ],
)
def test_other_commands_are_not_calendar(text: str) -> None:
    assert parse_calendar_command(text) is None
    assert route_command(text).action != "calendar"


def test_bare_hour_asks_instead_of_guessing() -> None:
    command = parse_calendar_command(f"Schedule a meeting on {DAY} at 3")
    assert command is not None and command.missing == "time"
    updated = continue_calendar(command, "3pm")
    assert updated.start == "15:00" and updated.missing is None


# ---------- payload rules ----------
def test_payload_sets_notifications_and_meet_explicitly() -> None:
    zone = ZoneInfo("Asia/Karachi")
    payload = gcal.build_create_payload(
        calendar="jane@example.com", timezone="Asia/Karachi", title="Solo",
        start=datetime(2099, 1, 5, 15, tzinfo=zone), end=datetime(2099, 1, 5, 16, tzinfo=zone),
        attendees=[], meet=False, location=None, conflicts=[],
    )
    args = payload["arguments"]
    assert args["send_updates"] == "none" and args["create_meeting_room"] is False
    assert args["start_datetime"] == "2099-01-05T15:00:00"
    tampered = {**payload, "arguments": {**args, "send_updates": "all"}}
    with pytest.raises(gcal.InvalidCalendarActionError):
        gcal.validate_create(tampered)


def test_calendar_tools_are_allowlisted_correctly() -> None:
    tools = {a.tool_slug for a in ACTIONS.values() if a.toolkit == "googlecalendar"}
    assert tools == {
        "GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_PATCH_EVENT", "GOOGLECALENDAR_DELETE_EVENT",
    }
    assert tools <= APPROVED_ACTION_TOOLS and not tools & READ_ONLY_TOOLS
    assert "GOOGLECALENDAR_CLEAR_CALENDAR" not in APPROVED_ACTION_TOOLS


# ---------- reads ----------
async def test_read_calendar_answers_immediately_without_ai(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.calendar_events = [event()]
    await connect_calendar(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, f"show my calendar on {DAY}")
    assert await status(client, task) == "COMPLETED"
    reply = await last_reply(client, chat)
    assert "Budget review" in reply and "3:00 PM" in reply
    assert (await approvals_for(client, task)) == [] and gateway.publish_calls == []
    assert team.writer_feedback == [] and team.email_calls == []
    assert gateway.calendar_queries[0]["singleEvents"] is True


async def test_free_time_is_calculated_from_events_and_rejects_partial_data(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [
        event("a", "Morning", "10:00", "12:00"),
        event("b", "Overlap", "11:30", "14:00"),
        event("c", "Optional", "15:00", "16:00", transparency="transparent"),
    ]
    command = parse_calendar_command(f"when am I free on {DAY}")
    assert command is not None
    reply = await read_free_time(gateway, "user", command)
    assert "9:00 AM to 10:00 AM" in reply and "2:00 PM to 6:00 PM" in reply
    assert "12:00 PM" not in reply

    gateway.calendar_has_more = True
    await connect_calendar(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, f"when am I free on {DAY}")
    assert "too many events" in await last_reply(client, chat)


# ---------- create ----------
async def test_schedule_needs_approval_and_sends_exact_event(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_response = CREATED
    task, a = await paused(client, app, SCHEDULE)
    assert gateway.publish_calls == [] and team.writer_feedback == []
    args = a["payload"]["arguments"]
    assert args["start_datetime"] == f"{DAY}T15:00:00" and args["end_datetime"] == f"{DAY}T15:45:00"
    assert args["timezone"] == "Asia/Karachi" and args["attendees"] == ["ann@example.com"]
    assert args["send_updates"] == "all" and args["create_meeting_room"] is False

    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    call = gateway.publish_calls[0]
    assert call["tool"] == "GOOGLECALENDAR_CREATE_EVENT" and call["arguments"] == args
    assert call["account"] == a["target_account_id"]
    rows = (await client.get(f"/api/tasks/{task['id']}/publications", headers=ALICE)).json()
    assert rows[0]["external_url"].startswith("https://www.google.com/calendar/")


async def test_conflict_is_shown_but_user_can_still_decide(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [
        event("busy", "Client call", "15:15", "15:30"),
        event("later", "Later", "17:00", "17:30"),
        event("free", "Optional", "15:00", "15:30", transparency="transparent"),
    ]
    _task, a = await paused(client, app, SCHEDULE)
    assert [c["title"] for c in a["payload"]["conflicts"]] == ["Client call"]


async def test_missing_details_continue_the_same_command(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_calendar(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "Schedule a meeting with ann@example.com")
    assert "What day" in await last_reply(client, chat)
    await say(client, app, chat, DAY)
    assert "What time" in await last_reply(client, chat)
    assert gateway.read_calls == []
    task = await say(client, app, chat, "4pm")
    a = (await approvals_for(client, task))[0]
    assert a["payload"]["arguments"]["start_datetime"] == f"{DAY}T16:00:00"
    assert a["payload"]["arguments"]["attendees"] == ["ann@example.com"]


async def test_past_time_is_refused(client: httpx.AsyncClient, app: FastAPI) -> None:
    await connect_calendar(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, "Schedule a meeting on 2020-01-05 at 3pm")
    assert "already passed" in await last_reply(client, chat)
    assert (await approvals_for(client, task)) == []


async def test_saved_contact_is_invited_but_unknown_name_is_not(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await connect_calendar(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "remember that my manager is ali@example.com")
    known = await say(client, app, chat, f"Schedule a meeting with my manager on {DAY} at 3pm")
    assert (await approvals_for(client, known))[0]["payload"]["arguments"]["attendees"] == ["ali@example.com"]
    unknown = await say(client, app, await new_chat(client), f"Schedule a meeting with sara on {DAY} at 4pm")
    payload = (await approvals_for(client, unknown))[0]["payload"]
    assert payload["arguments"]["attendees"] == [] and payload["arguments"]["send_updates"] == "none"


# ---------- reschedule and cancel ----------
async def test_reschedule_keeps_duration_and_needs_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [event()]
    gateway.publish_response = {"id": "evt1", "htmlLink": "https://www.google.com/calendar/event?eid=x"}
    task, a = await paused(client, app, f"reschedule my budget meeting on {DAY} to {DAY} at 5pm")
    args = a["payload"]["arguments"]
    assert args["start_time"] == f"{DAY}T17:00:00+05:00" and args["end_time"] == f"{DAY}T17:30:00+05:00"
    assert args["send_updates"] == "all" and gateway.publish_calls == []
    await approve(client, a)
    await app.state.runner.run_once()
    assert gateway.publish_calls[0]["tool"] == "GOOGLECALENDAR_PATCH_EVENT"
    assert await status(client, task) == "COMPLETED"


async def test_cancel_needs_approval_and_targets_one_event(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [event()]
    gateway.publish_response = {"response_data": {"status": "success"}}
    task, a = await paused(client, app, f"cancel my budget meeting on {DAY}")
    assert a["payload"]["arguments"] == {"calendar_id": "primary", "event_id": "evt1", "send_updates": "all"}
    assert gateway.publish_calls == []
    await approve(client, a)
    await app.state.runner.run_once()
    assert gateway.publish_calls[0]["tool"] == "GOOGLECALENDAR_DELETE_EVENT"
    assert await status(client, task) == "COMPLETED"


async def test_multiple_matches_ask_for_precision(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [event("a"), event("b", start="16:00", end="16:30")]
    await connect_calendar(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, f"cancel my budget meeting on {DAY}")
    assert "More than one" in await last_reply(client, chat)
    assert (await approvals_for(client, task)) == []


async def test_invited_guest_cannot_change_someone_elses_event(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.calendar_events = [event(organizer={"email": "boss@example.com"})]
    await connect_calendar(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, f"cancel my budget meeting on {DAY}")
    assert "only its organizer" in await last_reply(client, chat)
    assert (await approvals_for(client, task)) == []


# ---------- safety ----------
async def test_rejection_never_changes_the_calendar(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    _task, a = await paused(client, app, SCHEDULE)
    await client.post(f"/api/approvals/{a['id']}/reject", headers=ALICE, json={})
    await app.state.runner.run_once()
    assert gateway.publish_calls == []


async def test_changed_time_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused(client, app, SCHEDULE)
    await approve(client, a)
    moved = {**a["payload"], "arguments": {**a["payload"]["arguments"], "start_datetime": f"{DAY}T09:00:00"}}
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Approval).where(Approval.id == uuid.UUID(a["id"])).values(payload=moved))
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_read_only_grant_cannot_change_events(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_calendar(
        client, app, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
    )
    task = await say(client, app, await new_chat(client), SCHEDULE)
    a = (await approvals_for(client, task))[0]
    await approve(client, a)
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_not_connected_gives_guidance(client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway) -> None:
    chat = await new_chat(client)
    task = await say(client, app, chat, f"show my calendar on {DAY}")
    assert await status(client, task) == "FAILED"
    assert "Google Calendar isn't connected" in await last_reply(client, chat)
    assert gateway.read_calls == []


async def test_unknown_result_is_not_repeated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused(client, app, SCHEDULE)
    gateway.publish_error = ActionOutcomeUnknownError("timeout")
    await approve(client, a)
    await app.state.runner.run_once()
    assert (await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)).status_code == 409
    assert len(gateway.publish_calls) == 1


GOOGLE_403 = (
    '{"error": {"code": 403, "message": "Request had insufficient authentication scopes.", '
    '"status": "PERMISSION_DENIED", "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}'
)


async def test_google_permission_error_is_a_definite_failure_at_the_gateway() -> None:
    failed = SimpleNamespace(result_type="failed", error=GOOGLE_403)
    gateway, _created = stub_gateway(lambda slug, arguments: failed)
    with pytest.raises(ActionRejectedError):
        await send(gateway)


async def test_google_timeout_code_stays_unknown() -> None:
    failed = SimpleNamespace(result_type="failed", error='{"error": {"code": 408}}')
    gateway, _created = stub_gateway(lambda slug, arguments: failed)
    with pytest.raises(ActionOutcomeUnknownError):
        await send(gateway)


async def test_missing_permission_tells_user_to_reconnect_and_allows_retry(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused(client, app, SCHEDULE)
    gateway.publish_error = ActionRejectedError(GOOGLE_403)
    await approve(client, a)
    await app.state.runner.run_once()
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "Reconnect Google Calendar" in chat["messages"][-1]["content"]
    assert (await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)).status_code == 200


def test_all_day_payload_sends_the_calendar_timezone() -> None:
    payload = gcal.build_all_day_payload(
        calendar="jane@example.com", timezone="Asia/Karachi", title="Deadline",
        day=date(2099, 1, 5), attendees=[],
    )
    assert payload["arguments"]["timezone"] == "Asia/Karachi"


class Crash(BaseException):
    pass


async def test_crash_during_create_is_never_repeated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    task, a = await paused(client, app, SCHEDULE)
    gateway.publish_error = Crash()
    await approve(client, a)
    with pytest.raises(Crash):
        await app.state.runner.run_once()
    await app.state.runner.close()
    gateway.publish_error = None
    restarted = fresh_runner(app, gateway, team)
    try:
        await restarted.recover()
    finally:
        await restarted.close()
    assert len(gateway.publish_calls) == 1 and await status(client, task) == "FAILED"
    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(Publication))
    assert row is not None and row.status == "NEEDS_CHECK"


async def test_calendar_approval_is_workspace_isolated(client: httpx.AsyncClient, app: FastAPI) -> None:
    _task, a = await paused(client, app, SCHEDULE)
    await client.get("/api/me", headers=BOB)
    body = {"payload_hash": a["payload_hash"], "content_version": a["content_version"]}
    assert (await client.post(f"/api/approvals/{a['id']}/approve", headers=BOB, json=body)).status_code == 404
