from fastapi import APIRouter, Request
from sqlalchemy import text

from cognitive_mesh.deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, session: SessionDep) -> dict[str, str]:
    """Public check that the backend and its database are up."""
    settings = request.app.state.settings
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "database": "ok",
    }
