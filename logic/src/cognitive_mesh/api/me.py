from fastapi import APIRouter

from cognitive_mesh.deps import CurrentAccountDep
from cognitive_mesh.schemas import MeOut, UserOut, WorkspaceOut

router = APIRouter(tags=["account"])


@router.get("/me", response_model=MeOut)
async def me(account: CurrentAccountDep) -> MeOut:
    """The signed-in user and their personal workspace (created on first call)."""
    return MeOut(
        user=UserOut.model_validate(account.user),
        workspace=WorkspaceOut.model_validate(account.workspace),
    )
