"""Phase 2 acceptance: the Supervisor and active items, using the real failed conversation."""

import uuid

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.integrations import google_sheets as gs
from cognitive_mesh.models import Publication
from cognitive_mesh.services.active_items import (
    ActiveEvent,
    ActiveItems,
    ActiveSheet,
    load_active_items,
)
from cognitive_mesh.workflows.command_router import route_command
from cognitive_mesh.workflows.manager_routing import route_from_decision
from cognitive_mesh.workflows.sheet_commands import DEFAULT_SHEET_NAME, parse_sheet_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import status
from tests.test_sheets import CREATED, SHEET_ID, connect_sheets, last_reply, new_chat, say

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
FIRST = "make a google sheet and write my name in it  shahryar"
SECOND = "make two coulumn one for mention name and one to mention  Shahryar"
ACTIVE = ActiveItems(sheet=ActiveSheet(SHEET_ID, "Customers"))


async def _approve_and_run(client: httpx.AsyncClient, app: FastAPI, task: dict) -> dict:
    approval = (await approvals_for(client, task))[0]
    await approve(client, approval)
    await app.state.runner.run_once()
    return approval


# ---------- the exact conversation, with the AI unavailable ----------
async def test_failed_sheet_conversation_now_works_without_ai(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    team.manager_error = RuntimeError("Gemini unavailable")
    gateway.publish_response = CREATED
    await connect_sheets(client, app)
    chat = await new_chat(client)

    first = await say(client, app, chat, FIRST)
    created = await _approve_and_run(client, app, first)
    assert created["payload"]["spreadsheet"]["name"] == DEFAULT_SHEET_NAME
    assert created["payload"]["rows"] == [["shahryar"]]
    assert gateway.publish_calls[1]["arguments"]["values"] == [["shahryar"]]

    gateway.publish_calls.clear()
    second = await say(client, app, chat, SECOND)
    assert await status(client, second) == "AWAITING_APPROVAL" and gateway.publish_calls == []
    follow_up = (await approvals_for(client, second))[0]
    assert follow_up["action_type"] == gs.APPEND_ACTION
    assert follow_up["payload"]["spreadsheet"]["id"] == SHEET_ID
    assert follow_up["payload"]["spreadsheet"]["name"] == DEFAULT_SHEET_NAME
    assert follow_up["payload"]["rows"] == [["Name", "Shahryar"]]
    assert team.manager_calls == []


def test_the_word_name_is_not_taken_as_the_title() -> None:
    command = parse_sheet_command(FIRST)
    assert command is not None and command.name == DEFAULT_SHEET_NAME
    assert command.rows == (("shahryar",),) and command.missing is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("create a sheet called Leads", "Leads"),
        ("make a sheet named Budget 2027", "Budget 2027"),
        ("create a spreadsheet name Sales", "Sales"),
        ("make a sheet with name Team", "Team"),
    ],
)
def test_real_sheet_names_still_work(text: str, expected: str) -> None:
    command = parse_sheet_command(text)
    assert command is not None and command.name == expected


@pytest.mark.parametrize(
    "text",
    [
        "make a google sheet and write my name in it",
        "make a sheet and put the customer data there",
    ],
)
def test_unclear_create_is_left_for_the_supervisor(text: str) -> None:
    assert parse_sheet_command(text) is None
    assert route_command(text).action != "sheets"


# ---------- Supervisor decisions ----------
async def test_supervisor_receives_active_sheet_and_uses_it(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_response = CREATED
    await connect_sheets(client, app)
    chat = await new_chat(client)
    first = await say(client, app, chat, "create a sheet called Customers with columns Name")
    await _approve_and_run(client, app, first)

    message = "pls note ali there"
    team.manager_decision = ManagerDecision(
        target="sheets", action="sheet_append", rows=[["ali"]], use_active_item=True
    )
    task = await say(client, app, chat, message)
    assert "Active sheet: Customers" in team.manager_contexts[-1]
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["spreadsheet"]["id"] == SHEET_ID
    assert approval["payload"]["rows"] == [["ali"]]


def test_supervisor_cannot_use_an_active_sheet_that_does_not_exist() -> None:
    decision = ManagerDecision(
        target="sheets", action="sheet_append", rows=[["ali"]], use_active_item=True
    )
    route, _question = route_from_decision(decision, "note ali there", ActiveItems())
    assert route.sheet is not None and not route.sheet.use_last and route.sheet.missing == "target"


def test_supervisor_cannot_invent_values_for_a_new_sheet() -> None:
    decision = ManagerDecision(target="sheets", action="sheet_create", rows=[["Invented"]])
    route, _question = route_from_decision(decision, "make me a sheet with my name")
    assert route.sheet is not None and route.sheet.rows == () and route.sheet.missing == "name"


def test_supervisor_cannot_invent_values_for_the_active_sheet() -> None:
    decision = ManagerDecision(
        target="sheets", action="sheet_append", rows=[["ali", "9999"]], use_active_item=True
    )
    route, _question = route_from_decision(decision, "add ali to it", ACTIVE)
    assert route.sheet is not None and route.sheet.rows == () and route.sheet.missing == "rows"


def test_active_event_identifies_a_follow_up_cancel() -> None:
    items = ActiveItems(event=ActiveEvent("Team sync", "2099-01-05"))
    decision = ManagerDecision(target="calendar", action="cancel", use_active_item=True)
    route, _question = route_from_decision(decision, "cancel it", items)
    assert route.calendar is not None
    assert (route.calendar.query, route.calendar.source_date) == ("Team sync", "2099-01-05")


def test_active_event_is_ignored_unless_the_supervisor_refers_to_it() -> None:
    items = ActiveItems(event=ActiveEvent("Team sync", "2099-01-05"))
    decision = ManagerDecision(target="calendar", action="cancel")
    route, _question = route_from_decision(decision, "cancel something", items)
    assert route.calendar is not None and route.calendar.missing == "event"


# ---------- active items ----------
async def test_follow_up_with_no_active_sheet_asks_which_one(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_sheets(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, SECOND)
    assert "Which sheet" in await last_reply(client, chat)
    assert await approvals_for(client, task) == [] and gateway.publish_calls == []


async def test_unapproved_sheet_never_becomes_active(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_sheets(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, FIRST)  # paused for approval, so nothing was created
    me = (await client.get("/api/me", headers=ALICE)).json()
    async with app.state.db.sessionmaker() as s:
        active = await load_active_items(s, uuid.UUID(me["workspace"]["id"]), uuid.UUID(chat))
    assert active.sheet is None


async def test_active_items_are_isolated_by_chat_and_workspace(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    await connect_sheets(client, app)
    chat = await new_chat(client)
    await _approve_and_run(client, app, await say(client, app, chat, FIRST))
    alice = (await client.get("/api/me", headers=ALICE)).json()
    bob = (await client.get("/api/me", headers=BOB)).json()
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Publication, uuid.uuid4()) is None
        mine = await load_active_items(s, uuid.UUID(alice["workspace"]["id"]), uuid.UUID(chat))
        other_chat = await load_active_items(s, uuid.UUID(alice["workspace"]["id"]), uuid.uuid4())
        other_user = await load_active_items(s, uuid.UUID(bob["workspace"]["id"]), uuid.UUID(chat))
    assert mine.sheet is not None and mine.sheet.id == SHEET_ID
    assert other_chat.sheet is None and other_user.sheet is None
