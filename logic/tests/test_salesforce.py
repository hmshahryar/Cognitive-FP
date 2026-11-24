"""Salesforce shares the CRM safety flow, and its Account and duplicate rules are explicit."""

import uuid

import httpx
import pytest
from fastapi import FastAPI

from cognitive_mesh.capabilities import AgentRole, capabilities_for
from cognitive_mesh.integrations import salesforce as sf
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.catalog import get_connector
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    IntegrationUnavailableError,
)
from cognitive_mesh.integrations.hubspot import InvalidCrmActionError
from cognitive_mesh.models import ConnectionStatus, IntegrationConnection
from cognitive_mesh.workflows.crm_commands import MULTIPLE_CRMS, parse_crm_command
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve
from tests.test_runner import status

ALICE = auth_headers("user_alice")
CONTACT_ID = "003000000000001AAA"
ACCOUNT_ID = "001000000000001AAA"
CREATE = "Add contact Ali Khan, ali@example.com, company Acme to Salesforce"
EXISTING = {
    "Id": CONTACT_ID, "FirstName": "Ali", "LastName": "Khan", "Email": "ali@example.com",
    "Phone": None, "Title": None, "Account": {"Name": "Existing Co"},
}
ACME = {"Id": ACCOUNT_ID, "Name": "Acme"}


async def connect(client, app, slug="salesforce", scopes=None) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=ALICE)).json()
    defaults = {
        "salesforce": ["api", "refresh_token"],
        "hubspot": ["oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"],
    }
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug=slug,
                composio_account_id=f"ca_{slug}_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes or defaults[slug],
            )
        )
        await s.commit()


async def say(client: httpx.AsyncClient, app: FastAPI, text: str) -> tuple[dict, str]:
    chat = (await client.post("/api/chats", headers=ALICE, json={})).json()["id"]
    task = (
        await client.post(f"/api/chats/{chat}/tasks", headers=ALICE, json={"request": text})
    ).json()
    await app.state.runner.run_once()
    messages = (await client.get(f"/api/chats/{chat}", headers=ALICE)).json()["messages"]
    return task, messages[-1]["content"]


# ---------- choosing the CRM ----------
@pytest.mark.parametrize(
    ("text", "provider"),
    [
        (CREATE, "salesforce"),
        ("add contact Ali Khan, ali@example.com to sales force", "salesforce"),
        ("add contact Ali Khan, ali@example.com to hubspot", "hubspot"),
        ("add contact Ali Khan, ali@example.com to zoho", "zoho"),
        ("add contact Ali Khan, ali@example.com", None),
        ("add contact Ali, ali@example.com, company Salesforce to hubspot", "hubspot"),
        ("add a note to contact ali@example.com: moving to Salesforce soon", None),
        ("add contact ali@example.com to hubspot and to salesforce", MULTIPLE_CRMS),
    ],
)
def test_only_the_named_destination_selects_a_crm(text: str, provider: str | None) -> None:
    command = parse_crm_command(text)
    assert command is not None and command.provider == provider


