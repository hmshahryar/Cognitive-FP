"""Runner + graph: start, draft, pause for approval, failures, cancel, isolation."""

import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from cognitive_mesh.models import ConnectionStatus, IntegrationConnection
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam, draft, verdict

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")


async def connect_linkedin(client: httpx.AsyncClient, app: FastAPI, headers=ALICE) -> None:  # type: ignore[no-untyped-def]
    me = (await client.get("/api/me", headers=headers)).json()
    async with app.state.db.sessionmaker() as s:
        s.add(
            IntegrationConnection(
                workspace_id=uuid.UUID(me["workspace"]["id"]),
                user_id=uuid.UUID(me["user"]["id"]),
                toolkit_slug="linkedin",
                composio_account_id=f"ca_{uuid.uuid4().hex[:8]}",
                status=ConnectionStatus.ACTIVE,
                granted_scopes=["w_member_social"],
            )
        )
        await s.commit()


async def command(client: httpx.AsyncClient, text="Research agentic AI and write a LinkedIn post", headers=ALICE) -> dict:  # type: ignore[no-untyped-def]
    chat = (await client.post("/api/chats", headers=headers, json={})).json()
    return (
        await client.post(f"/api/chats/{chat['id']}/tasks", headers=headers, json={"request": text})
    ).json()


async def status(client, task, headers=ALICE) -> str:  # type: ignore[no-untyped-def]
    return (await client.get(f"/api/tasks/{task['id']}", headers=headers)).json()["status"]


async def events(client, task) -> list[str]:  # type: ignore[no-untyped-def]
    rows = (await client.get(f"/api/tasks/{task['id']}/events", headers=ALICE)).json()
    return [e["event"] for e in rows]


async def test_run_drafts_and_pauses_for_approval(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    task = await command(client)
    assert await app.state.runner.run_once() is True

    assert await status(client, task) == "AWAITING_APPROVAL"
    drafts = (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json()
    assert len(drafts) == 1 and drafts[0]["is_final"]

    approvals = (await client.get("/api/approvals", headers=ALICE, params={"task_id": task["id"]})).json()
    assert len(approvals) == 1
    a = approvals[0]
    assert a["status"] == "PENDING" and a["tool_slug"] == "LINKEDIN_CREATE_LINKED_IN_POST"
    # The signed payload is the exact post: author from the profile, PUBLIC by default.
    assert a["payload"] == {
        "author": "urn:li:person:abc",
        "commentary": drafts[0]["text"],
        "visibility": "PUBLIC",
        "lifecycleState": "PUBLISHED",
    }
    assert await events(client, task) == [
        "task.created", "execution.started", "route.selected",
        "research.completed", "review.completed", "draft.ready", "approval.requested",
    ]
    # The Social Media employee read the profile through the read-only path, as this user.
    assert gateway.read_calls[0][1] == "LINKEDIN_GET_MY_INFO"
    assert "AI engineer" in team.profiles[0]


async def test_all_reviewed_versions_are_kept(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.drafts = [draft("v1"), draft("v2")]
    team.verdicts = [verdict(False, ["tighten"]), verdict(True)]
    await connect_linkedin(client, app)
    task = await command(client)
    await app.state.runner.run_once()
    drafts = (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json()
    assert [(d["version"], d["accepted"], d["is_final"]) for d in drafts] == [
        (1, False, False), (2, True, True)
    ]


async def test_nothing_to_do_returns_false(app: FastAPI) -> None:
    assert await app.state.runner.run_once() is False


async def test_not_connected_fails_with_guidance_then_retry_works(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task = await command(client)
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "Connectors page" in chat["messages"][-1]["content"]

    await connect_linkedin(client, app)
    await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)
    await app.state.runner.run_once()
    assert await status(client, task) == "AWAITING_APPROVAL"


async def test_missing_profile_id_fails_clearly(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway
) -> None:
    gateway.profile_id = None
    await connect_linkedin(client, app)
    task = await command(client)
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "member id" in chat["messages"][-1]["content"]


async def test_employee_failure_marks_task_failed_without_leaking_internals(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.research_error = RuntimeError("secret stack detail")
    await connect_linkedin(client, app)
    task = await command(client)
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "secret stack detail" not in chat["messages"][-1]["content"]
    execs = (await client.get(f"/api/tasks/{task['id']}/executions", headers=ALICE)).json()
    assert "secret stack detail" in execs[0]["error"]


async def test_unsupported_command_completes_with_explanation(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    team.action = "unsupported"
    await connect_linkedin(client, app)
    task = await command(client, "Book me a flight")
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"


async def test_cancel_during_research_stops_before_drafting(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    task = await command(client)
    original = team.research

    async def research_then_user_cancels(intent, profile, registry):  # type: ignore[no-untyped-def]
        out = await original(intent, profile, registry)
        await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)
        return out

    team.research = research_then_user_cancels  # type: ignore[method-assign]
    await app.state.runner.run_once()
    assert await status(client, task) == "CANCELLED"
    assert (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json() == []
    assert (await client.get("/api/approvals", headers=ALICE)).json() == []


async def test_runs_oldest_queued_first_and_each_once(client: httpx.AsyncClient, app: FastAPI) -> None:
    await connect_linkedin(client, app)
    first = await command(client, "Research agents, then write a LinkedIn post")
    second = await command(client, "Research robotics, then write a LinkedIn post")
    assert await app.state.runner.run_once() and await app.state.runner.run_once()
    assert await app.state.runner.run_once() is False
    for t in (first, second):
        assert await status(client, t) == "AWAITING_APPROVAL"


async def test_drafts_and_approvals_are_workspace_isolated(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await connect_linkedin(client, app)
    task = await command(client)
    await app.state.runner.run_once()
    await client.get("/api/me", headers=BOB)
    assert (await client.get(f"/api/tasks/{task['id']}/drafts", headers=BOB)).status_code == 404
    assert (await client.get("/api/approvals", headers=BOB)).json() == []


async def test_disconnected_after_queue_fails_cleanly(client: httpx.AsyncClient, app: FastAPI) -> None:
    await connect_linkedin(client, app)
    task = await command(client)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(IntegrationConnection).values(status=ConnectionStatus.EXPIRED))
        await s.commit()
    await app.state.runner.run_once()
    assert await status(client, task) == "FAILED"
