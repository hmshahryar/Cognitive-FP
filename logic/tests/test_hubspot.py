"""HubSpot: search first, never duplicate, never silently overwrite, always approve writes."""

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from cognitive_mesh.agents.schemas import ManagerDecision
from cognitive_mesh.capabilities import AgentRole, capabilities_for
from cognitive_mesh.integrations import hubspot as hs
from cognitive_mesh.integrations.actions import ACTIONS
from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    ActionOutcomeUnknownError,
    ActionRejectedError,
    IntegrationUnavailableError,
)
from cognitive_mesh.models import Approval, ConnectionStatus, IntegrationConnection
from cognitive_mesh.workflows.command_router import route_command
from cognitive_mesh.workflows.crm_commands import continue_crm, parse_crm_command
from cognitive_mesh.workflows.engine import ledger
from cognitive_mesh.workflows.manager_routing import route_from_decision
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve, fresh_runner
from tests.test_runner import status

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
CREATE = "Add contact Ali Khan, ali@example.com, phone +92 300 1234567, company Acme to HubSpot"
EXISTING = {
    "id": "501",
    "properties": {
        "email": "ali@example.com", "firstname": "Ali", "lastname": "",
        "phone": "", "company": "Existing Co",
    },
}


async def connect_hubspot(client, app, headers=ALICE, scopes=None) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=headers)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug="hubspot",
                composio_account_id=f"ca_hub_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=scopes or ["oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"],
            )
        )
        await s.commit()


async def new_chat(client: httpx.AsyncClient) -> str:
    return (await client.post("/api/chats", headers=ALICE, json={})).json()["id"]


async def say(client: httpx.AsyncClient, app: FastAPI, chat: str, text: str) -> dict:
    url = f"/api/chats/{chat}/tasks"
    task = (await client.post(url, headers=ALICE, json={"request": text})).json()
    await app.state.runner.run_once()
    return task


async def last_reply(client: httpx.AsyncClient, chat: str) -> str:
    return (await client.get(f"/api/chats/{chat}", headers=ALICE)).json()["messages"][-1]["content"]


async def paused(client: httpx.AsyncClient, app: FastAPI, text: str) -> tuple[dict, dict]:
    await connect_hubspot(client, app)
    task = await say(client, app, await new_chat(client), text)
    assert await status(client, task) == "AWAITING_APPROVAL"
    return task, (await approvals_for(client, task))[0]


# ---------- command parsing ----------
def test_create_keeps_exact_details() -> None:
    command = parse_crm_command(CREATE)
    assert command is not None and command.kind == "create" and command.email == "ali@example.com"
    assert dict(command.fields) == {
        "email": "ali@example.com", "firstname": "Ali", "lastname": "Khan",
        "phone": "+92 300 1234567", "company": "Acme",
    }


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("find contact ali@example.com", "find"),
        ("search for lead Ali in hubspot", "find"),
        ("update contact ali@example.com phone +92 311 7654321", "update"),
        ("add a note to contact ali@example.com: called about pricing", "note"),
    ],
)
def test_crm_commands(text: str, kind: str) -> None:
    command = parse_crm_command(text)
    assert command is not None and command.kind == kind and command.missing is None


@pytest.mark.parametrize(
    "text",
    [
        "Send an email to ali@example.com about the contact form",
        "Write a LinkedIn post about customers",
        "add customer Ali to sheet Customers",
        "add contact ali@example.com to my sheet",
        "Schedule a meeting tomorrow at 3pm",
        "remember that Ali is ali@example.com",
    ],
)
def test_other_commands_are_not_crm(text: str) -> None:
    assert route_command(text).action != "crm"


def test_missing_email_continues_in_chat() -> None:
    command = parse_crm_command("add contact Ali Khan to hubspot")
    assert command is not None and command.missing == "email"
    answered = continue_crm(command, "ALI@Example.com")
    assert answered.email == "ali@example.com" and answered.missing is None
    assert ("email", "ali@example.com") in answered.fields


