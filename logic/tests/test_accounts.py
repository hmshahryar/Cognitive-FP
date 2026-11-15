import asyncio

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from cognitive_mesh.main import create_app
from cognitive_mesh.models import User, Workspace
from tests.conftest import FakeProfileFetcher, auth_headers


async def _count(app: FastAPI, model: type) -> int:
    async with app.state.db.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_first_request_registers_user_and_workspace(
    client: httpx.AsyncClient, app: FastAPI, profile_fetcher: FakeProfileFetcher
) -> None:
    res = await client.get("/api/me", headers=auth_headers("user_alice"))

    assert res.status_code == 200
    body = res.json()
    assert body["user"]["clerk_user_id"] == "user_alice"
    # Profile data comes from Clerk's API (fake), not from the request.
    assert body["user"]["email"] == "user_alice@example.com"
    assert body["user"]["first_name"] == "First-user_alice"
    assert body["workspace"]["name"] == "Personal workspace"
    assert profile_fetcher.calls == ["user_alice"]
    assert await _count(app, User) == 1
    assert await _count(app, Workspace) == 1


async def test_repeat_requests_are_idempotent(
    client: httpx.AsyncClient, app: FastAPI, profile_fetcher: FakeProfileFetcher
) -> None:
    responses = [await client.get("/api/me", headers=auth_headers("user_alice")) for _ in range(3)]

    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["user"]["id"] for r in responses}) == 1
    assert len({r.json()["workspace"]["id"] for r in responses}) == 1
    assert profile_fetcher.calls == ["user_alice"]  # Clerk API hit only once
    assert await _count(app, User) == 1
    assert await _count(app, Workspace) == 1


async def test_concurrent_first_requests_create_one_account(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    responses = await asyncio.gather(
        *(client.get("/api/me", headers=auth_headers("user_race")) for _ in range(10))
    )

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len({r.json()["workspace"]["id"] for r in responses}) == 1
    assert await _count(app, User) == 1
    assert await _count(app, Workspace) == 1


async def test_profile_fetch_failure_creates_nothing(
    client: httpx.AsyncClient, app: FastAPI, profile_fetcher: FakeProfileFetcher
) -> None:
    profile_fetcher.fail = True
    res = await client.get("/api/me", headers=auth_headers("user_alice"))

    assert res.status_code == 503
    assert await _count(app, User) == 0
    assert await _count(app, Workspace) == 0


async def test_unauthenticated_requests_are_rejected_by_real_verifier(migrated) -> None:  # type: ignore[no-untyped-def]
    # No dependency override: the real Clerk verifier runs.
    app = create_app(migrated, profile_fetcher=FakeProfileFetcher())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/me")
        forged = await client.get("/api/me", headers={"Authorization": "Bearer not-a-jwt"})
        workspace = await client.get("/api/workspace")

    assert missing.status_code == 401
    assert forged.status_code == 401
    assert workspace.status_code == 401
    assert await _count(app, User) == 0
    await app.state.db.dispose()
