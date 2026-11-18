import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request

from cognitive_mesh.auth import VerifiedIdentity, verify_clerk_session
from cognitive_mesh.config import PROJECT_ROOT, Settings
from cognitive_mesh.integrations.gateway import (
    READ_ONLY_TOOLS,
    ConnectedAccountInfo,
    ConnectionStart,
    IntegrationUnavailableError,
    ToolkitInfo,
    ToolNotAllowedError,
)
from cognitive_mesh.main import create_app
from cognitive_mesh.services.clerk_profiles import ClerkProfile, ProfileFetchError
from tests.fakes_ai import FakeTeam

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


class FakeGateway:
    """In-memory stand-in for Composio with the same contract as ComposioGateway."""

    def __init__(self) -> None:
        self.accounts: dict[str, ConnectedAccountInfo] = {}
        self.started: list[dict] = []
        self.deleted: list[str] = []
        self.read_calls: list[tuple[str, str]] = []
        self.profile_id: str | None = "abc"
        self.fail = False
        self._n = 0

    def _check(self) -> None:
        if self.fail:
            raise IntegrationUnavailableError("composio down")

    async def get_toolkits(self, slugs: list[str]) -> dict[str, ToolkitInfo]:
        self._check()
        return {
            s: ToolkitInfo(slug=s, name=s.title(), logo_url=f"https://logos/{s}", description=None)
            for s in slugs
        }

    async def start_connection(
        self, *, user_id: str, toolkit_slug: str, callback_url: str
    ) -> ConnectionStart:
        self._check()
        self._n += 1
        acc_id = f"ca_{self._n}"
        self.accounts[acc_id] = ConnectedAccountInfo(
            id=acc_id, user_id=user_id, toolkit_slug=toolkit_slug, status="INITIATED",
            display_name=None,
        )
        self.started.append({"user_id": user_id, "slug": toolkit_slug, "callback": callback_url})
        return ConnectionStart(acc_id, f"https://connect.composio.dev/link/ln_{self._n}")

    def finish_oauth(
        self,
        acc_id: str,
        status: str = "ACTIVE",
        name: str = "Jane Doe",
        scopes: tuple[str, ...] = ("email", "openid", "profile", "w_member_social"),
    ) -> None:
        """Simulate the user completing (or failing) the provider's consent screen."""
        self.accounts[acc_id] = dataclasses.replace(
            self.accounts[acc_id], status=status, display_name=name, granted_scopes=scopes
        )

    async def get_connected_account(self, connected_account_id: str) -> ConnectedAccountInfo:
        self._check()
        return self.accounts[connected_account_id]

    async def list_connected_accounts(
        self, *, user_id: str, toolkit_slugs: list[str]
    ) -> list[ConnectedAccountInfo]:
        self._check()
        return [
            a for a in self.accounts.values()
            if a.user_id == user_id and a.toolkit_slug in toolkit_slugs
        ]

    async def delete_connected_account(self, connected_account_id: str) -> None:
        self._check()
        self.deleted.append(connected_account_id)
        self.accounts.pop(connected_account_id, None)

    async def execute_read_only(self, *, user_id, toolkit_slug, tool_slug, arguments):  # type: ignore[no-untyped-def]
        self._check()
        if tool_slug not in READ_ONLY_TOOLS:
            raise ToolNotAllowedError(tool_slug)
        self.read_calls.append((user_id, tool_slug))
        if tool_slug == "LINKEDIN_GET_MY_INFO":
            return {"localizedFirstName": "Jane", "localizedLastName": "Doe",
                    "localizedHeadline": "AI engineer", "id": self.profile_id}
        return {}


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
        approval_signing_key="test-signing-key-" + "x" * 32,
        frontend_origins=["http://localhost:3000"],
        run_worker=False,  # tests drive the runner explicitly with run_once()
        checkpoint_db_path=(tmp_path / 'checkpoints.db').as_posix(),
        google_api_key="",
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
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def team() -> FakeTeam:
    return FakeTeam()


@pytest.fixture
async def app(
    migrated: Settings, profile_fetcher: FakeProfileFetcher, gateway: "FakeGateway", team: FakeTeam
) -> AsyncIterator[FastAPI]:
    app = create_app(
        migrated,
        profile_fetcher=profile_fetcher,
        integration_gateway=gateway,
        pipeline_factory=lambda _s, _g, _u: team.pipeline(),
    )
    app.dependency_overrides[verify_clerk_session] = fake_verify_clerk_session
    yield app
    await app.state.runner.close()
    await app.state.db.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
