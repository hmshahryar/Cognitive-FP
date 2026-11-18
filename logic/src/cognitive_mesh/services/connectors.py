"""Connector Hub: start, verify, list and remove app connections for a workspace.

Security rules enforced here:
- Workspace and user come only from the verified session (`Account`), never input.
- The Composio user_id is always str(user.id) of the signed-in user.
- An OAuth callback is not trusted: the attempt must belong to the caller, and the
  connected account is re-read from Composio and checked (owner, app, status).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.db.base import utcnow, uuid7
from cognitive_mesh.db.dialect import upsert_insert
from cognitive_mesh.integrations.catalog import CONNECTORS, ConnectorSpec, get_connector
from cognitive_mesh.integrations.gateway import (
    ConnectedAccountInfo,
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolkitInfo,
)
from cognitive_mesh.models import (
    AttemptStatus,
    ConnectionAttempt,
    ConnectionStatus,
    IntegrationConnection,
)
from cognitive_mesh.services.accounts import Account
from cognitive_mesh.services.approvals import invalidate_for_connection_change

logger = logging.getLogger(__name__)


# ---------- errors (mapped to HTTP codes in the API layer) ----------
class ConnectorError(Exception):
    pass


class ConnectorNotFoundError(ConnectorError):
    pass


class ConnectorUnavailableError(ConnectorError):
    """The app is listed but not enabled yet ("coming soon")."""


class AttemptNotFoundError(ConnectorError):
    pass


class AttemptExpiredError(ConnectorError):
    pass


class CallbackVerificationError(ConnectorError):
    """The callback doesn't match what we started, or Composio disagrees."""


# ---------- read models ----------
@dataclass(frozen=True)
class ConnectorView:
    slug: str
    name: str
    logo_url: str | None
    description: str | None
    category: str
    available: bool
    status: ConnectionStatus | None  # None = never connected
    display_name: str | None
    connected_at: datetime | None
    granted_scopes: tuple[str, ...]
    consented_at: datetime | None

    @property
    def connected(self) -> bool:
        return self.status == ConnectionStatus.ACTIVE


@dataclass(frozen=True)
class CallbackResult:
    outcome: str  # "connected" | "pending" | "failed"
    connector: ConnectorView


# ---------- helpers ----------
def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; treat stored naive datetimes as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _require_spec(slug: str) -> ConnectorSpec:
    spec = get_connector(slug)
    if spec is None:
        raise ConnectorNotFoundError(slug)
    return spec


def _view(
    spec: ConnectorSpec, info: ToolkitInfo | None, conn: IntegrationConnection | None
) -> ConnectorView:
    return ConnectorView(
        slug=spec.slug,
        name=info.name if info else spec.slug.title(),
        logo_url=info.logo_url if info else None,
        description=info.description if info else None,
        category=spec.category,
        available=spec.available,
        status=ConnectionStatus(conn.status) if conn else None,
        display_name=conn.display_name if conn else None,
        connected_at=conn.connected_at if conn else None,
        granted_scopes=tuple(conn.granted_scopes or ()) if conn else (),
        consented_at=conn.consented_at if conn else None,
    )


