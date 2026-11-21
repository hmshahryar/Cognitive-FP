"""The Manager understands natural requests; code keeps control of anything risky."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.agents.llm import build_chat_model
from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.config import Settings
from cognitive_mesh.workflows.calendar_commands import parse_calendar_mark
from cognitive_mesh.workflows.manager_routing import grounded_date, route_from_decision
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam, draft
from tests.test_calendar import connect_calendar, last_reply, new_chat, say
from tests.test_gmail import connect_gmail
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import connect_linkedin, status

ALICE = auth_headers("user_alice")
FIRST = "make a calender and mark the 24 november as finaal dat of final ear project"
SECOND = "schedule on google calender and mark the 24 november as final year project"
MARK = ManagerDecision(
    target="calendar", action="create", title="Final Date of Final Year Project",
    date_phrase="24 november", all_day=True,
)


@pytest.mark.parametrize("message", [FIRST, SECOND])
async def test_your_exact_messages_reach_a_calendar_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam, message: str
) -> None:
    team.manager_decision = MARK
    await connect_calendar(client, app)
    task = await say(client, app, await new_chat(client), message)

    assert await status(client, task) == "AWAITING_APPROVAL" and team.manager_calls == [message]
    approval = (await approvals_for(client, task))[0]
    args = approval["payload"]["arguments"]
    assert approval["action_type"] == "calendar.create_event"
    assert args["summary"] == "Final Date of Final Year Project"
    assert args["start_date"].endswith("-11-24") and args["end_date"].endswith("-11-25")
    assert args["attendees"] == [] and args["send_updates"] == "none"
    assert args["create_meeting_room"] is False and args["transparency"] == "transparent"
    assert "start_datetime" not in args and gateway.publish_calls == []

    row = (await client.get(f"/api/tasks/{task['id']}", headers=ALICE)).json()
    assert row["route_label"] == "Calendar agent · Schedule event"
    events = (await client.get(f"/api/tasks/{task['id']}/events", headers=ALICE)).json()
    selected = next(e for e in events if e["event"] == "route.selected")
    assert selected["detail"]["routed_by"] == "manager"

    gateway.publish_response = {"response_data": {"id": "evt1"}}
    await approve(client, approval)
    await app.state.runner.run_once()
    assert gateway.publish_calls[0]["tool"] == "GOOGLECALENDAR_CREATE_EVENT"
    assert gateway.publish_calls[0]["arguments"] == args


async def test_clear_commands_do_not_spend_a_manager_call(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_calendar(client, app)
    await say(client, app, await new_chat(client), "show my calendar on 2099-01-05")
    assert team.manager_calls == []


async def test_offline_fallback_still_routes_when_the_model_fails(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.manager_error = RuntimeError("quota")
    await connect_calendar(client, app)
    task = await say(client, app, await new_chat(client), SECOND)
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["arguments"]["summary"] == "Final year project"
    assert approval["payload"]["event"]["all_day"] is True


async def test_slow_manager_cannot_block_the_chat(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs(_message: str) -> ManagerDecision:
        await asyncio.sleep(30)
        raise AssertionError("should have timed out")

    monkeypatch.setattr("cognitive_mesh.services.conversation.MANAGER_TIMEOUT_SECONDS", -1.95)
    team.decide = hangs  # type: ignore[method-assign]
    await connect_calendar(client, app)
    task = await say(client, app, await new_chat(client), SECOND)
    assert await status(client, task) == "AWAITING_APPROVAL"


def test_model_client_does_not_retry_silently() -> None:
    settings = Settings(_env_file=None, google_api_key="test-key")
    model = build_chat_model(settings, thinking_level="low", timeout_seconds=20)
    assert model.max_retries == 1 and model.timeout == 20


async def test_model_failure_on_other_text_gets_a_clear_reply(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.manager_error = RuntimeError("quota")
    chat = await new_chat(client)
    task = await say(client, app, chat, "hello there")
    assert await status(client, task) == "COMPLETED"
    assert "Right now" in await last_reply(client, chat)


async def test_manager_can_ask_one_question(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.manager_decision = ManagerDecision(target="clarify", question="Which day do you mean?")
    chat = await new_chat(client)
    await say(client, app, chat, "put it on there")
    assert await last_reply(client, chat) == "Which day do you mean?"


async def test_missing_date_from_manager_continues_as_a_question(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.manager_decision = ManagerDecision(
        target="calendar", action="create", title="Project deadline", all_day=True
    )
    await connect_calendar(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "put my project deadline on the calender")
    assert "What day" in await last_reply(client, chat)
    task = await say(client, app, chat, "2099-01-05")
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["arguments"]["start_date"] == "2099-01-05"


# ---------- the model cannot introduce risky details ----------
@pytest.mark.parametrize(
    ("phrase", "message", "expected"),
    [
        ("24 november", "mark 24 novembr as due", "24 november"),
        ("tomorrow", "meeting tommorow please", "tomorrow"),
        ("25 november", "mark 24 november as due", None),
        ("tomorrow", "mark this as due", None),
        ("24 november 2030", "mark 24 november as due", None),
    ],
)
def test_invented_dates_are_discarded(phrase: str, message: str, expected: str | None) -> None:
    assert grounded_date(phrase, message) == expected


def test_model_cannot_add_a_date_guest_or_time() -> None:
    decision = ManagerDecision(
        target="calendar", action="create", title="Sync", date_phrase="31 december"
    )
    route, _question = route_from_decision(decision, "set up a sync for my team sometime")
    assert route.calendar is not None
    assert route.calendar.date_text is None and route.calendar.attendees == ()
    assert route.calendar.start is None and route.calendar.missing == "date"


def test_email_recipient_comes_from_the_message_only() -> None:
    decision = ManagerDecision(target="gmail", action="write_email")
    route, _question = route_from_decision(decision, "pls mail the team about friday")
    assert route.email is not None and route.email.to == () and route.email.missing == "recipient"


def test_linkedin_platform_must_be_named_by_the_user() -> None:
    decision = ManagerDecision(target="linkedin", action="write_post")
    route, _question = route_from_decision(decision, "make something nice about ai")
    assert route.platforms == ()


def test_invalid_target_action_pair_is_unsupported() -> None:
    decision = ManagerDecision(target="calendar", action="write_post")
    assert route_from_decision(decision, "x")[0].action == "unsupported"


def test_manager_has_no_exact_publish_path() -> None:
    assert "post_as_is" not in ManagerDecision.model_json_schema()["properties"]["action"]["enum"]


def test_fallback_needs_calendar_intent_and_a_date() -> None:
    assert parse_calendar_mark("mark the 24 november as done") is None
    assert parse_calendar_mark("mark my calendar as busy") is None
    marked = parse_calendar_mark(SECOND)
    assert marked is not None and marked.all_day and marked.date_text == "24 november"


async def test_manager_routes_gmail_and_linkedin_to_their_specialists(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_gmail(client, app)
    await connect_linkedin(client, app)
    team.manager_decision = ManagerDecision(target="gmail", action="write_email")
    email = await say(client, app, await new_chat(client), "pls mail ann@example.com to say thanks")
    approval = (await approvals_for(client, email))[0]
    assert approval["action_type"] == "gmail.send_email"
    assert approval["payload"]["arguments"]["recipient_email"] == "ann@example.com"

    only = draft("Consistency compounds over a career.")
    only.source_ids = []
    team.drafts = [only]
    team.manager_decision = ManagerDecision(target="linkedin", action="write_post")
    post = await say(client, app, await new_chat(client), "make something nice for linkedin on growth")
    assert (await approvals_for(client, post))[0]["action_type"] == "linkedin.publish_post"
