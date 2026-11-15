from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request

from cognitive_mesh.auth import VerifiedIdentity, verify_clerk_session
from cognitive_mesh.config import PROJECT_ROOT, Settings
from cognitive_mesh.main import create_app
from cognitive_mesh.services.clerk_profiles import ClerkProfile, ProfileFetchError

TEST_TOKEN_PREFIX = "test-token:"


def auth_headers(clerk_user_id: str) -> dict[str, str]:
    """Headers for a request 'signed in' as clerk_user_id (test verifier only)."""
    return {"Authorization": f"Bearer {TEST_TOKEN_PREFIX}{clerk_user_id}"}


class FakeProfileFetcher:
    """Stands in for Clerk's Backend API; records every lookup."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def __call__(self, clerk_user_id: str) -> ClerkProfile:
        self.calls.append(clerk_user_id)
        if self.fail:
            raise ProfileFetchError("Clerk unavailable")
        return ClerkProfile(
            clerk_user_id=clerk_user_id,
            email=f"{clerk_user_id}@example.com",
            first_name=f"First-{clerk_user_id}",
            last_name="Tester",
            image_url=None,
        )


async def fake_verify_clerk_session(request: Request) -> VerifiedIdentity:
    """Replaces only the Clerk signature check: the token names the user."""
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ")
    if not token.startswith(TEST_TOKEN_PREFIX):
        raise HTTPException(401, "Not signed in")
    return VerifiedIdentity(clerk_user_id=token.removeprefix(TEST_TOKEN_PREFIX), session_id="sess")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        clerk_secret_key="sk_test_dummy",
        frontend_origins=["http://localhost:3000"],
    )


@pytest.fixture
def migrated(settings: Settings) -> Settings:
    """Create the schema with the real Alembic migrations."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = settings.database_url
    command.upgrade(cfg, "head")
    return settings


@pytest.fixture
def profile_fetcher() -> FakeProfileFetcher:
    return FakeProfileFetcher()


@pytest.fixture
async def app(migrated: Settings, profile_fetcher: FakeProfileFetcher) -> AsyncIterator[FastAPI]:
    app = create_app(migrated, profile_fetcher=profile_fetcher)
    app.dependency_overrides[verify_clerk_session] = fake_verify_clerk_session
    yield app
    await app.state.db.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
