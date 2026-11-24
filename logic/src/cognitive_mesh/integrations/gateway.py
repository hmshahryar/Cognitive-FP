"""The single boundary between Cognitive Mesh and the Composio SDK.

- The Composio API key stays here; OAuth tokens never reach us (Composio holds them).
- The SDK is synchronous, so every call runs in a worker thread.
- Callers depend on the `IntegrationGateway` protocol, so tests use a fake.
"""

import asyncio
import contextlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from composio import Composio
from composio_client import APIStatusError

from cognitive_mesh.capabilities import draft_tool_slugs, read_tool_slugs, write_tool_slugs
from cognitive_mesh.integrations.catalog import get_connector


class IntegrationUnavailableError(Exception):
    """Composio could not be reached or rejected the request."""


class ToolNotAllowedError(Exception):
    """A tool outside the read-only allowlist was requested."""


# Both allowlists come from the capability registry, the single source of truth.
# Tools AI employees may run directly.
READ_ONLY_TOOLS: frozenset[str] = read_tool_slugs()
# External writes. Only the verified publishing service calls these; no AI employee can.
APPROVED_ACTION_TOOLS: frozenset[str] = write_tool_slugs()
# Private drafts in the user's own account (a Canva design nobody else sees). No approval.
PRIVATE_DRAFT_TOOLS: frozenset[str] = draft_tool_slugs()


class DraftSession(Protocol):
    """One short Composio session for building a private draft. Runs in a worker thread."""

    def upload(self, content: bytes, *, filename: str, mimetype: str) -> str:
        """Store bytes for this session and return a temporary HTTPS download link."""
        ...

    def run(self, tool_slug: str, arguments: dict[str, Any]) -> Any:
        """Run an allowlisted read or private-draft tool and return its `data`."""
        ...


class ToolExecutionError(Exception):
    """The tool ran but reported failure."""


class ActionRejectedError(Exception):
    """The external action definitely did not happen."""


class ActionOutcomeUnknownError(Exception):
    """The request was sent but its result is unknown. It must never be resent automatically."""


_CLIENT_ERROR = re.compile(
    r"(?:status(?:\s+code)?|http|error|\"?code\"?)[\s:=]*(4\d\d)\b", re.IGNORECASE
)


def _is_definite_rejection(status_code: int) -> bool:
    return 400 <= status_code < 500 and status_code != 408


@dataclass(frozen=True)
class ToolkitInfo:
    slug: str
    name: str
    logo_url: str | None
    description: str | None


@dataclass(frozen=True)
class ConnectionStart:
    connected_account_id: str
    redirect_url: str


@dataclass(frozen=True)
class ConnectedAccountInfo:
    id: str
    user_id: str
    toolkit_slug: str
    status: str  # Composio status: INITIATED, ACTIVE, FAILED, EXPIRED, ...
    display_name: str | None
    # OAuth scopes the user actually granted on the provider's consent screen.
    granted_scopes: tuple[str, ...] = ()


