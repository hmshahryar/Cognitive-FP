from fastapi import APIRouter

from cognitive_mesh.deps import CurrentAccountDep, OwnedWorkspaceDep
from cognitive_mesh.schemas import WorkspaceOut

router = APIRouter(tags=["workspaces"])


@router.get("/workspace", response_model=WorkspaceOut)
async def current_workspace(account: CurrentAccountDep) -> WorkspaceOut:
    """The caller's own workspace. No ID needed: it's derived from the token."""
    return WorkspaceOut.model_validate(account.workspace)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace: OwnedWorkspaceDep) -> WorkspaceOut:
    """A workspace by ID, only if the caller owns it (404 otherwise).

    Future workspace-scoped routes (chats, tasks, ...) reuse OwnedWorkspaceDep.
    """
    return WorkspaceOut.model_validate(workspace)
