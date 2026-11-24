"""Zoho CRM shares the CRM safety flow, with its search-lag and automation rules explicit."""

import uuid

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.capabilities import AgentRole, capabilities_for
from cognitive_mesh.integrations import zoho
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.catalog import get_connector
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    ActionRejectedError,
    IntegrationUnavailableError,
)
from cognitive_mesh.integrations.hubspot import InvalidCrmActionError
from cognitive_mesh.models import ConnectionStatus, IntegrationConnection
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import status

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
SCOPES = [
    "ZohoCRM.modules.contacts.READ", "ZohoCRM.modules.contacts.CREATE",
    "ZohoCRM.modules.contacts.UPDATE", "ZohoCRM.modules.notes.CREATE",
    "ZohoSearch.securesearch.READ",
]
CREATE = "Add contact Ali Khan, ali@example.com to Zoho"
SUCCESS = {"data": [{"code": "SUCCESS", "status": "success", "details": {"id": "5725767000000524157"}}]}
EXISTING = {
    "id": "5725767000000524157", "First_Name": "Ali", "Last_Name": "Khan",
    "Email": "ali@example.com", "Phone": None, "Account_Name": {"name": "Existing Co", "id": "9"},
}


async def connect(client, app, headers=ALICE, scopes=None) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=headers)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug="zoho",
                composio_account_id=f"ca_zoho_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes or SCOPES,
            )
        )
        await s.commit()


async def say(
    client: httpx.AsyncClient, app: FastAPI, text: str, headers: dict = ALICE
) -> tuple[dict, str]:
    chat = (await client.post("/api/chats", headers=headers, json={})).json()["id"]
    task = (
        await client.post(f"/api/chats/{chat}/tasks", headers=headers, json={"request": text})
    ).json()
    await app.state.runner.run_once()
    messages = (await client.get(f"/api/chats/{chat}", headers=headers)).json()["messages"]
    return task, messages[-1]["content"]