class IntegrationGateway(Protocol):
    async def get_toolkits(self, slugs: list[str]) -> dict[str, ToolkitInfo]: ...

    async def start_connection(
        self, *, user_id: str, toolkit_slug: str, callback_url: str
    ) -> ConnectionStart: ...

    async def auth_config_scopes(self, toolkit_slug: str) -> tuple[str, ...] | None:
        """Every scope the app's auth config can request, or None if none is configured."""
        ...

    async def get_connected_account(self, connected_account_id: str) -> ConnectedAccountInfo: ...

    async def list_connected_accounts(
        self, *, user_id: str, toolkit_slugs: list[str]
    ) -> list[ConnectedAccountInfo]: ...

    async def delete_connected_account(self, connected_account_id: str) -> None: ...

    async def execute_read_only(
        self, *, user_id: str, toolkit_slug: str, tool_slug: str, arguments: dict[str, Any]
    ) -> Any: ...

    async def execute_approved_action(
        self,
        *,
        user_id: str,
        toolkit_slug: str,
        tool_slug: str,
        connected_account_id: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    async def run_private_draft(
        self, *, user_id: str, toolkit_slug: str, work: Any
    ) -> Any:
        """Run `work(session: DraftSession)` in one session and return its result."""
        ...


class _ComposioDraftSession:
    def __init__(self, session: Any) -> None:
        self._session = session

    def upload(self, content: bytes, *, filename: str, mimetype: str) -> str:
        # Documented session file mount (docs.composio.dev/docs/sandbox/remote). Composio marks
        # it experimental; files are cleaned up after 24 hours.
        remote = self._session.experimental.files.upload(
            content, remote_path=filename, mimetype=mimetype
        )
        if not str(remote.download_url).startswith("https://"):
            raise IntegrationUnavailableError("Composio returned no HTTPS file link")
        return str(remote.download_url)

    def run(self, tool_slug: str, arguments: dict[str, Any]) -> Any:
        if tool_slug not in PRIVATE_DRAFT_TOOLS | READ_ONLY_TOOLS:
            raise ToolNotAllowedError(tool_slug)
        result = self._session.execute(tool_slug, arguments=arguments)
        if result.result_type != "completed":
            raise ToolExecutionError(f"{tool_slug}: {result.error or result.result_type}")
        return result.model_dump(mode="json").get("data")


def _display_name(account: Any) -> str | None:
    # Documented: "Connected as" label lives at state.val.displayName when available.
    state = account.model_dump().get("state") or {}
    val = state.get("val") or {}
    return val.get("displayName") if isinstance(val, dict) else None


def _granted_scopes(account: Any) -> tuple[str, ...]:
    """Scopes from the provider's token response (state.val.scope), else those requested.

    Only the scope string is read; tokens in the state are never touched.
    """
    state = account.model_dump().get("state") or {}
    val = state.get("val") or {}
    raw = val.get("scope") if isinstance(val, dict) else None
    if isinstance(raw, str) and raw.strip():
        parts = raw.replace(",", " ").split()
    else:
        parts = list(getattr(account, "requested_scopes", None) or [])
    return tuple(sorted(set(parts)))


def _to_info(account: Any) -> ConnectedAccountInfo:
    return ConnectedAccountInfo(
        id=account.id,
        user_id=account.user_id,
        toolkit_slug=account.toolkit.slug,
        status=account.status,
        display_name=_display_name(account),
        granted_scopes=_granted_scopes(account),
    )


def _scope_set(value: Any) -> set[str]:
    parts = value if isinstance(value, list) else str(value or "").replace(",", " ").split()
    return {str(part).strip() for part in parts if str(part).strip()}


def _requested_scopes(config: dict[str, Any]) -> set[str]:
    """What the connection asks for. Only credentials' scope fields are read.

    A limited Composio-managed config can keep its default `optional_scopes` as metadata, but
    its OAuth request was verified to contain only `scopes`. A custom app sends both.
    """
    credentials = config.get("credentials") or {}
    scopes = _scope_set(credentials.get("scopes"))
    if not config.get("is_composio_managed"):
        scopes |= _scope_set(credentials.get("optional_scopes"))
    return scopes


class ComposioGateway:
    TOOLKIT_CACHE_SECONDS = 3600

    def __init__(self, api_key: str, *, auth_configs: dict[str, str] | None = None) -> None:
        self._client = Composio(api_key=api_key) if api_key else None
        self._auth_configs = dict(auth_configs or {})
        self._auth_config_lock = threading.Lock()
        self._toolkit_cache: dict[str, ToolkitInfo] = {}
        self._toolkit_cache_at = 0.0

    def _limited_config_id(self, toolkit_slug: str) -> str | None:
        """The app's limited config, created once in this Composio project when needed."""
        spec = get_connector(toolkit_slug)
        if spec is None or not spec.limited_config_name:
            return self._auth_configs.get(toolkit_slug)
        with self._auth_config_lock:
            if toolkit_slug in self._auth_configs:
                return self._auth_configs[toolkit_slug]
            wanted = set(spec.allowed_scopes)
            for row in self.client.client.auth_configs.list(toolkit_slug=toolkit_slug).items:
                data = row.model_dump()
                if (
                    data.get("name") == spec.limited_config_name
                    and data.get("status") == "ENABLED"
                    and data.get("is_composio_managed")
                    and _requested_scopes(data) == wanted
                ):
                    self._auth_configs[toolkit_slug] = data["id"]
                    return data["id"]
            created = self.client.auth_configs.create(
                toolkit=toolkit_slug,
                options={
                    "type": "use_composio_managed_auth",
                    "name": spec.limited_config_name,
                    "credentials": {"scopes": ",".join(spec.allowed_scopes)},
                },
            )
            self._auth_configs[toolkit_slug] = created.id
            return created.id

    def _session_auth(self, toolkit_slug: str) -> dict[str, Any]:
        """Use the limited auth config when this toolkit has one."""
        config_id = self._limited_config_id(toolkit_slug)
        return {"auth_configs": {toolkit_slug: config_id}} if config_id else {}

    async def auth_config_scopes(self, toolkit_slug: str) -> tuple[str, ...] | None:
        def _read() -> tuple[str, ...] | None:
            config_id = self._limited_config_id(toolkit_slug)
            if not config_id:
                return None
            data = self.client.client.auth_configs.retrieve(config_id).model_dump()
            if (data.get("toolkit") or {}).get("slug") != toolkit_slug:
                raise IntegrationUnavailableError("The auth config is for a different app")
            return tuple(sorted(_requested_scopes(data)))

        return await self._run(_read)

    @property
    def client(self) -> Composio:
        if self._client is None:
            raise IntegrationUnavailableError("COMPOSIO_API_KEY is not configured")
        return self._client

    async def _run(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except IntegrationUnavailableError:
            raise
        except Exception as exc:  # SDK/network errors
            raise IntegrationUnavailableError(str(exc)) from exc

    async def get_toolkits(self, slugs: list[str]) -> dict[str, ToolkitInfo]:
        """Catalog metadata (name, logo). Global, not user data, so it's cached."""
        fresh = time.monotonic() - self._toolkit_cache_at < self.TOOLKIT_CACHE_SECONDS
        if not (fresh and all(s in self._toolkit_cache for s in slugs)):
            toolkits = await self._run(self.client.toolkits.get_many, slugs)
            for tk in toolkits:
                meta = tk.meta
                self._toolkit_cache[tk.slug] = ToolkitInfo(
                    slug=tk.slug,
                    name=tk.name,
                    logo_url=getattr(meta, "logo", None),
                    description=getattr(meta, "description", None),
                )
            self._toolkit_cache_at = time.monotonic()
        return {s: self._toolkit_cache[s] for s in slugs if s in self._toolkit_cache}

    async def start_connection(
        self, *, user_id: str, toolkit_slug: str, callback_url: str
    ) -> ConnectionStart:
        def _start() -> ConnectionStart:
            # In-chat auth is off: users connect only through the Connector Hub.
            session = self.client.sessions.create(
                user_id=user_id, toolkits=[toolkit_slug], manage_connections=False,
                **self._session_auth(toolkit_slug),
            )
            request = session.authorize(toolkit_slug, callback_url=callback_url)
            if not request.id or not request.redirect_url:
                raise IntegrationUnavailableError("Composio returned no Connect Link")
            return ConnectionStart(
                connected_account_id=request.id, redirect_url=request.redirect_url
            )

        return await self._run(_start)

    async def get_connected_account(self, connected_account_id: str) -> ConnectedAccountInfo:
        account = await self._run(self.client.connected_accounts.get, connected_account_id)
        return _to_info(account)

    async def list_connected_accounts(
        self, *, user_id: str, toolkit_slugs: list[str]
    ) -> list[ConnectedAccountInfo]:
        response = await self._run(
            self.client.connected_accounts.list,
            user_ids=[user_id],
            toolkit_slugs=toolkit_slugs,
        )
        return [_to_info(a) for a in response.items]

    async def delete_connected_account(self, connected_account_id: str) -> None:
        await self._run(self.client.connected_accounts.delete, connected_account_id)

    async def execute_read_only(
        self, *, user_id: str, toolkit_slug: str, tool_slug: str, arguments: dict[str, Any]
    ) -> Any:
        """Run an allowlisted read-only tool as `user_id`; returns the tool's `data`."""
        if tool_slug not in READ_ONLY_TOOLS:
            raise ToolNotAllowedError(tool_slug)

        def _execute() -> Any:
            session = self.client.sessions.create(
                user_id=user_id, toolkits=[toolkit_slug], manage_connections=False,
                **self._session_auth(toolkit_slug),
            )
            try:
                result = session.execute(tool_slug, arguments=arguments)
            finally:
                session.delete()
            if result.result_type != "completed":
                raise ToolExecutionError(f"{tool_slug}: {result.error or result.result_type}")
            return result.model_dump(mode="json").get("data")

        try:
            return await asyncio.to_thread(_execute)
        except (ToolExecutionError, ToolNotAllowedError):
            raise
        except Exception as exc:
            raise IntegrationUnavailableError(str(exc)) from exc

    async def run_private_draft(self, *, user_id: str, toolkit_slug: str, work: Any) -> Any:
        """Build a private draft in one session as `user_id`. Only allowlisted tools can run."""

        def _execute() -> Any:
            session = self.client.sessions.create(
                user_id=user_id, toolkits=[toolkit_slug], manage_connections=False,
                **self._session_auth(toolkit_slug),
            )
            try:
                return work(_ComposioDraftSession(session))
            finally:
                with contextlib.suppress(Exception):
                    session.delete()

        try:
            return await asyncio.to_thread(_execute)
        except (ToolExecutionError, ToolNotAllowedError):
            raise
        except Exception as exc:
            raise IntegrationUnavailableError(str(exc)) from exc

    async def execute_approved_action(
        self,
        *,
        user_id: str,
        toolkit_slug: str,
        tool_slug: str,
        connected_account_id: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Run one approved write, locked to one account. Called once and never retried."""
        if tool_slug not in APPROVED_ACTION_TOOLS:
            raise ToolNotAllowedError(tool_slug)

        def _execute() -> Any:
            try:  # nothing has been sent yet, so any failure here is a definite "not posted"
                account = _to_info(self.client.connected_accounts.get(connected_account_id))
                if (
                    account.user_id != user_id
                    or account.toolkit_slug != toolkit_slug
                    or account.status != "ACTIVE"
                ):
                    raise ActionRejectedError("The approved account is no longer usable")
                session = self.client.sessions.create(
                    user_id=user_id,
                    toolkits=[toolkit_slug],
                    manage_connections=False,
                    connected_accounts={toolkit_slug: connected_account_id},
                    **self._session_auth(toolkit_slug),
                )
            except ActionRejectedError:
                raise
            except Exception as exc:
                raise ActionRejectedError(f"Could not prepare the request: {exc}") from exc

            try:
                # The SDK disables retries on session.execute for this reason.
                result = session.execute(tool_slug, arguments=arguments)
            except APIStatusError as exc:
                if _is_definite_rejection(exc.status_code):
                    raise ActionRejectedError(str(exc)) from exc
                raise ActionOutcomeUnknownError(str(exc)) from exc
            except Exception as exc:
                raise ActionOutcomeUnknownError(str(exc)) from exc
            finally:
                with contextlib.suppress(Exception):  # cleanup must not change the outcome
                    session.delete()

            if result.result_type == "completed":
                return result.model_dump(mode="json").get("data")
            detail = str(result.error or result.result_type)
            client_error = _CLIENT_ERROR.search(detail)
            if result.result_type == "blocked" or (
                client_error and _is_definite_rejection(int(client_error.group(1)))
            ):
                raise ActionRejectedError(detail)
            raise ActionOutcomeUnknownError(detail)

        return await asyncio.to_thread(_execute)