# ---------- payload rules ----------
def test_only_safe_hubspot_tools_are_allowed() -> None:
    tools = {a.tool_slug for a in ACTIONS.values() if a.toolkit == "hubspot"}
    assert tools == {hs.CREATE_TOOL, hs.UPDATE_TOOL, hs.NOTE_TOOL}
    assert tools <= APPROVED_ACTION_TOOLS and not tools & READ_ONLY_TOOLS
    assert not any(
        t.startswith("HUBSPOT") and any(word in t for word in ("DELETE", "MERGE", "ARCHIVE", "GDPR"))
        for t in APPROVED_ACTION_TOOLS | READ_ONLY_TOOLS
    )
    crm_tools = {c.tool_slug for c in capabilities_for(AgentRole.CRM) if c.toolkit == "hubspot"}
    assert crm_tools == tools | {hs.SEARCH_TOOL}


def test_fill_mode_cannot_overwrite_existing_data() -> None:
    with pytest.raises(hs.InvalidCrmActionError, match="empty fields"):
        hs.build_update_payload(
            contact_id="501", email="ali@example.com", mode="fill",
            changes=[{"field": "company", "previous": "Existing Co", "value": "Other"}],
        )


def test_email_cannot_be_changed_by_an_update() -> None:
    with pytest.raises(hs.InvalidCrmActionError):
        hs.build_update_payload(
            contact_id="501", email="ali@example.com", mode="change",
            changes=[{"field": "email", "previous": "ali@example.com", "value": "x@example.com"}],
        )


def test_unknown_contact_fields_are_rejected() -> None:
    with pytest.raises(hs.InvalidCrmActionError):
        hs.build_create_payload({"email": "ali@example.com", "hubspot_owner_id": "1"})


def test_note_text_is_escaped_and_linked_with_the_official_type() -> None:
    payload = hs.build_note_payload(
        contact_id="501", email="ali@example.com", note="<img src=x onerror=alert(1)>",
        timestamp="2026-11-22T10:00:00Z",
    )
    assert "<img" not in payload["arguments"]["hs_note_body"]
    assert payload["arguments"]["associations"][0]["types"][0]["associationTypeId"] == 202
    tampered = {**payload, "note": "changed"}
    with pytest.raises(hs.InvalidCrmActionError):
        hs.validate_note(tampered)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"id": "501"}, "501"),
        ({"id": 501}, "501"),
        ({"response_data": {"id": "501"}}, "501"),
        ({"id": "not-a-number"}, None),
        ({"id": True}, None),
        ("501", None),
    ],
)
def test_result_id_is_strict(data: object, expected: str | None) -> None:
    assert hs.result_id(data) == expected


# ---------- duplicate protection ----------
async def test_new_contact_searches_first_and_needs_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_response = {"id": "777"}
    task, a = await paused(client, app, CREATE)
    search = gateway.hubspot_searches[0]["filterGroups"][0]["filters"][0]
    assert search == {"propertyName": "email", "operator": "EQ", "value": "ali@example.com"}
    assert a["action_type"] == hs.CREATE_ACTION and gateway.publish_calls == []
    assert team.manager_calls == []

    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED" and len(gateway.publish_calls) == 1
    call = gateway.publish_calls[0]
    assert call["tool"] == hs.CREATE_TOOL and call["arguments"]["company"] == "Acme"


