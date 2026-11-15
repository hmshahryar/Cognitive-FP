"""Idempotent sync of a verified Clerk identity into a User + personal Workspace."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.auth import VerifiedIdentity
from cognitive_mesh.models import User, Workspace
from cognitive_mesh.services.clerk_profiles import ProfileFetcher


@dataclass(frozen=True)
class Account:
    user: User
    workspace: Workspace


def _insert(session: AsyncSession, model: type):  # type: ignore[no-untyped-def]
    """Dialect-specific INSERT that supports ON CONFLICT DO NOTHING."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        return sqlite.insert(model)
    if dialect == "postgresql":
        return postgresql.insert(model)
    raise NotImplementedError(f"Unsupported database dialect: {dialect}")


async def _get_user(session: AsyncSession, clerk_user_id: str) -> User | None:
    return await session.scalar(select(User).where(User.clerk_user_id == clerk_user_id))


async def _get_workspace(session: AsyncSession, user: User) -> Workspace | None:
    return await session.scalar(select(Workspace).where(Workspace.owner_id == user.id))


async def get_or_create_account(
    session: AsyncSession,
    identity: VerifiedIdentity,
    fetch_profile: ProfileFetcher,
) -> Account:
    """Return the caller's User and Workspace, creating them on first sight.

    Safe to call on every request and under concurrent first requests: rows are
    inserted with ON CONFLICT DO NOTHING against unique keys, then re-read.
    """
    user = await _get_user(session, identity.clerk_user_id)
    created = False

    if user is None:
        # Profile comes from Clerk's server API, never from the request body.
        profile = await fetch_profile(identity.clerk_user_id)
        await session.execute(
            _insert(session, User)
            .values(
                clerk_user_id=identity.clerk_user_id,
                email=profile.email,
                first_name=profile.first_name,
                last_name=profile.last_name,
                image_url=profile.image_url,
            )
            .on_conflict_do_nothing(index_elements=[User.clerk_user_id])
        )
        user = await _get_user(session, identity.clerk_user_id)
        assert user is not None
        created = True

    workspace = await _get_workspace(session, user)
    if workspace is None:
        await session.execute(
            _insert(session, Workspace)
            .values(owner_id=user.id)
            .on_conflict_do_nothing(index_elements=[Workspace.owner_id])
        )
        workspace = await _get_workspace(session, user)
        assert workspace is not None
        created = True

    if created:
        await session.commit()
    return Account(user=user, workspace=workspace)
