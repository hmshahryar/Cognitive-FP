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
    APPROVED_ACTION_TOOLS,
    PRIVATE_DRAFT_TOOLS,
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
        self.publish_calls: list[dict] = []
        self.publish_error: BaseException | None = None
        self.publish_response: dict | None = None
        self.gmail_messages: list[dict] = []
        self.gmail_queries: list[str] = []
        self.calendar_events: list[dict] = []
        self.calendar_queries: list[dict] = []
        self.calendar_has_more = False
        self.sheets: list[dict] = []
        self.sheet_tabs: list[str] = ["Sheet1"]
        self.sheet_values: list[list[str]] = []
        self.sheet_reads: list[str] = []
        self.hubspot_contacts: list[dict] = []
        self.hubspot_searches: list[dict] = []
        self.salesforce_contacts: list[dict] = []
        self.salesforce_accounts: list[dict] = []
        self.salesforce_searches: list[dict] = []
        self.auth_scopes: dict[str, tuple[str, ...]] = {
            "hubspot": ("crm.objects.contacts.read", "crm.objects.contacts.write", "oauth"),
            "salesforce": ("api", "refresh_token"),
            "facebook": ("pages_manage_posts", "pages_read_engagement", "pages_show_list"),
            "instagram": ("instagram_business_basic", "instagram_business_content_publish"),
            "canva": (
                "asset:read", "asset:write", "brandtemplate:content:read",
                "brandtemplate:meta:read", "design:content:read", "design:content:write",
                "design:meta:read", "profile:read",
            ),
            "zoho": (
                "ZohoCRM.modules.contacts.CREATE", "ZohoCRM.modules.contacts.READ",
                "ZohoCRM.modules.contacts.UPDATE", "ZohoCRM.modules.notes.CREATE",
                "ZohoSearch.securesearch.READ",
            ),
        }
        self.zoho_contacts: list[dict] = []
        self.zoho_searches: list[dict] = []
        self.notion_pages: list[dict] = []
        self.notion_markdown = ""
        self.facebook_pages: list[dict] = []
        self.instagram_account: dict = {
            "user_id": "17841400000000001", "username": "cognitive.mesh", "account_type": "BUSINESS",
        }
        self.instagram_quota: dict = {"data": [{"quota_usage": 2, "config": {"quota_total": 100}}]}
        self.publish_responses: list[dict] = []  # consumed in order before publish_response
        self.draft_uploads: list[tuple] = []
        self.draft_calls: list[tuple[str, dict]] = []
        self.canva_responses: dict[str, dict] = {
            "CANVA_CREATE_URL_ASSET_UPLOAD_JOB": {"job": {"id": "up1", "status": "in_progress"}},
            "CANVA_GET_URL_ASSET_UPLOADS_JOBID": {
                "job": {"id": "up1", "status": "success", "asset": {"id": "Msd123"}}
            },
            "CANVA_POST_DESIGNS": {
                "design": {"id": "DAF123", "urls": {"edit_url": "https://www.canva.com/design/DAF123/edit"}}
            },
            "CANVA_POST_EXPORTS": {"job": {"id": "ex1", "status": "in_progress"}},
            "CANVA_GET_DESIGN_EXPORT_JOB_RESULT": {
                "job": {"id": "ex1", "status": "success",
                        "urls": ["https://export-download.canva.com/banner.jpg"]}
            },
        }
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

    async def auth_config_scopes(self, toolkit_slug: str) -> tuple[str, ...] | None:
        self._check()
        return self.auth_scopes.get(toolkit_slug)

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
        if tool_slug == "GMAIL_GET_PROFILE":
            return {"emailAddress": "jane@example.com"}
        if tool_slug == "GMAIL_FETCH_EMAILS":
            self.gmail_queries.append(arguments["query"])
            return {"messages": self.gmail_messages[: arguments["max_results"]]}
        if tool_slug == "GOOGLECALENDAR_EVENTS_LIST":
            if "fields" in arguments:  # the timezone lookup
                return {"summary": "jane@example.com", "timeZone": "Asia/Karachi"}
            self.calendar_queries.append(dict(arguments))
            result = {"items": list(self.calendar_events), "timeZone": "Asia/Karachi"}
            return {**result, "nextPageToken": "more"} if self.calendar_has_more else result
        if tool_slug == "GOOGLESHEETS_SEARCH_SPREADSHEETS":
            return {"spreadsheets": list(self.sheets), "total_found": len(self.sheets)}
        if tool_slug == "GOOGLESHEETS_GET_SHEET_NAMES":
            return {"sheet_names": list(self.sheet_tabs)}
        if tool_slug == "GOOGLESHEETS_VALUES_GET":
            self.sheet_reads.append(arguments["range"])
            return {"range": arguments["range"], "values": self.sheet_values}
        if tool_slug == "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA":
            self.hubspot_searches.append(dict(arguments))
            return {"results": list(self.hubspot_contacts)}
        if tool_slug == "SALESFORCE_SEARCH_CONTACTS":
            self.salesforce_searches.append(dict(arguments))
            rows = list(self.salesforce_contacts)
            return {"totalSize": len(rows), "done": True, "records": rows}
        if tool_slug == "ZOHO_SEARCH_CONTACTS":
            self.zoho_searches.append(dict(arguments))
            rows = list(self.zoho_contacts)
            return {"data": rows, "info": {"count": len(rows), "more_records": False}}
        if tool_slug == "SALESFORCE_SEARCH_ACCOUNTS":
            rows = list(self.salesforce_accounts)
            return {"totalSize": len(rows), "done": True, "records": rows}
        if tool_slug == "NOTION_SEARCH_NOTION_PAGE":
            return {"results": list(self.notion_pages), "has_more": False, "next_cursor": None}
        if tool_slug == "NOTION_RETRIEVE_PAGE":
            return next(p for p in self.notion_pages if p["id"] == arguments["page_id"])
        if tool_slug == "NOTION_GET_PAGE_MARKDOWN":
            return {"object": "page_markdown", "id": arguments["page_id"],
                    "markdown": self.notion_markdown, "truncated": False, "unknown_block_ids": []}
        if tool_slug == "FACEBOOK_LIST_MANAGED_PAGES":
            return {"data": list(self.facebook_pages), "paging": {"cursors": {}}}
        if tool_slug == "INSTAGRAM_GET_USER_INFO":
            return dict(self.instagram_account)
        if tool_slug == "INSTAGRAM_GET_IG_USER_CONTENT_PUBLISHING_LIMIT":
            return self.instagram_quota
        return {}

    async def run_private_draft(self, *, user_id, toolkit_slug, work):  # type: ignore[no-untyped-def]
        self._check()
        gateway = self

        class Session:
            def upload(self, content: bytes, *, filename: str, mimetype: str) -> str:
                gateway.draft_uploads.append((user_id, toolkit_slug, len(content), mimetype))
                return f"https://files.composio.example/{filename}"

            def run(self, tool_slug: str, arguments: dict) -> dict:
                if tool_slug not in PRIVATE_DRAFT_TOOLS | READ_ONLY_TOOLS:
                    raise ToolNotAllowedError(tool_slug)
                gateway.draft_calls.append((tool_slug, dict(arguments)))
                return gateway.canva_responses[tool_slug]

        return work(Session())

    async def execute_approved_action(  # type: ignore[no-untyped-def]
        self, *, user_id, toolkit_slug, tool_slug, connected_account_id, arguments
    ):
        if tool_slug not in APPROVED_ACTION_TOOLS:
            raise ToolNotAllowedError(tool_slug)
        self.publish_calls.append(
            {"user_id": user_id, "tool": tool_slug, "account": connected_account_id,
             "arguments": dict(arguments)}
        )
        if self.publish_responses:
            step = self.publish_responses.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step
        if self.publish_error is not None:
            raise self.publish_error
        return self.publish_response or {"x_restli_id": f"urn:li:share:{7000 + len(self.publish_calls)}"}


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
        memory_db_path=(tmp_path / 'memory.db').as_posix(),
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
