"""Approval security: binding, invalidation, isolation, idempotency (requirements 1-3)."""

import asyncio
import uuid
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.models import (
    Approval,
    ConnectionStatus,
    Execution,
    IntegrationConnection,
    Task,
    TaskStatus,
    User,
    Workspace,
)
from cognitive_mesh.services import approvals as svc
from cognitive_mesh.services.errors import ApprovalInvalidError
from cognitive_mesh.services.tasks import transition
from tests.conftest import auth_headers

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")
POST = {
    "author": "urn:li:person:abc",
    "commentary": "Agentic AI is here.",
    "visibility": "PUBLIC",
    "lifecycleState": "PUBLISHED",
}


@dataclass
class Ctx:
    task_id: uuid.UUID
    execution_id: uuid.UUID
    connection_id: uuid.UUID
    user_id: uuid.UUID


async def _setup(client: httpx.AsyncClient, app: FastAPI, headers=ALICE) -> Ctx:  # type: ignore[no-untyped-def]
    """A task that is RUNNING with an ACTIVE LinkedIn connection in the workspace."""
    me = (await client.get("/api/me", headers=headers)).json()
    chat = (await client.post("/api/chats", headers=headers, json={})).json()
    task = (
        await client.post(f"/api/chats/{chat['id']}/tasks", headers=headers, json={"request": "post"})
    ).json()
    async with app.state.db.sessionmaker() as s:
        conn = IntegrationConnection(
            workspace_id=uuid.UUID(me["workspace"]["id"]),
            user_id=uuid.UUID(me["user"]["id"]),
            toolkit_slug="linkedin",
            composio_account_id=f"ca_{uuid.uuid4().hex[:8]}",
            status=ConnectionStatus.ACTIVE,
            granted_scopes=["w_member_social"],
        )
        s.add(conn)
        await s.commit()
        t = await s.get(Task, uuid.UUID(task["id"]))
        assert t is not None
        await transition(s, t, allowed_from=[TaskStatus.QUEUED], to=TaskStatus.RUNNING)
        await s.commit()
        return Ctx(t.id, t.current_execution_id, conn.id, uuid.UUID(me["user"]["id"]))  # type: ignore[arg-type]


async def _propose(app: FastAPI, ctx: Ctx, payload: dict = POST) -> Approval:
    async with app.state.db.sessionmaker() as s:
        return await svc.request_approval(
            s,
            app.state.approval_signer,
            task=await s.get(Task, ctx.task_id, populate_existing=True),  # type: ignore[arg-type]
            execution=await s.get(Execution, ctx.execution_id),  # type: ignore[arg-type]
            requested_by=ctx.user_id,
            action_type="linkedin.publish_post",
            toolkit_slug="linkedin",
            tool_slug="LINKEDIN_CREATE_LINKED_IN_POST",
            connection=await s.get(IntegrationConnection, ctx.connection_id),  # type: ignore[arg-type]
            payload=payload,
        )


async def _approve(client: httpx.AsyncClient, a: Approval | dict, headers=ALICE, **override):  # type: ignore[no-untyped-def]
    body = (
        {"payload_hash": a.payload_hash, "content_version": a.content_version}
        if isinstance(a, Approval)
        else {"payload_hash": a["payload_hash"], "content_version": a["content_version"]}
    )
    aid = a.id if isinstance(a, Approval) else a["id"]
    return await client.post(f"/api/approvals/{aid}/approve", headers=headers, json={**body, **override})


async def _status(app: FastAPI, model: type, id_: uuid.UUID) -> str:
    async with app.state.db.sessionmaker() as s:
        return (await s.get(model, id_, populate_existing=True)).status  # type: ignore[union-attr]


# ---------- happy path ----------
async def test_request_and_approve(client: httpx.AsyncClient, app: FastAPI) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    assert await _status(app, Task, ctx.task_id) == TaskStatus.AWAITING_APPROVAL

    pending = (await client.get("/api/approvals", headers=ALICE, params={"status": "PENDING"})).json()
    assert [p["id"] for p in pending] == [str(approval.id)]
    assert pending[0]["payload"] == POST and "binding_signature" not in pending[0]

    res = await _approve(client, pending[0], note="Looks good")
    assert res.status_code == 200 and res.json()["status"] == "APPROVED"
    assert await _status(app, Task, ctx.task_id) == TaskStatus.APPROVED

    events = (await client.get(f"/api/tasks/{ctx.task_id}/events", headers=ALICE)).json()
    assert [e["event"] for e in events] == ["task.created", "approval.requested", "approval.approved"]
    assert events[-1]["detail"]["payload_hash"] == approval.payload_hash


