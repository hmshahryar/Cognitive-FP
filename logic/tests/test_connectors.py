import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update

from cognitive_mesh.db.base import utcnow
from cognitive_mesh.models import ConnectionAttempt, IntegrationConnection
from tests.conftest import FakeGateway, auth_headers

ALICE = auth_headers("user_alice")
BOB = auth_headers("user_bob")


async def _connector(client: httpx.AsyncClient, headers: dict, slug: str) -> dict:
    res = await client.get("/api/connectors", headers=headers)
    assert res.status_code == 200
    return next(c for c in res.json()["connectors"] if c["slug"] == slug)


async def _start(client: httpx.AsyncClient, headers: dict, slug: str = "linkedin") -> str:
    """Start a connection; return the attempt id embedded in the callback URL."""
    res = await client.post(f"/api/connectors/{slug}/connect", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["redirect_url"].startswith("https://connect.composio.dev/")
    return res.json()["redirect_url"]


def _attempt_from(gateway: FakeGateway) -> tuple[str, str]:
    started = gateway.started[-1]
    attempt_id = parse_qs(urlparse(started["callback"]).query)["attempt"][0]
    account_id = next(a.id for a in reversed(list(gateway.accounts.values())))
    return attempt_id, account_id


async def _connect_fully(client, gateway, headers) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    await _start(client, headers)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id)
    res = await client.post(
        "/api/connectors/callback",
        headers=headers,
        json={"attempt_id": attempt_id, "connected_account_id": account_id},
    )
    assert res.status_code == 200, res.text
    assert res.json()["outcome"] == "connected"
    return attempt_id, account_id


# ---------- catalog ----------
async def test_lists_catalog_with_linkedin_available(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/connectors", headers=ALICE)
    body = res.json()
    slugs = {c["slug"] for c in body["connectors"]}
    assert {"linkedin", "gmail", "hubspot", "notion", "facebook", "instagram"} <= slugs
    linkedin = next(c for c in body["connectors"] if c["slug"] == "linkedin")
    assert linkedin["available"] is True and linkedin["connected"] is False
    assert linkedin["logo_url"] == "https://logos/linkedin"
    assert body["degraded"] is False


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/connectors")).status_code == 401
    assert (await client.post("/api/connectors/linkedin/connect")).status_code == 401


async def test_unknown_and_coming_soon_connectors_are_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/connectors/nope/connect", headers=ALICE)).status_code == 404
    assert (await client.post("/api/connectors/slack/connect", headers=ALICE)).status_code == 409
    assert (await client.post("/api/connectors/notion/connect", headers=ALICE)).status_code == 409
    assert (await client.post("/api/connectors/hubspot/connect", headers=ALICE)).status_code == 409


# ---------- connect flow ----------
async def test_connect_uses_verified_user_as_composio_user(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    me = (await client.get("/api/me", headers=ALICE)).json()
    await _start(client, ALICE)
    started = gateway.started[-1]
    assert started["user_id"] == me["user"]["id"]  # our DB user.id, not Clerk's id
    assert started["callback"].startswith("http://localhost:3000/connectors/callback?attempt=")


async def test_full_connect_flow_persists_connection(
    client: httpx.AsyncClient, gateway: FakeGateway, app: FastAPI
) -> None:
    _, account_id = await _connect_fully(client, gateway, ALICE)

    linkedin = await _connector(client, ALICE, "linkedin")
    assert linkedin["connected"] is True
    assert linkedin["status"] == "ACTIVE"
    assert linkedin["display_name"] == "Jane Doe"

    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(IntegrationConnection))
        assert row is not None and row.composio_account_id == account_id


async def test_connection_records_user_consent(
    client: httpx.AsyncClient, gateway: FakeGateway, app: FastAPI
) -> None:
    await _connect_fully(client, gateway, ALICE)
    linkedin = await _connector(client, ALICE, "linkedin")
    assert linkedin["granted_scopes"] == ["email", "openid", "profile", "w_member_social"]
    assert "Create, edit and delete posts, comments and reactions as you" in linkedin["permissions"]
    assert linkedin["consented_at"] is not None
    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(IntegrationConnection))
        assert row is not None and row.has_scopes("w_member_social")
        assert not row.has_scopes("w_organization_social")


