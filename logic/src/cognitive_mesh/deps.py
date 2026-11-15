"""Request dependencies that resolve the caller's account and enforce ownership."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cognitive_mesh.auth import VerifiedIdentityDep
from cognitive_mesh.db import get_session
from cognitive_mesh.models import Workspace
from cognitive_mesh.services.accounts import Account, get_or_create_account
from cognitive_mesh.services.clerk_profiles import ProfileFetcher, ProfileFetchError

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_profile_fetcher(request: Request) -> ProfileFetcher:
    return request.app.state.profile_fetcher


async def get_current_account(
    identity: VerifiedIdentityDep,
    session: SessionDep,
    fetch_profile: Annotated[ProfileFetcher, Depends(get_profile_fetcher)],
) -> Account:
    try:
        return await get_or_create_account(session, identity, fetch_profile)
    except ProfileFetchError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Could not load your profile from Clerk"
        ) from exc


CurrentAccountDep = Annotated[Account, Depends(get_current_account)]


async def get_owned_workspace(workspace_id: uuid.UUID, account: CurrentAccountDep) -> Workspace:
    """Resolve a workspace from the URL only if the caller owns it.

    Returns 404 (not 403) for other users' workspaces so IDs can't be probed.
    """
    if workspace_id != account.workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return account.workspace


OwnedWorkspaceDep = Annotated[Workspace, Depends(get_owned_workspace)]
