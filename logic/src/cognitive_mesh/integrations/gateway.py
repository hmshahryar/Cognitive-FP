"""The single boundary between Cognitive Mesh and the Composio SDK.

- The Composio API key stays here; OAuth tokens never reach us (Composio holds them).
- The SDK is synchronous, so every call runs in a worker thread.
- Callers depend on the `IntegrationGateway` protocol, so tests use a fake.
"""

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from composio import Composio
from composio_client import APIStatusError

from cognitive_mesh.capabilities import read_tool_slugs, write_tool_slugs


class IntegrationUnavailableError(Exception):
    """Composio could not be reached or rejected the request."""


class ToolNotAllowedError(Exception):
    """A tool outside the read-only allowlist was requested."""


# Both allowlists come from the capability registry, the single source of truth.
# Tools AI employees may run directly.
READ_ONLY_TOOLS: frozenset[str] = read_tool_slugs()
# External writes. Only the verified publishing service calls these; no AI employee can.
APPROVED_ACTION_TOOLS: frozenset[str] = write_tool_slugs()


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


class ComposioGateway:
    TOOLKIT_CACHE_SECONDS = 3600

    def __init__(self, api_key: str) -> None:
        self._client = Composio(api_key=api_key) if api_key else None
        self._toolkit_cache: dict[str, ToolkitInfo] = {}
        self._toolkit_cache_at = 0.0

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
                user_id=user_id, toolkits=[toolkit_slug], manage_connections=False
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
                user_id=user_id, toolkits=[toolkit_slug], manage_connections=False
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