async def test_double_approve_is_rejected(client: httpx.AsyncClient, app: FastAPI) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    results = await asyncio.gather(*(_approve(client, approval) for _ in range(4)))
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409]


# ---------- requirement 1: bound to exact content/version/account/action ----------
async def test_approving_with_wrong_hash_or_version_fails(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    bad_hash = await _approve(client, approval, payload_hash="0" * 64)
    bad_version = await _approve(client, approval, content_version=2)
    assert bad_hash.status_code == bad_version.status_code == 409
    assert await _status(app, Approval, approval.id) == "PENDING"


@pytest.mark.parametrize(
    "tamper",
    [
        {"payload": {**POST, "commentary": "Changed after request"}},  # content
        {"target_account_id": "ca_attacker"},  # destination account
        {"tool_slug": "LINKEDIN_DELETE_POST"},  # action
        {"action_type": "linkedin.delete_post"},
        {"content_version": 7},
    ],
)
async def test_tampered_approval_is_invalidated(
    client: httpx.AsyncClient, app: FastAPI, tamper: dict
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Approval).where(Approval.id == approval.id).values(**tamper))
        await s.commit()
    seen = {"payload_hash": approval.payload_hash, "content_version": tamper.get("content_version", 1)}
    res = await client.post(f"/api/approvals/{approval.id}/approve", headers=ALICE, json=seen)
    assert res.status_code == 409
    assert await _status(app, Approval, approval.id) == "INVALIDATED"
    assert await _status(app, Task, ctx.task_id) == TaskStatus.AWAITING_APPROVAL