async def test_create_needs_approval_and_skips_automations(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect(client, app)
    gateway.publish_response = SUCCESS
    task, _reply = await say(client, app, CREATE)
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == zoho.CREATE_ACTION and gateway.publish_calls == []
    assert team.manager_calls == [] and gateway.zoho_searches[0]["email"] == "ali@example.com"
    assert a["payload"]["arguments"] == {
        "first_name": "Ali", "last_name": "Khan", "email": "ali@example.com", "trigger": [],
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert [c["tool"] for c in gateway.publish_calls] == [zoho.CREATE_TOOL]


async def test_company_is_disclosed_and_not_written(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    task, _reply = await say(client, app, "Add contact Ali Khan, ali@example.com, company Acme to Zoho")
    payload = (await approvals_for(client, task))[0]["payload"]
    assert "account_name" not in payload["arguments"] and "Acme" in payload["skipped"][0]


async def test_last_name_is_required(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    task, reply = await say(client, app, "add contact Ali, ali@example.com to zoho")
    assert "last name" in reply and await approvals_for(client, task) == []


async def test_search_lag_cannot_create_a_duplicate(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.publish_response = SUCCESS
    task, _reply = await say(client, app, CREATE)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    second, reply = await say(client, app, CREATE)  # search is still empty, as with index lag
    assert "didn't create a duplicate" in reply and await approvals_for(client, second) == []
    assert len(gateway.publish_calls) == 1


async def test_pending_approval_blocks_a_second_card(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    await say(client, app, CREATE)
    second, reply = await say(client, app, CREATE)
    assert "waiting for your approval" in reply and await approvals_for(client, second) == []


async def test_failed_create_can_be_tried_again(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.publish_error = ActionRejectedError("400 invalid")
    task, _reply = await say(client, app, CREATE)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    gateway.publish_error = None
    second, _reply = await say(client, app, CREATE)
    assert (await approvals_for(client, second))[0]["action_type"] == zoho.CREATE_ACTION


async def test_another_workspace_is_not_blocked_by_the_lag_guard(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    await connect(client, app, BOB)
    await say(client, app, CREATE)
    second, _reply = await say(client, app, CREATE, BOB)
    rows = (await client.get("/api/approvals", headers=BOB, params={"task_id": second["id"]})).json()
    assert rows[0]["action_type"] == zoho.CREATE_ACTION


async def test_existing_contact_only_fills_empty_fields(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.zoho_contacts = [EXISTING]
    task, _reply = await say(
        client, app, "Add contact Ali Khan, ali@example.com, phone +92 300 1234567 to Zoho"
    )
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == zoho.UPDATE_ACTION and a["payload"]["mode"] == "fill"
    assert a["payload"]["arguments"] == {
        "module_api_name": "Contacts",
        "data": [{"id": EXISTING["id"], "Phone": "+92 300 1234567"}],
        "trigger": [],
    }


async def test_secondary_email_counts_as_the_same_person(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.zoho_contacts = [{**EXISTING, "Email": "other@example.com", "Secondary_Email": "ali@example.com"}]
    task, reply = await say(client, app, CREATE)
    assert "didn't create a duplicate" in reply and await approvals_for(client, task) == []


async def test_two_matches_stop_the_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.zoho_contacts = [EXISTING, {**EXISTING, "id": "5725767000000524158"}]
    task, reply = await say(client, app, CREATE)
    assert "More than one" in reply and await approvals_for(client, task) == []


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"results": []},
        {"data": "none"},
        {"data": [{"id": "not-a-number"}]},
        {"data": [EXISTING], "info": {"more_records": True}},
    ],
)
def test_unreadable_search_never_looks_like_no_match(response: dict) -> None:
    with pytest.raises(InvalidCrmActionError):
        zoho.contacts_from(response)


async def test_failed_duplicate_check_never_creates(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(**_kwargs):  # type: ignore[no-untyped-def]
        raise IntegrationUnavailableError("down")

    await connect(client, app)
    monkeypatch.setattr(gateway, "execute_read_only", unavailable)
    task, reply = await say(client, app, CREATE)
    assert "couldn't read Zoho CRM" in reply and await approvals_for(client, task) == []


async def test_note_is_attached_to_the_found_contact(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.zoho_contacts = [EXISTING]
    gateway.publish_response = SUCCESS
    task, _reply = await say(
        client, app, "add a note to contact ali@example.com in zoho: called about pricing"
    )
    a = (await approvals_for(client, task))[0]
    assert a["payload"]["arguments"] == {
        "module_api_name": "Contacts", "record_id": EXISTING["id"],
        "Note_Title": zoho.NOTE_TITLE, "Note_Content": "called about pricing",
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (SUCCESS, "5725767000000524157"),
        ({"response_data": SUCCESS}, "5725767000000524157"),
        ({"data": [{"status": "error", "details": {"id": "1"}}]}, None),
        ({"data": [*SUCCESS["data"], *SUCCESS["data"]]}, None),
        ({"data": []}, None),
        ({"id": "1"}, None),
    ],
)
def test_result_id_requires_one_clear_success(data: dict, expected: str | None) -> None:
    assert zoho.result_id(data) == expected


async def test_partial_zoho_failure_is_never_reported_as_done(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.publish_response = {"data": [{"status": "error", "code": "INVALID_DATA", "details": {}}]}
    task, _reply = await say(client, app, CREATE)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"


def test_tampered_update_is_rejected() -> None:
    payload = zoho.build_update_payload(
        contact_id="501", email="ali@example.com", mode="change", skipped=(),
        changes=[{"field": "phone", "previous": "", "value": "123456"}],
    )
    tampered = {
        **payload,
        "arguments": {**payload["arguments"], "data": [{"id": "999", "Phone": "123456"}]},
    }
    with pytest.raises(InvalidCrmActionError):
        zoho.validate_update(tampered)


def test_update_cannot_target_another_module_or_change_email() -> None:
    payload = zoho.build_update_payload(
        contact_id="501", email="ali@example.com", mode="change", skipped=(),
        changes=[{"field": "phone", "previous": "", "value": "123456"}],
    )
    with pytest.raises(InvalidCrmActionError):
        zoho.validate_update(
            {**payload, "arguments": {**payload["arguments"], "module_api_name": "Deals"}}
        )
    with pytest.raises(InvalidCrmActionError):
        zoho.build_update_payload(
            contact_id="501", email="ali@example.com", mode="change", skipped=(),
            changes=[{"field": "email", "previous": "a@b.co", "value": "x@example.com"}],
        )


def test_only_safe_zoho_tools_are_allowed() -> None:
    writes = {a.tool_slug for a in ACTIONS.values() if a.toolkit == "zoho"}
    assert writes == {zoho.CREATE_TOOL, zoho.UPDATE_TOOL, zoho.NOTE_TOOL}
    assert writes <= APPROVED_ACTION_TOOLS and not writes & READ_ONLY_TOOLS
    registered = {c.tool_slug for c in capabilities_for(AgentRole.CRM) if c.toolkit == "zoho"}
    assert registered == writes | {zoho.SEARCH_TOOL}
    assert not any("DELETE" in t or "COQL" in t for t in registered)


def test_connection_requests_contacts_and_notes_only() -> None:
    spec = get_connector("zoho")
    assert spec is not None and set(spec.allowed_scopes) == set(SCOPES)
    assert not any(".ALL" in scope or "settings" in scope for scope in spec.allowed_scopes)


async def test_default_broad_config_never_starts_a_connection(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    gateway.auth_scopes["zoho"] = ("ZohoCRM.modules.ALL", "ZohoCRM.settings.ALL", "ZohoCRM.users.ALL")
    response = await client.post("/api/connectors/zoho/connect", headers=ALICE)
    assert response.status_code == 409 and gateway.started == []


async def test_read_only_grant_cannot_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, scopes=["ZohoCRM.modules.contacts.READ"])
    task, _reply = await say(client, app, CREATE)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"