async def test_two_connected_crms_are_never_guessed(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    await connect(client, app, "hubspot")
    task, reply = await say(client, app, "add contact Ali Khan, ali@example.com")
    assert "Which CRM" in reply and await approvals_for(client, task) == []
    assert gateway.salesforce_searches == [] and gateway.hubspot_searches == []


async def test_only_connected_crm_is_used_when_none_is_named(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    task, _reply = await say(client, app, "add contact Ali Khan, ali@example.com")
    assert (await approvals_for(client, task))[0]["action_type"] == sf.CREATE_ACTION


async def test_named_crm_that_is_not_connected_never_falls_back(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, "hubspot")
    task, reply = await say(client, app, CREATE)
    assert "Salesforce isn't connected" in reply and await status(client, task) == "FAILED"
    assert gateway.hubspot_searches == []


# ---------- create and Account linking ----------
async def test_create_links_one_exact_account_after_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect(client, app)
    gateway.salesforce_accounts = [ACME, {"Id": "001000000000002AAA", "Name": "Acme Holdings"}]
    gateway.publish_response = {"id": CONTACT_ID, "success": True}
    task, _reply = await say(client, app, CREATE)
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == sf.CREATE_ACTION and gateway.publish_calls == []
    assert team.manager_calls == []
    assert a["payload"]["arguments"] == {
        "first_name": "Ali", "last_name": "Khan", "email": "ali@example.com",
        "account_id": ACCOUNT_ID,
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert [c["tool"] for c in gateway.publish_calls] == [sf.CREATE_TOOL]


async def test_unknown_company_is_disclosed_and_no_account_is_created(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    task, _reply = await say(client, app, CREATE)
    payload = (await approvals_for(client, task))[0]["payload"]
    assert "account_id" not in payload["arguments"] and "company" not in payload["properties"]
    assert "Acme" in payload["skipped"][0]


async def test_two_accounts_with_the_company_name_stop(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_accounts = [ACME, {"Id": "001000000000002AAA", "Name": "ACME"}]
    task, reply = await say(client, app, CREATE)
    assert "More than one" in reply and await approvals_for(client, task) == []


async def test_last_name_is_required(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    task, reply = await say(client, app, "add contact Ali, ali@example.com to salesforce")
    assert "last name" in reply and await approvals_for(client, task) == []


# ---------- duplicate protection ----------
async def test_existing_contact_only_fills_empty_fields(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_contacts = [EXISTING]
    text = "Add contact Ali Khan, ali@example.com, phone +92 300 1234567, company Acme to Salesforce"
    task, _reply = await say(client, app, text)
    a = (await approvals_for(client, task))[0]
    assert a["action_type"] == sf.UPDATE_ACTION and a["payload"]["mode"] == "fill"
    assert a["payload"]["arguments"] == {"contact_id": CONTACT_ID, "phone": "+92 300 1234567"}


async def test_repeat_create_does_not_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_contacts = [EXISTING]
    task, reply = await say(client, app, "add contact Ali Khan, ali@example.com to salesforce")
    assert "didn't create a duplicate" in reply and await approvals_for(client, task) == []


async def test_partial_email_match_is_not_the_same_person(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_contacts = [{**EXISTING, "Email": "mali@example.com"}]
    task, _reply = await say(client, app, "add contact Ali Khan, ali@example.com to salesforce")
    assert (await approvals_for(client, task))[0]["action_type"] == sf.CREATE_ACTION


async def test_duplicate_emails_stop_the_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_contacts = [EXISTING, {**EXISTING, "Id": "003000000000002AAA"}]
    task, reply = await say(client, app, CREATE)
    assert "More than one" in reply and await approvals_for(client, task) == []


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"results": []},
        {"records": "none"},
        {"records": [{"Id": "not-a-contact"}]},
        {"totalSize": 300, "records": [EXISTING]},
    ],
)
def test_unreadable_search_never_looks_like_no_match(response: dict) -> None:
    with pytest.raises(InvalidCrmActionError):
        sf.contacts_from(response)


async def test_failed_duplicate_check_never_creates(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(**_kwargs):  # type: ignore[no-untyped-def]
        raise IntegrationUnavailableError("down")

    await connect(client, app)
    monkeypatch.setattr(gateway, "execute_read_only", unavailable)
    task, reply = await say(client, app, CREATE)
    assert "couldn't read Salesforce" in reply and await approvals_for(client, task) == []


# ---------- notes and payload rules ----------
async def test_note_is_attached_to_the_found_contact(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app)
    gateway.salesforce_contacts = [EXISTING]
    gateway.publish_response = {"id": "002000000000001AAA"}
    task, _reply = await say(
        client, app, "add a note to contact ali@example.com in salesforce: called about pricing"
    )
    a = (await approvals_for(client, task))[0]
    assert a["payload"]["arguments"] == {
        "parent_id": CONTACT_ID, "title": sf.NOTE_TITLE, "body": "called about pricing",
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"


def test_tampered_account_is_rejected() -> None:
    payload = sf.build_create_payload(
        {"email": "ali@example.com", "lastname": "Khan", "company": "Acme"},
        {"id": ACCOUNT_ID, "name": "Acme"},
        (),
    )
    tampered = {**payload, "arguments": {**payload["arguments"], "account_id": "001000000000009AAA"}}
    with pytest.raises(InvalidCrmActionError):
        sf.validate_create(tampered)


def test_fill_cannot_overwrite_and_email_cannot_change() -> None:
    with pytest.raises(InvalidCrmActionError, match="empty fields"):
        sf.build_update_payload(
            contact_id=CONTACT_ID, email="ali@example.com", mode="fill", account=None, skipped=(),
            changes=[{"field": "phone", "previous": "123", "value": "456"}],
        )
    with pytest.raises(InvalidCrmActionError):
        sf.build_update_payload(
            contact_id=CONTACT_ID, email="ali@example.com", mode="change", account=None, skipped=(),
            changes=[{"field": "email", "previous": "a@b.co", "value": "x@example.com"}],
        )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"id": CONTACT_ID}, CONTACT_ID),
        ({"response_data": {"id": CONTACT_ID}}, CONTACT_ID),
        ({"id": "short"}, None),
        ({"id": 5}, None),
        ("x", None),
    ],
)
def test_result_id_is_strict(data: object, expected: str | None) -> None:
    assert sf.result_id(data) == expected


# ---------- least privilege ----------
def test_only_safe_salesforce_tools_are_allowed() -> None:
    writes = {a.tool_slug for a in ACTIONS.values() if a.toolkit == "salesforce"}
    assert writes == {sf.CREATE_TOOL, sf.UPDATE_TOOL, sf.NOTE_TOOL}
    assert writes <= APPROVED_ACTION_TOOLS and not writes & READ_ONLY_TOOLS
    registered = {c.tool_slug for c in capabilities_for(AgentRole.CRM) if c.toolkit == "salesforce"}
    assert registered == writes | {sf.SEARCH_TOOL, sf.ACCOUNT_SEARCH_TOOL}
    assert not any("SOQL" in t or "DELETE" in t for t in registered)


def test_connection_requests_no_full_access() -> None:
    spec = get_connector("salesforce")
    assert spec is not None and set(spec.allowed_scopes) == {"api", "refresh_token"}
    assert spec.limited_config_name


async def test_full_access_config_never_starts_a_connection(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    gateway.auth_scopes["salesforce"] = ("full", "refresh_token", "openid")
    response = await client.post("/api/connectors/salesforce/connect", headers=ALICE)
    assert response.status_code == 409 and gateway.started == []


async def test_grant_without_api_cannot_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect(client, app, scopes=["refresh_token"])
    task, _reply = await say(client, app, "add contact Ali Khan, ali@example.com to salesforce")
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"