async def test_existing_contact_is_never_duplicated_or_overwritten(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [EXISTING]
    gateway.publish_response = {"id": "501"}
    _task, a = await paused(client, app, CREATE)
    assert a["action_type"] == hs.UPDATE_ACTION and a["payload"]["mode"] == "fill"
    assert a["payload"]["arguments"]["contactId"] == "501"
    assert a["payload"]["arguments"]["properties"] == {
        "lastname": "Khan", "phone": "+92 300 1234567"
    }
    await approve(client, a)
    await app.state.runner.run_once()
    assert [c["tool"] for c in gateway.publish_calls] == [hs.UPDATE_TOOL]


async def test_nothing_new_means_no_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [
        {"id": "501", "properties": {"email": "ali@example.com", "firstname": "Sara"}}
    ]
    await connect_hubspot(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, "add contact Ali, ali@example.com to hubspot")
    assert await status(client, task) == "COMPLETED"
    reply = await last_reply(client, chat)
    assert "didn't create a duplicate" in reply and "First name" in reply
    assert await approvals_for(client, task) == [] and gateway.publish_calls == []


async def test_similar_email_is_not_treated_as_the_same_person(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [{"id": "9", "properties": {"email": "ali2@example.com"}}]
    _task, a = await paused(client, app, CREATE)
    assert a["action_type"] == hs.CREATE_ACTION


async def test_two_matching_contacts_stop_the_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [EXISTING, {**EXISTING, "id": "502"}]
    await connect_hubspot(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, CREATE)
    assert "More than one" in await last_reply(client, chat)
    assert await approvals_for(client, task) == []


async def test_failed_duplicate_check_never_creates(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(**_kwargs):  # type: ignore[no-untyped-def]
        raise IntegrationUnavailableError("down")

    await connect_hubspot(client, app)
    monkeypatch.setattr(gateway, "execute_read_only", unavailable)
    chat = await new_chat(client)
    task = await say(client, app, chat, CREATE)
    assert "couldn't read HubSpot" in await last_reply(client, chat)
    assert await approvals_for(client, task) == [] and gateway.publish_calls == []


async def test_hubspot_duplicate_rejection_at_send_is_a_clean_failure(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.publish_error = ActionRejectedError("409 contact already exists")
    task, a = await paused(client, app, CREATE)
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED" and len(gateway.publish_calls) == 1


# ---------- explicit changes and notes ----------
async def test_explicit_update_shows_old_and_new_values(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [EXISTING]
    _task, a = await paused(client, app, "update contact ali@example.com company New Co")
    assert a["payload"]["mode"] == "change"
    assert a["payload"]["changes"] == [
        {"field": "company", "previous": "Existing Co", "value": "New Co"}
    ]


async def test_update_of_unknown_contact_stops(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_hubspot(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, "update contact ali@example.com company New Co")
    assert "couldn't find" in await last_reply(client, chat)
    assert await approvals_for(client, task) == []


async def test_note_is_attached_to_the_found_contact(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.hubspot_contacts = [EXISTING]
    gateway.publish_response = {"id": "9001"}
    task, a = await paused(client, app, "add a note to contact ali@example.com: called about pricing")
    assert a["payload"]["note"] == "called about pricing"
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"
    assert gateway.publish_calls[0]["arguments"]["associations"][0]["to"]["id"] == "501"


async def test_find_answers_without_approval_or_ai(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.hubspot_contacts = [EXISTING]
    await connect_hubspot(client, app)
    chat = await new_chat(client)
    task = await say(client, app, chat, "find contact ali@example.com")
    assert "Existing Co" in await last_reply(client, chat)
    assert await approvals_for(client, task) == [] and team.manager_calls == []


async def test_missing_email_then_reply_reaches_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_hubspot(client, app)
    chat = await new_chat(client)
    await say(client, app, chat, "add contact Ali Khan to hubspot")
    assert "email address" in await last_reply(client, chat)
    task = await say(client, app, chat, "ali@example.com")
    approval = (await approvals_for(client, task))[0]
    assert approval["payload"]["properties"]["firstname"] == "Ali"


# ---------- Supervisor ----------
def test_supervisor_cannot_invent_contact_details() -> None:
    decision = ManagerDecision(
        target="crm", action="crm_create", contact_email="made.up@example.com",
        first_name="Ali", phone="+1 555 0000", company="Invented Inc",
    )
    route, _question = route_from_decision(decision, "pls put ali in our crm")
    assert route.crm is not None and route.crm.email is None and route.crm.missing == "email"
    assert dict(route.crm.fields) == {"firstname": "ali"} or dict(route.crm.fields) == {"firstname": "Ali"}


def test_supervisor_routes_grounded_details() -> None:
    decision = ManagerDecision(
        target="crm", action="crm_create", contact_email="ali@example.com", first_name="Ali",
    )
    route, _question = route_from_decision(decision, "pls put Ali ali@example.com in our crm")
    assert route.action == "crm" and route.crm is not None and route.crm.missing is None


# ---------- safety ----------
async def test_rejection_never_writes(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    _task, a = await paused(client, app, CREATE)
    await client.post(f"/api/approvals/{a['id']}/reject", headers=ALICE, json={})
    await app.state.runner.run_once()
    assert gateway.publish_calls == []


async def test_changed_contact_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused(client, app, CREATE)
    await approve(client, a)
    changed = {
        **a["payload"],
        "arguments": {**a["payload"]["arguments"], "company": "Attacker"},
        "properties": {**a["payload"]["properties"], "company": "Attacker"},
    }
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Approval).where(Approval.id == uuid.UUID(a["id"])).values(payload=changed))
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_read_only_grant_cannot_write(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_hubspot(client, app, scopes=["oauth", "crm.objects.contacts.read"])
    task = await say(client, app, await new_chat(client), CREATE)
    await approve(client, (await approvals_for(client, task))[0])
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_not_connected_gives_guidance(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    chat = await new_chat(client)
    task = await say(client, app, chat, CREATE)
    assert await status(client, task) == "FAILED"
    assert "HubSpot isn't connected" in await last_reply(client, chat)
    assert gateway.hubspot_searches == []


async def test_unknown_result_is_never_repeated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    gateway.publish_error = ActionOutcomeUnknownError("timeout")
    task, a = await paused(client, app, CREATE)
    await approve(client, a)
    await app.state.runner.run_once()
    await app.state.runner.close()
    restarted = fresh_runner(app, gateway, team)
    try:
        await restarted.recover()
    finally:
        await restarted.close()
    assert len(gateway.publish_calls) == 1 and await status(client, task) == "FAILED"


async def test_approval_is_workspace_isolated(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    _task, a = await paused(client, app, CREATE)
    await client.get("/api/me", headers=BOB)
    body = {"payload_hash": a["payload_hash"], "content_version": a["content_version"]}
    assert (await client.post(f"/api/approvals/{a['id']}/approve", headers=BOB, json=body)).status_code == 404


async def test_contact_id_flows_safely_through_a_workflow_plan(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_hubspot(client, app)
    gateway.publish_response = {"id": "777"}
    ref = {"$ref": "steps.contact.external_id"}
    note = hs.build_note_payload(
        contact_id="1", email="ali@example.com", note="New inquiry",
        timestamp="2026-11-22T10:00:00Z",
    )
    note["contact"]["id"] = ref
    note["arguments"]["associations"][0]["to"]["id"] = ref
    plan = ledger.build_plan(
        playbook="crm_test", version=1, title="Add contact and note",
        steps=[
            {"key": "contact", "action_type": hs.CREATE_ACTION,
             "payload": hs.build_create_payload({"email": "ali@example.com"})},
            {"key": "note", "action_type": hs.NOTE_ACTION, "payload": note,
             "depends_on": ["contact"]},
        ],
    )
    me = (await client.get("/api/me", headers=ALICE)).json()
    async with app.state.db.sessionmaker() as s:
        run = await ledger.create_run(
            s, app.state.approval_signer, workspace_id=uuid.UUID(me["workspace"]["id"]),
            created_by=uuid.UUID(me["user"]["id"]), plan=plan,
        )
        run_id, plan_hash = run.id, run.plan_hash
    body = {"plan_hash": plan_hash, "gate": 0}
    assert (await client.post(f"/api/workflow-runs/{run_id}/approve", headers=ALICE, json=body)).status_code == 200
    await app.state.runner.run_once()
    final = (await client.get(f"/api/workflow-runs/{run_id}", headers=ALICE)).json()
    assert final["status"] == "COMPLETED"
    assert gateway.publish_calls[1]["arguments"]["associations"][0]["to"]["id"] == "777"


# ---------- least-privilege connection ----------
async def test_connect_uses_only_the_permissions_the_app_needs(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    response = await client.post("/api/connectors/hubspot/connect", headers=ALICE)
    assert response.status_code == 200 and gateway.started[0]["slug"] == "hubspot"


@pytest.mark.parametrize(
    "scopes",
    [
        None,
        (),
        ("oauth", "crm.objects.contacts.read", "crm.objects.contacts.write", "tickets"),
        ("oauth", "crm.objects.contacts.write", "crm.objects.deals.write"),
    ],
)
async def test_broad_or_missing_permissions_never_start_a_connection(
    client: httpx.AsyncClient, gateway: FakeGateway, scopes: tuple[str, ...] | None
) -> None:
    if scopes is None:
        gateway.auth_scopes.pop("hubspot")
    else:
        gateway.auth_scopes["hubspot"] = scopes
    response = await client.post("/api/connectors/hubspot/connect", headers=ALICE)
    assert response.status_code == 409 and "limited permissions" in response.json()["detail"]
    assert gateway.started == []