async def test_listing_refreshes_scopes_when_consent_changes(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    _, account_id = await _connect_fully(client, gateway, ALICE)
    # User re-authorized with fewer permissions at the provider.
    gateway.finish_oauth(account_id, scopes=("openid", "profile"))
    linkedin = await _connector(client, ALICE, "linkedin")
    assert linkedin["granted_scopes"] == ["openid", "profile"]


async def test_callback_is_idempotent(client: httpx.AsyncClient, gateway: FakeGateway, app) -> None:  # type: ignore[no-untyped-def]
    attempt_id, account_id = await _connect_fully(client, gateway, ALICE)
    again = await client.post(
        "/api/connectors/callback",
        headers=ALICE,
        json={"attempt_id": attempt_id, "connected_account_id": account_id},
    )
    assert again.status_code == 200 and again.json()["outcome"] == "connected"
    async with app.state.db.sessionmaker() as s:
        assert len((await s.scalars(select(IntegrationConnection))).all()) == 1


async def test_callback_before_oauth_finishes_is_pending(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _start(client, ALICE)
    attempt_id, _ = _attempt_from(gateway)
    res = await client.post("/api/connectors/callback", headers=ALICE, json={"attempt_id": attempt_id})
    assert res.json()["outcome"] == "pending"
    assert (await _connector(client, ALICE, "linkedin"))["connected"] is False


async def test_failed_oauth_is_not_persisted(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    await _start(client, ALICE)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id, status="FAILED")
    res = await client.post("/api/connectors/callback", headers=ALICE, json={"attempt_id": attempt_id})
    assert res.json()["outcome"] == "failed"
    assert (await _connector(client, ALICE, "linkedin"))["status"] is None


# ---------- callback tampering & isolation ----------
async def test_other_user_cannot_complete_my_attempt(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _start(client, ALICE)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id)

    res = await client.post(
        "/api/connectors/callback",
        headers=BOB,
        json={"attempt_id": attempt_id, "connected_account_id": account_id},
    )
    assert res.status_code == 404
    assert (await _connector(client, BOB, "linkedin"))["connected"] is False


async def test_mismatched_account_id_in_callback_is_rejected(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _start(client, ALICE)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id)
    res = await client.post(
        "/api/connectors/callback",
        headers=ALICE,
        json={"attempt_id": attempt_id, "connected_account_id": "ca_someone_else"},
    )
    assert res.status_code == 400


async def test_account_owned_by_another_composio_user_is_rejected(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _start(client, ALICE)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id)
    # Composio reports the account belongs to someone else.
    import dataclasses

    gateway.accounts[account_id] = dataclasses.replace(
        gateway.accounts[account_id], user_id=str(uuid.uuid4())
    )
    res = await client.post("/api/connectors/callback", headers=ALICE, json={"attempt_id": attempt_id})
    assert res.status_code == 400
    assert (await _connector(client, ALICE, "linkedin"))["connected"] is False


async def test_expired_attempt_is_rejected(
    client: httpx.AsyncClient, gateway: FakeGateway, app: FastAPI
) -> None:
    await _start(client, ALICE)
    attempt_id, account_id = _attempt_from(gateway)
    gateway.finish_oauth(account_id)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(ConnectionAttempt).values(expires_at=utcnow() - timedelta(minutes=1))
        )
        await s.commit()
    res = await client.post("/api/connectors/callback", headers=ALICE, json={"attempt_id": attempt_id})
    assert res.status_code == 410


async def test_unknown_attempt_is_404(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/connectors/callback", headers=ALICE, json={"attempt_id": str(uuid.uuid4())}
    )
    assert res.status_code == 404


async def test_connections_are_isolated_per_workspace(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _connect_fully(client, gateway, ALICE)
    assert (await _connector(client, ALICE, "linkedin"))["connected"] is True
    assert (await _connector(client, BOB, "linkedin"))["connected"] is False
    # Bob disconnecting does nothing to Alice's connection.
    assert (await client.delete("/api/connectors/linkedin", headers=BOB)).status_code == 204
    assert gateway.deleted == []
    assert (await _connector(client, ALICE, "linkedin"))["connected"] is True


# ---------- lifecycle ----------
async def test_disconnect_removes_at_composio_and_is_idempotent(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    _, account_id = await _connect_fully(client, gateway, ALICE)
    assert (await client.delete("/api/connectors/linkedin", headers=ALICE)).status_code == 204
    assert gateway.deleted == [account_id]
    assert (await _connector(client, ALICE, "linkedin"))["status"] == "DISCONNECTED"
    assert (await client.delete("/api/connectors/linkedin", headers=ALICE)).status_code == 204
    assert gateway.deleted == [account_id]


async def test_reconnect_after_disconnect_replaces_account(
    client: httpx.AsyncClient, gateway: FakeGateway, app: FastAPI
) -> None:
    _, old_account = await _connect_fully(client, gateway, ALICE)
    await client.delete("/api/connectors/linkedin", headers=ALICE)
    _, new_account = await _connect_fully(client, gateway, ALICE)
    linkedin = await _connector(client, ALICE, "linkedin")
    assert linkedin["connected"] is True
    async with app.state.db.sessionmaker() as s:
        rows = (await s.scalars(select(IntegrationConnection))).all()
    assert len(rows) == 1  # same row, account swapped
    assert rows[0].composio_account_id == new_account != old_account


async def test_listing_picks_up_expired_connection(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    _, account_id = await _connect_fully(client, gateway, ALICE)
    gateway.finish_oauth(account_id, status="EXPIRED")
    linkedin = await _connector(client, ALICE, "linkedin")
    assert linkedin["connected"] is False and linkedin["status"] == "EXPIRED"


async def test_composio_outage_degrades_listing_and_blocks_connect(
    client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    await _connect_fully(client, gateway, ALICE)
    gateway.fail = True
    res = await client.get("/api/connectors", headers=ALICE)
    assert res.status_code == 200 and res.json()["degraded"] is True
    linkedin = next(c for c in res.json()["connectors"] if c["slug"] == "linkedin")
    assert linkedin["connected"] is True  # last known state still shown
    assert (await client.post("/api/connectors/linkedin/connect", headers=ALICE)).status_code == 503
