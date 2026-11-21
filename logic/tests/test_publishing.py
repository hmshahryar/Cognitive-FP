"""Step 5: a post goes out only with valid consent, and at most once. Uses a fake LinkedIn."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from composio_client import APIStatusError
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.integrations.gateway import (
    APPROVED_ACTION_TOOLS,
    READ_ONLY_TOOLS,
    ActionOutcomeUnknownError,
    ActionRejectedError,
    ComposioGateway,
    ToolNotAllowedError,
)
from cognitive_mesh.integrations.linkedin import post_url
from cognitive_mesh.models import Approval, IntegrationConnection, Publication
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_graph_approval import approvals_for, approve, fresh_runner, paused_task
from tests.test_runner import command, connect_linkedin, status

ALICE = auth_headers("user_alice")


async def publications(client: httpx.AsyncClient, task: dict, headers=ALICE) -> list[dict]:  # type: ignore[no-untyped-def]
    return (await client.get(f"/api/tasks/{task['id']}/publications", headers=headers)).json()


async def last_message(client: httpx.AsyncClient, task: dict) -> str:
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    return chat["messages"][-1]["content"]


async def test_one_click_publishes_exact_approved_payload_to_the_approved_account(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    await connect_linkedin(client, app)
    exact = "Line one.\n\n  Line two, unchanged."
    task = await command(client, f"Post this on LinkedIn: {exact}")
    await app.state.runner.run_once()
    a = (await approvals_for(client, task))[0]
    assert gateway.publish_calls == []  # nothing is sent before consent

    await approve(client, a)
    await app.state.runner.run_once()

    assert await status(client, task) == "COMPLETED"
    assert len(gateway.publish_calls) == 1
    call = gateway.publish_calls[0]
    assert call["arguments"] == a["payload"] and call["arguments"]["commentary"] == exact
    assert call["account"] == a["target_account_id"]
    row = (await publications(client, task))[0]
    assert row["status"] == "PUBLISHED" and row["external_id"] == "urn:li:share:7001"
    assert row["external_url"] == "https://www.linkedin.com/feed/update/urn:li:share:7001/"
    assert row["external_url"] in await last_message(client, task)


async def test_selected_visibility_is_what_gets_sent(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    _task, v1 = await paused_task(client, app)
    v2 = (await client.post(f"/api/approvals/{v1['id']}/visibility", headers=ALICE, json={"visibility": "CONNECTIONS"})).json()
    await approve(client, v2)
    await app.state.runner.run_once()
    assert gateway.publish_calls[0]["arguments"]["visibility"] == "CONNECTIONS"


async def test_double_click_and_repeated_resume_post_once(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    assert (await approve(client, a)).status_code == 200
    assert (await approve(client, a)).status_code == 409
    await app.state.runner.run_once()
    assert await app.state.runner.resume(uuid.UUID(task["current_execution_id"])) is False
    assert len(gateway.publish_calls) == 1


@pytest.mark.parametrize("decision", ["reject", "request-changes"])
async def test_non_approval_never_publishes(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, decision: str
) -> None:
    _task, a = await paused_task(client, app)
    await client.post(f"/api/approvals/{a['id']}/{decision}", headers=ALICE, json={"note": "no"})
    await app.state.runner.run_once()
    assert gateway.publish_calls == []


async def test_content_tampered_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(Approval).where(Approval.id == uuid.UUID(a["id"]))
            .values(payload={**a["payload"], "commentary": "Tampered"})
        )
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"
    assert "Nothing went live" in await last_message(client, task)


async def test_account_changed_after_approval_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(IntegrationConnection).values(composio_account_id="ca_someone_else"))
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_missing_posting_permission_is_blocked(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(IntegrationConnection).values(granted_scopes=["profile"]))
        await s.commit()
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "FAILED"


async def test_cancel_after_approval_before_sending_never_publishes(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)
    await app.state.runner.run_once()
    assert gateway.publish_calls == [] and await status(client, task) == "CANCELLED"


async def test_definite_rejection_can_retry_only_with_new_consent(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    gateway.publish_error = ActionRejectedError("status code 403 secret-provider-detail")
    await approve(client, a)
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
    assert (await publications(client, task))[0]["status"] == "FAILED"
    assert "secret-provider-detail" not in await last_message(client, task)

    gateway.publish_error = None
    assert (await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)).status_code == 200
    await app.state.runner.run_once()
    assert await status(client, task) == "AWAITING_APPROVAL"
    assert len(gateway.publish_calls) == 1  # retry alone never posts


async def test_unknown_result_is_not_resent_and_blocks_retry(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    gateway.publish_error = ActionOutcomeUnknownError("read timeout")
    await approve(client, a)
    await app.state.runner.run_once()
    assert (await publications(client, task))[0]["status"] == "NEEDS_CHECK"
    assert "Check your LinkedIn" in await last_message(client, task)
    assert (await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)).status_code == 409
    assert len(gateway.publish_calls) == 1


async def test_success_without_a_post_id_needs_checking(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    task, a = await paused_task(client, app)
    gateway.publish_response = {"unexpected": True}
    await approve(client, a)
    await app.state.runner.run_once()
    assert (await publications(client, task))[0]["status"] == "NEEDS_CHECK"


class Crash(BaseException):
    """The process dies during the external call."""


async def test_crash_during_send_is_never_sent_again(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    task, a = await paused_task(client, app)
    gateway.publish_error = Crash()
    await approve(client, a)
    with pytest.raises(Crash):
        await app.state.runner.run_once()
    await app.state.runner.close()
    assert await status(client, task) == "EXECUTING"

    gateway.publish_error = None
    restarted = fresh_runner(app, gateway, team)
    try:
        assert (await restarted.recover())["continued"] == 1
    finally:
        await restarted.close()
    assert len(gateway.publish_calls) == 1
    assert await status(client, task) == "FAILED"
    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(Publication))
    assert row is not None and row.status == "NEEDS_CHECK"


async def test_publications_are_workspace_isolated(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    await app.state.runner.run_once()
    bob = auth_headers("user_bob")
    await client.get("/api/me", headers=bob)
    response = await client.get(f"/api/tasks/{task['id']}/publications", headers=bob)
    assert response.status_code == 404


def test_write_tools_are_never_read_only() -> None:
    assert not APPROVED_ACTION_TOOLS & READ_ONLY_TOOLS


@pytest.mark.parametrize(
    ("post_id", "url"),
    [
        ("urn:li:share:123", "https://www.linkedin.com/feed/update/urn:li:share:123/"),
        ("urn:li:ugcPost:456", "https://www.linkedin.com/feed/update/urn:li:ugcPost:456/"),
        ("javascript:alert(1)", None),
        (None, None),
    ],
)
def test_post_url_only_accepts_real_post_urns(post_id: str | None, url: str | None) -> None:
    assert post_url(post_id) == url


# ---------- real gateway logic against a stub Composio client ----------
def status_error(code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://composio.example/execute")
    return APIStatusError("error", response=httpx.Response(code, request=request), body=None)


def stub_gateway(execute, owner="user-1", account_status="ACTIVE"):  # type: ignore[no-untyped-def]
    created: list[dict] = []
    account = SimpleNamespace(
        id="ca_1", user_id=owner, toolkit=SimpleNamespace(slug="linkedin"), status=account_status,
        requested_scopes=[], model_dump=dict,
    )
    session = SimpleNamespace(execute=execute, delete=lambda: None)

    def create(**kwargs):  # type: ignore[no-untyped-def]
        created.append(kwargs)
        return session

    gateway = ComposioGateway("")
    gateway._client = SimpleNamespace(  # type: ignore[assignment]
        connected_accounts=SimpleNamespace(get=lambda _id: account),
        sessions=SimpleNamespace(create=create),
    )
    return gateway, created


async def send(gateway):  # type: ignore[no-untyped-def]
    return await gateway.execute_approved_action(
        user_id="user-1", toolkit_slug="linkedin", tool_slug="LINKEDIN_CREATE_LINKED_IN_POST",
        connected_account_id="ca_1", arguments={"commentary": "x"},
    )


async def test_gateway_locks_session_to_the_approved_account() -> None:
    result = SimpleNamespace(
        result_type="completed", error=None,
        model_dump=lambda mode: {"data": {"x_restli_id": "urn:li:share:1"}},
    )
    gateway, created = stub_gateway(lambda slug, arguments: result)
    assert await send(gateway) == {"x_restli_id": "urn:li:share:1"}
    assert created[0]["connected_accounts"] == {"linkedin": "ca_1"}


async def test_gateway_refuses_another_users_account_before_sending() -> None:
    def must_not_run(slug, arguments):  # type: ignore[no-untyped-def]
        raise AssertionError("sent")

    gateway, _created = stub_gateway(must_not_run, owner="someone-else")
    with pytest.raises(ActionRejectedError):
        await send(gateway)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (status_error(403), ActionRejectedError),
        (status_error(429), ActionRejectedError),
        (status_error(408), ActionOutcomeUnknownError),
        (status_error(503), ActionOutcomeUnknownError),
        (TimeoutError("read timeout"), ActionOutcomeUnknownError),
    ],
)
async def test_gateway_only_calls_definite_errors_rejected(failure: Exception, expected: type) -> None:
    calls = {"n": 0}

    def fail(slug, arguments):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise failure

    gateway, _created = stub_gateway(fail)
    with pytest.raises(expected):
        await send(gateway)
    assert calls["n"] == 1  # never retried


async def test_gateway_rejects_tools_outside_the_write_allowlist() -> None:
    gateway, _created = stub_gateway(lambda slug, arguments: None)
    with pytest.raises(ToolNotAllowedError):
        await gateway.execute_approved_action(
            user_id="user-1", toolkit_slug="linkedin", tool_slug="LINKEDIN_DELETE_LINKED_IN_POST",
            connected_account_id="ca_1", arguments={},
        )