async def _connections_by_slug(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[str, IntegrationConnection]:
    rows = await session.scalars(
        select(IntegrationConnection)
        .where(IntegrationConnection.workspace_id == workspace_id)
        # Rows may have been changed by a Core upsert in this session; reload them.
        .execution_options(populate_existing=True)
    )
    return {c.toolkit_slug: c for c in rows}


async def _reconcile(
    session: AsyncSession,
    account: Account,
    gateway: IntegrationGateway,
    connections: dict[str, IntegrationConnection],
) -> None:
    """Pull live statuses from Composio (e.g. ACTIVE -> EXPIRED after a revoked token)."""
    tracked = [c for c in connections.values() if c.status != ConnectionStatus.DISCONNECTED]
    if not tracked:
        return
    live = await gateway.list_connected_accounts(
        user_id=str(account.user.id), toolkit_slugs=[c.toolkit_slug for c in tracked]
    )
    by_id = {a.id: a for a in live}
    changed = False
    for conn in tracked:
        remote = by_id.get(conn.composio_account_id)
        new_status = ConnectionStatus(remote.status) if remote else ConnectionStatus.DISCONNECTED
        if new_status != conn.status:
            conn.status = new_status
            changed = True
        if remote and remote.granted_scopes and list(remote.granted_scopes) != conn.granted_scopes:
            conn.granted_scopes = list(remote.granted_scopes)
            conn.consented_at = conn.consented_at or utcnow()
            changed = True
    if changed:
        await session.commit()
        for conn in tracked:
            if conn.status != ConnectionStatus.ACTIVE:
                await invalidate_for_connection_change(
                    session, conn, f"Account is {conn.status.lower()}"
                )


# ---------- use cases ----------
async def list_connectors(
    session: AsyncSession, account: Account, gateway: IntegrationGateway
) -> tuple[list[ConnectorView], bool]:
    """All Hub connectors with this workspace's status. Returns (views, degraded)."""
    connections = await _connections_by_slug(session, account.workspace.id)
    degraded = False
    toolkits: dict[str, ToolkitInfo] = {}
    try:
        toolkits = await gateway.get_toolkits([c.slug for c in CONNECTORS])
        await _reconcile(session, account, gateway, connections)
    except IntegrationUnavailableError:
        logger.warning("Composio unavailable; showing stored connector state", exc_info=True)
        degraded = True
    views = [_view(spec, toolkits.get(spec.slug), connections.get(spec.slug)) for spec in CONNECTORS]
    return views, degraded


async def start_connection(
    session: AsyncSession,
    account: Account,
    gateway: IntegrationGateway,
    *,
    slug: str,
    frontend_url: str,
    ttl_minutes: int,
) -> str:
    """Create an attempt and return Composio's Connect Link (redirect URL)."""
    spec = _require_spec(slug)
    if not spec.available:
        raise ConnectorUnavailableError(slug)

    attempt = ConnectionAttempt(
        id=uuid7(),
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        toolkit_slug=slug,
        status=AttemptStatus.PENDING,
        expires_at=utcnow() + timedelta(minutes=ttl_minutes),
    )
    callback_url = f"{frontend_url}/connectors/callback?{urlencode({'attempt': attempt.id})}"
    started = await gateway.start_connection(
        user_id=str(account.user.id), toolkit_slug=slug, callback_url=callback_url
    )
    attempt.composio_account_id = started.connected_account_id
    session.add(attempt)
    await session.commit()
    return started.redirect_url


async def _upsert_connection(
    session: AsyncSession, account: Account, remote: ConnectedAccountInfo
) -> None:
    now = utcnow()
    values = {
        "workspace_id": account.workspace.id,
        "user_id": account.user.id,
        "toolkit_slug": remote.toolkit_slug,
        "composio_account_id": remote.id,
        "status": ConnectionStatus.ACTIVE,
        "display_name": remote.display_name,
        "connected_at": now,
        "granted_scopes": list(remote.granted_scopes),
        "consented_at": now,
    }
    stmt = upsert_insert(session, IntegrationConnection).values(
        id=uuid7(), created_at=now, updated_at=now, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[IntegrationConnection.workspace_id, IntegrationConnection.toolkit_slug],
        set_={**values, "updated_at": now},
    )
    await session.execute(stmt)


async def complete_connection(
    session: AsyncSession,
    account: Account,
    gateway: IntegrationGateway,
    *,
    attempt_id: uuid.UUID,
    reported_account_id: str | None,
) -> CallbackResult:
    """Verify an OAuth callback and persist the connection. Idempotent."""
    attempt = await session.scalar(
        select(ConnectionAttempt).where(
            ConnectionAttempt.id == attempt_id,
            # Ownership: only the user who started it can complete it.
            ConnectionAttempt.user_id == account.user.id,
            ConnectionAttempt.workspace_id == account.workspace.id,
        )
    )
    if attempt is None or attempt.composio_account_id is None:
        raise AttemptNotFoundError(str(attempt_id))

    spec = _require_spec(attempt.toolkit_slug)

    async def result(outcome: str) -> CallbackResult:
        conns = await _connections_by_slug(session, account.workspace.id)
        info = (await gateway.get_toolkits([spec.slug])).get(spec.slug)
        return CallbackResult(outcome, _view(spec, info, conns.get(spec.slug)))

    if attempt.status == AttemptStatus.COMPLETED:
        return await result("connected")
    if attempt.status == AttemptStatus.FAILED:
        return await result("failed")
    if _aware(attempt.expires_at) < utcnow():
        raise AttemptExpiredError(str(attempt_id))

    # Query parameters are not proof: they must match what we recorded.
    if reported_account_id and reported_account_id != attempt.composio_account_id:
        raise CallbackVerificationError("Connected account does not match this attempt")

    # Ask Composio directly, using OUR stored account id.
    remote = await gateway.get_connected_account(attempt.composio_account_id)
    if remote.user_id != str(account.user.id) or remote.toolkit_slug != attempt.toolkit_slug:
        attempt.status = AttemptStatus.FAILED
        await session.commit()
        raise CallbackVerificationError("Connected account belongs to a different user or app")

    status = ConnectionStatus(remote.status)
    if status in (ConnectionStatus.INITIATED, ConnectionStatus.INITIALIZING):
        return await result("pending")
    if status != ConnectionStatus.ACTIVE:
        attempt.status = AttemptStatus.FAILED
        await session.commit()
        return await result("failed")

    await _upsert_connection(session, account, remote)
    attempt.status = AttemptStatus.COMPLETED
    attempt.completed_at = utcnow()
    await session.commit()
    # A reconnect may point at a different account: old approvals must not carry over.
    conns = await _connections_by_slug(session, account.workspace.id)
    await invalidate_for_connection_change(
        session, conns[spec.slug], "Target account was reconnected"
    )
    return await result("connected")


async def disconnect(
    session: AsyncSession, account: Account, gateway: IntegrationGateway, *, slug: str
) -> None:
    _require_spec(slug)
    conn = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == account.workspace.id,
            IntegrationConnection.toolkit_slug == slug,
        )
    )
    if conn is None or conn.status == ConnectionStatus.DISCONNECTED:
        return  # already disconnected: idempotent
    await gateway.delete_connected_account(conn.composio_account_id)
    conn.status = ConnectionStatus.DISCONNECTED
    await session.commit()
    await invalidate_for_connection_change(session, conn, "Account disconnected")


async def get_active_connection(
    session: AsyncSession, workspace_id: uuid.UUID, slug: str
) -> IntegrationConnection | None:
    """For AI employees (later steps): the workspace's ACTIVE connection, if any."""
    return await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.toolkit_slug == slug,
            IntegrationConnection.status == ConnectionStatus.ACTIVE,
        )
    )