# ---------- requirement 2: any modification needs new consent ----------
async def test_new_content_version_supersedes_old_approval(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    v1 = await _propose(app, ctx)
    v2 = await _propose(app, ctx, {**POST, "commentary": "Revised draft"})
    assert (v1.content_version, v2.content_version) == (1, 2)
    assert await _status(app, Approval, v1.id) == "SUPERSEDED"

    assert (await _approve(client, v1)).status_code == 409  # old consent can't be used
    # v2 approved with v1's hash -> mismatch
    stale = await _approve(client, v2, payload_hash=v1.payload_hash)
    assert stale.status_code == 409
    assert (await _approve(client, v2)).status_code == 200


async def test_reconnecting_a_different_account_invalidates(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(IntegrationConnection)
            .where(IntegrationConnection.id == ctx.connection_id)
            .values(composio_account_id="ca_another_linkedin")
        )
        await s.commit()
    res = await _approve(client, approval)
    assert res.status_code == 409
    assert "Target account" in res.json()["detail"]
    assert await _status(app, Approval, approval.id) == "INVALIDATED"


async def test_disconnect_invalidates_open_approvals(
    client: httpx.AsyncClient, app: FastAPI, gateway
) -> None:  # type: ignore[no-untyped-def]
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    assert (await client.delete("/api/connectors/linkedin", headers=ALICE)).status_code == 204
    assert await _status(app, Approval, approval.id) == "INVALIDATED"


async def test_expired_connection_invalidates_open_approvals(
    client: httpx.AsyncClient, app: FastAPI, gateway
) -> None:  # type: ignore[no-untyped-def]
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    async with app.state.db.sessionmaker() as s:
        conn = await s.get(IntegrationConnection, ctx.connection_id)
        assert conn is not None
        conn.status = ConnectionStatus.EXPIRED  # what the Connector Hub sync records
        await s.commit()
        assert await svc.invalidate_for_connection_change(s, conn, "Account is expired") == 1
    assert await _status(app, Approval, approval.id) == "INVALIDATED"


async def test_cancelling_task_invalidates_pending_approval(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    assert (await client.post(f"/api/tasks/{ctx.task_id}/cancel", headers=ALICE)).status_code == 200
    assert await _status(app, Approval, approval.id) == "INVALIDATED"


async def test_cannot_request_approval_without_active_connection(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(IntegrationConnection).values(status=ConnectionStatus.EXPIRED)
        )
        await s.commit()
    from cognitive_mesh.services.errors import ConflictError

    with pytest.raises(ConflictError):
        await _propose(app, ctx)


# ---------- reject / request changes ----------
async def test_reject_blocks_execution_and_allows_retry(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    res = await client.post(f"/api/approvals/{approval.id}/reject", headers=ALICE, json={"note": "no"})
    assert res.json()["status"] == "REJECTED"
    assert await _status(app, Task, ctx.task_id) == TaskStatus.REJECTED
    async with app.state.db.sessionmaker() as s:
        with pytest.raises(ApprovalInvalidError):
            await svc.verify_for_execution(s, app.state.approval_signer, await s.get(Approval, approval.id))  # type: ignore[arg-type]
    assert (await client.post(f"/api/tasks/{ctx.task_id}/retry", headers=ALICE)).status_code == 200


async def test_request_changes_sends_task_back_and_needs_note(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    no_note = await client.post(f"/api/approvals/{approval.id}/request-changes", headers=ALICE, json={})
    assert no_note.status_code == 422
    res = await client.post(
        f"/api/approvals/{approval.id}/request-changes",
        headers=ALICE,
        json={"note": "Make it shorter"},
    )
    assert res.json()["status"] == "CHANGES_REQUESTED"
    assert await _status(app, Task, ctx.task_id) == TaskStatus.RUNNING
    # The agent proposes v2; only v2 can be approved.
    v2 = await _propose(app, ctx, {**POST, "commentary": "Short."})
    assert v2.content_version == 2
    assert (await _approve(client, approval)).status_code == 409
    assert (await _approve(client, v2)).status_code == 200


# ---------- requirement 3: isolation & authorization ----------
async def test_other_workspace_cannot_see_or_decide(client: httpx.AsyncClient, app: FastAPI) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    await client.get("/api/me", headers=BOB)
    assert (await client.get("/api/approvals", headers=BOB)).json() == []
    for res in await asyncio.gather(
        client.get(f"/api/approvals/{approval.id}", headers=BOB),
        _approve(client, approval, headers=BOB),
        client.post(f"/api/approvals/{approval.id}/reject", headers=BOB, json={}),
        client.post(f"/api/approvals/{approval.id}/request-changes", headers=BOB, json={"note": "x"}),
    ):
        assert res.status_code == 404
    assert await _status(app, Approval, approval.id) == "PENDING"


async def test_only_workspace_owner_can_approve(client: httpx.AsyncClient, app: FastAPI) -> None:
    """Guards the future team case: a member who isn't the owner can't consent."""
    from cognitive_mesh.services.accounts import Account
    from cognitive_mesh.services.errors import ForbiddenError

    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    async with app.state.db.sessionmaker() as s:
        member = User(clerk_user_id="user_member")
        s.add(member)
        await s.commit()
        workspace = await s.get(Workspace, (await s.get(Task, ctx.task_id)).workspace_id)  # type: ignore[union-attr]
        with pytest.raises(ForbiddenError):
            await svc.approve(
                s,
                Account(user=member, workspace=workspace),  # type: ignore[arg-type]
                app.state.approval_signer,
                approval.id,
                seen_payload_hash=approval.payload_hash,
                seen_content_version=approval.content_version,
            )
    assert await _status(app, Approval, approval.id) == "PENDING"


# ---------- execution-side guarantees (used in step 5) ----------
async def test_approval_is_consumed_exactly_once(client: httpx.AsyncClient, app: FastAPI) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    await _approve(client, approval)
    async with app.state.db.sessionmaker() as s:
        a = await s.get(Approval, approval.id, populate_existing=True)
        await svc.verify_for_execution(s, app.state.approval_signer, a)  # type: ignore[arg-type]
        assert await svc.consume(s, a) is True  # type: ignore[arg-type]
        assert await svc.consume(s, a) is False  # type: ignore[arg-type]
        a = await s.get(Approval, approval.id, populate_existing=True)
        with pytest.raises(ApprovalInvalidError):
            await svc.verify_for_execution(s, app.state.approval_signer, a)  # type: ignore[arg-type]


async def test_approved_but_retargeted_approval_fails_at_execution(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    ctx = await _setup(client, app)
    approval = await _propose(app, ctx)
    await _approve(client, approval)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(Approval).where(Approval.id == approval.id).values(payload={**POST, "commentary": "swap"})
        )
        await s.commit()
        a = await s.get(Approval, approval.id, populate_existing=True)
        with pytest.raises(ApprovalInvalidError):
            await svc.verify_for_execution(s, app.state.approval_signer, a)  # type: ignore[arg-type]
        assert (await s.scalar(select(Approval.status).where(Approval.id == approval.id))) == "INVALIDATED"
