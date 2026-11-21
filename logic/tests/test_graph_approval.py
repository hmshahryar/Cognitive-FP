"""LangGraph pause/resume around human approval: durability, idempotency, recovery."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from cognitive_mesh.models import Approval, IntegrationConnection
from cognitive_mesh.services.runner import TaskRunner
from tests.conftest import FakeGateway, auth_headers
from tests.fakes_ai import FakeTeam
from tests.test_runner import command, connect_linkedin, events, status

ALICE = auth_headers("user_alice")


def fresh_runner(app: FastAPI, gateway: FakeGateway, team: FakeTeam) -> TaskRunner:
    """A brand-new runner on the same DB + checkpoint file, as after a server restart."""
    return TaskRunner(
        app.state.db,
        app.state.settings,
        gateway,
        lambda _s, _g, _u: team.pipeline(),
        signer=app.state.approval_signer,
    )


async def paused_task(client: httpx.AsyncClient, app: FastAPI) -> tuple[dict, dict]:
    await connect_linkedin(client, app)
    task = await command(client)
    await app.state.runner.run_once()
    approvals = (await client.get("/api/approvals", headers=ALICE, params={"task_id": task["id"]})).json()
    assert await status(client, task) == "AWAITING_APPROVAL" and len(approvals) == 1
    return task, approvals[0]


async def approve(client, a, **override):  # type: ignore[no-untyped-def]
    body = {"payload_hash": a["payload_hash"], "content_version": a["content_version"], **override}
    return await client.post(f"/api/approvals/{a['id']}/approve", headers=ALICE, json=body)


async def approvals_for(client, task) -> list[dict]:  # type: ignore[no-untyped-def]
    rows = (await client.get("/api/approvals", headers=ALICE, params={"task_id": task["id"]})).json()
    return sorted(rows, key=lambda r: r["content_version"])


async def approval_count(app: FastAPI) -> int:
    async with app.state.db.sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(Approval)) or 0


async def is_paused(app: FastAPI, task: dict) -> bool:
    runner: TaskRunner = app.state.runner
    async with app.state.db.sessionmaker() as s:
        from cognitive_mesh.models import Execution

        execution = await s.get(Execution, __import__("uuid").UUID(task["current_execution_id"]))
    return runner.is_paused(await runner.snapshot(execution))


# ---------- core pause/resume ----------
async def test_approve_resumes_graph_to_approved(client: httpx.AsyncClient, app: FastAPI) -> None:
    task, a = await paused_task(client, app)
    assert await is_paused(app, task)

    assert (await approve(client, a)).status_code == 200
    assert await app.state.runner.run_once() is True  # processes the queued resume
    assert await status(client, task) == "COMPLETED"
    assert not await is_paused(app, task)  # graph finished after publishing
    assert (await events(client, task))[-4:] == [
        "approval.approved", "graph.resumed", "publish.started", "publish.succeeded",
    ]
    assert await approval_count(app) == 1


async def test_forged_resume_while_pending_pauses_again_without_duplicates(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task, _a = await paused_task(client, app)
    # Resume without any decision: the graph must read PENDING from the DB and pause again.
    assert await app.state.runner.resume(__import__("uuid").UUID(task["current_execution_id"]))
    assert await status(client, task) == "AWAITING_APPROVAL"
    assert await is_paused(app, task)
    assert await approval_count(app) == 1  # node re-ran but reused the same approval
    assert (await approvals_for(client, task))[0]["status"] == "PENDING"


async def test_resume_is_idempotent(client: httpx.AsyncClient, app: FastAPI) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)
    await app.state.runner.run_once()
    exec_id = __import__("uuid").UUID(task["current_execution_id"])
    assert await app.state.runner.resume(exec_id) is False  # nothing left to resume
    assert await status(client, task) == "COMPLETED" and await approval_count(app) == 1


async def test_reject_ends_graph_and_retry_starts_a_new_thread(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task, a = await paused_task(client, app)
    await client.post(f"/api/approvals/{a['id']}/reject", headers=ALICE, json={"note": "no"})
    await app.state.runner.run_once()
    assert await status(client, task) == "REJECTED" and not await is_paused(app, task)

    await client.post(f"/api/tasks/{task['id']}/retry", headers=ALICE)
    await app.state.runner.run_once()
    execs = (await client.get(f"/api/tasks/{task['id']}/executions", headers=ALICE)).json()
    assert len(execs) == 2 and execs[0]["thread_id"] != execs[1]["thread_id"]
    assert await status(client, task) == "AWAITING_APPROVAL"


async def test_changes_requested_redrafts_without_re_researching(
    client: httpx.AsyncClient, app: FastAPI, team: FakeTeam
) -> None:
    task, a = await paused_task(client, app)
    researched = len(team.profiles)
    await client.post(
        f"/api/approvals/{a['id']}/request-changes", headers=ALICE, json={"note": "Make it shorter"}
    )
    from tests.fakes_ai import draft

    team.drafts = [draft("Original."), draft("Shorter.")]
    await app.state.runner.run_once()

    assert await status(client, task) == "AWAITING_APPROVAL"
    assert len(team.profiles) == researched  # research reused from the checkpoint
    assert ["Make it shorter"] in team.writer_feedback
    rows = await approvals_for(client, task)
    assert [(r["content_version"], r["status"]) for r in rows] == [(1, "CHANGES_REQUESTED"), (2, "PENDING")]
    assert rows[1]["payload"]["commentary"].startswith("Shorter.")
    drafts = (await client.get(f"/api/tasks/{task['id']}/drafts", headers=ALICE)).json()
    assert [d["version"] for d in drafts] == [1, 2]
    # The new version needs its own approval; the old one can't be used.
    assert (await approve(client, rows[0])).status_code == 409
    assert (await approve(client, rows[1])).status_code == 200


# ---------- visibility: part of the signed action ----------
async def test_visibility_change_requires_new_approval(client: httpx.AsyncClient, app: FastAPI) -> None:
    task, v1 = await paused_task(client, app)
    assert v1["payload"]["visibility"] == "PUBLIC"

    res = await client.post(f"/api/approvals/{v1['id']}/visibility", headers=ALICE, json={"visibility": "CONNECTIONS"})
    assert res.status_code == 200
    v2 = res.json()
    assert v2["id"] != v1["id"] and v2["content_version"] == 2
    assert v2["payload"] == {**v1["payload"], "visibility": "CONNECTIONS"}
    assert v2["payload_hash"] != v1["payload_hash"]  # different action, different binding

    rows = await approvals_for(client, task)
    assert [r["status"] for r in rows] == ["SUPERSEDED", "PENDING"]
    assert (await approve(client, v1)).status_code == 409  # old consent can't authorize it
    assert await is_paused(app, task)  # still waiting for the new consent

    assert (await approve(client, v2)).status_code == 200
    await app.state.runner.run_once()
    assert await status(client, task) == "COMPLETED"


async def test_visibility_rules(client: httpx.AsyncClient, app: FastAPI) -> None:
    _task, v1 = await paused_task(client, app)
    url = f"/api/approvals/{v1['id']}/visibility"
    assert (await client.post(url, headers=ALICE, json={"visibility": "LOGGED_IN"})).status_code == 422
    same = await client.post(url, headers=ALICE, json={"visibility": "PUBLIC"})
    assert same.json()["id"] == v1["id"]  # unchanged: no new version
    await client.post(url, headers=ALICE, json={"visibility": "CONNECTIONS"})
    # Only the pending version can be changed.
    assert (await client.post(url, headers=ALICE, json={"visibility": "PUBLIC"})).status_code == 409
    bob = auth_headers("user_bob")
    await client.get("/api/me", headers=bob)
    assert (await client.post(url, headers=bob, json={"visibility": "PUBLIC"})).status_code == 404


async def test_visibility_choice_survives_a_revision(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task, v1 = await paused_task(client, app)
    v2 = (await client.post(f"/api/approvals/{v1['id']}/visibility", headers=ALICE, json={"visibility": "CONNECTIONS"})).json()
    await client.post(f"/api/approvals/{v2['id']}/request-changes", headers=ALICE, json={"note": "tweak"})
    await app.state.runner.run_once()  # resumes -> redraft -> new approval
    latest = (await approvals_for(client, task))[-1]
    assert latest["status"] == "PENDING" and latest["payload"]["visibility"] == "CONNECTIONS"


# ---------- cancellation & invalidation while paused ----------
async def test_cancel_while_paused_closes_the_graph(client: httpx.AsyncClient, app: FastAPI) -> None:
    task, _a = await paused_task(client, app)
    assert (await client.post(f"/api/tasks/{task['id']}/cancel", headers=ALICE)).status_code == 200
    await app.state.runner.run_once()
    assert await status(client, task) == "CANCELLED" and not await is_paused(app, task)
    assert (await approvals_for(client, task))[0]["status"] == "INVALIDATED"


async def test_reconnected_account_invalidates_and_fails_task(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    task, a = await paused_task(client, app)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(IntegrationConnection).values(composio_account_id="ca_other_person"))
        await s.commit()
    assert (await approve(client, a)).status_code == 409  # binding check catches it
    await app.state.runner.recover()  # decided (INVALIDATED) -> graph resumes and fails
    assert await status(client, task) == "FAILED"
    chat = (await client.get(f"/api/chats/{task['chat_id']}", headers=ALICE)).json()
    assert "invalidated" in chat["messages"][-1]["content"]


# ---------- durability & recovery across restarts ----------
async def test_decision_made_while_server_down_is_resumed_on_startup(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    task, a = await paused_task(client, app)
    await approve(client, a)  # resume queued on the OLD runner, which "crashes" now
    await app.state.runner.close()

    restarted = fresh_runner(app, gateway, team)
    try:
        assert (await restarted.recover())["resumed"] == 1
    finally:
        await restarted.close()
    assert await status(client, task) == "COMPLETED"
    assert await approval_count(app) == 1


async def test_pause_survives_restart_and_still_waits(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    task, a = await paused_task(client, app)
    await app.state.runner.close()
    restarted = fresh_runner(app, gateway, team)
    try:
        assert await restarted.recover() == {"resumed": 0, "continued": 0, "failed": 0}
        app.state.runner = restarted
        assert await is_paused(app, task)  # checkpoint persisted (durability="sync")
        await approve(client, a)
        assert await restarted.run_once() is True
        assert await status(client, task) == "COMPLETED"
    finally:
        await restarted.close()


class Crash(BaseException):
    """Simulates the process dying mid-node (not a normal, handled exception)."""


async def test_crash_mid_run_continues_from_last_checkpoint(
    client: httpx.AsyncClient, app: FastAPI, gateway: FakeGateway, team: FakeTeam
) -> None:
    await connect_linkedin(client, app)
    task = await command(client)
    original_write, calls = team.write, {"n": 0}

    async def crash_once(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise Crash()
        return await original_write(*args, **kwargs)

    team.write = crash_once  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await app.state.runner.run_once()
    await app.state.runner.close()
    researched = len(team.profiles)

    restarted = fresh_runner(app, gateway, team)
    try:
        assert (await restarted.recover())["continued"] == 1
    finally:
        await restarted.close()
    assert await status(client, task) == "AWAITING_APPROVAL"
    assert len(team.profiles) == researched  # research NOT redone: resumed after it
    assert await approval_count(app) == 1


async def test_run_without_any_checkpoint_is_failed_on_recovery(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    from cognitive_mesh.models import Task, TaskStatus
    from cognitive_mesh.services.tasks import transition

    task = await command(client)
    async with app.state.db.sessionmaker() as s:  # claimed, then died before any checkpoint
        t = await s.get(Task, __import__("uuid").UUID(task["id"]))
        await transition(s, t, allowed_from=[TaskStatus.QUEUED], to=TaskStatus.RUNNING)  # type: ignore[arg-type]
        await s.commit()
    assert (await app.state.runner.recover())["failed"] == 1
    assert await status(client, task) == "FAILED"
